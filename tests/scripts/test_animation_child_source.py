"""Offline validation for data-only animated dependent child transforms."""
import copy
import json
import math
import re
import unittest
from pathlib import Path

from animation_child_source import (AnimationChildSourceError, ChildPoseHistory,
                                    build_kinematic_child_records)
from animation_pose_sampler import AnimationPoseSampler
from animation_rig_pose import evaluate_own_avatar
from physics_root_record import ClientChildBody
from world_snapshot import replay_world


ROOT = Path(__file__).resolve().parents[2]
CAPTURE_REPORT = ROOT / 'outputs/gamejoin/owned-animation-slot-probe3.json'
LAST_JOIN = ROOT / 'secrets/last-join.json'
RUN_CLIP = Path.home() / '.cache/roblox-client-research/animation-poses/run.json'


def _load_private_context():
    values = json.loads(LAST_JOIN.read_text())
    matches = re.findall(r"[\\\"']?UserId[\\\"']?\s*[:=]\s*[\\\"']?(\d+)",
                         json.dumps(values, ensure_ascii=True))
    for candidate in reversed(list(dict.fromkeys(matches))):
        try:
            user_id = int(candidate)
            world = replay_world(CAPTURE_REPORT, user_id)
            if world.avatar(user_id)['evidence']['player_spawned']:
                _trim_replay_world(world, user_id)
                return world, user_id
        except (ValueError, KeyError, TypeError):
            pass
    raise ValueError('local own-avatar capture does not match its private context')


def _trim_replay_world(world, user_id):
    """Keep only services and the selected own avatar in this local replay."""
    avatar = world.avatar(user_id)
    roots = {avatar['player_key'], avatar['character_key'], *world.top_containers}
    children = {}
    for key, node in world.instances.items():
        children.setdefault(node.get('parent'), []).append(key)
    keep = set(roots)
    pending = [avatar['character_key']]
    while pending:
        key = pending.pop()
        for child in children.get(key, ()):
            if child not in keep:
                keep.add(child)
                pending.append(child)
    for root in tuple(roots):
        node = world.instances.get(root)
        seen = set()
        while node is not None and node.get('parent') is not None:
            parent = node['parent']
            if parent in seen:
                raise ValueError('captured ancestry contains a cycle')
            seen.add(parent)
            keep.add(parent)
            node = world.instances.get(parent)
    world.instances = {key: node for key, node in world.instances.items() if key in keep}
    world.creation = {key: value for key, value in world.creation.items() if key in keep}
    world.property_versions = {key: value for key, value in world.property_versions.items() if key in keep}


def _world_yaw(position, yaw):
    c, s = math.cos(yaw), math.sin(yaw)
    return {'position': list(position),
            'rotation': [c, 0., s, 0., 1., 0., -s, 0., c]}


class AnimationChildSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not CAPTURE_REPORT.is_file() or not LAST_JOIN.is_file() or not RUN_CLIP.is_file():
            raise unittest.SkipTest('offline private capture and cached public run clip are unavailable')
        try:
            cls.source_world, cls.user_id = _load_private_context()
            cls.clip = json.loads(RUN_CLIP.read_text())
            cls.sampler = AnimationPoseSampler(cls.clip)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise unittest.SkipTest('offline animation fixture is unavailable') from error

    def setUp(self):
        self.world = copy.deepcopy(self.source_world)
        self.avatar = self.world.avatar(self.user_id)
        self.root_key = self.avatar['root_key']
        self.received_before = copy.deepcopy(self.world.instances)
        self.revision_before = self.world.revision

    def _posed(self, time, root=None):
        root = root or self.avatar['root']['properties']['CFrame']
        sampled = self.sampler.sample(time)
        self.assertEqual(sampled['status'], 'ok')
        evaluated = evaluate_own_avatar(self.world, self.user_id, root, sampled)
        self.assertEqual(evaluated['status'], 'ok', evaluated)
        self.assertEqual(evaluated['resolved_bodyparts'], 15)
        return evaluated['poses']

    def _build(self, posed, history, now, **kwargs):
        return build_kinematic_child_records(
            self.world, self.user_id, self.root_key, posed,
            context_tag=kwargs.pop('context_tag', 0x1234),
            origin_index=kwargs.pop('origin_index', 255),
            velocity_mode=kwargs.pop('velocity_mode', 'compact'),
            body_frame=kwargs.pop('body_frame', 'part'),
            now=now, history=history, **kwargs)

    def _parts(self):
        char = self.avatar['character_key']
        result = set()
        for key, node in self.world.instances.items():
            parent = node.get('parent')
            seen = set()
            while parent is not None and parent not in seen:
                if parent == char:
                    if self.world.schema['classes'][node['class']]['name'] in ('Part', 'MeshPart'):
                        result.add(key)
                    break
                seen.add(parent)
                node = self.world.instances.get(parent)
                if node is None:
                    break
                parent = node.get('parent')
        return result

    def _constraints(self):
        char = self.avatar['character_key']
        for key, original in self.world.instances.items():
            if self.world.schema['classes'][original['class']]['name'] != 'AnimationConstraint':
                continue
            parent = original.get('parent')
            seen = set()
            while parent is not None and parent not in seen:
                if parent == char:
                    yield key, original
                    break
                seen.add(parent)
                ancestor = self.world.instances.get(parent)
                if ancestor is None:
                    break
                parent = ancestor.get('parent')

    def test_actual_capture_graph_maps_under_asymmetric_root_yaw_without_world_mutation(self):
        root = _world_yaw((9., 5., -3.), .71)
        posed = self._posed(.17, root)
        history = ChildPoseHistory()
        records, report = self._build(posed, history, 10.)
        self.assertEqual(report['status'], 'ok')
        self.assertEqual(report['body_count'], 16)
        self.assertEqual(report['child_body_count'], 15)
        self.assertEqual(report['constraint_edge_count'], 15)
        self.assertTrue(report['selected_constraints_enabled'])
        self.assertTrue(report['selected_constraints_kinematic'])
        self.assertEqual(report['origin_index'], 255)
        self.assertEqual(report['body_frame'], 'part')
        self.assertFalse(report['physics_pv_basis_verified'])
        self.assertTrue(report['first_sample_zero_velocity'])
        self.assertFalse(report['native_hierarchy_compatibility_verified'])
        self.assertFalse(report['server_animation_confirmed'])
        self.assertFalse(report['network_authority_changed'])
        self.assertFalse(report['world_mutated'])
        self.assertEqual(report['body_frames'][self.root_key],
                         (tuple(root['position']), tuple(root['rotation'])))
        self.assertEqual(len(records), 1)
        self.assertIsInstance(records[0], ClientChildBody)
        self.assertEqual(records[0].motor_records, ())
        flattened = list(_walk(records))
        self.assertEqual(len(flattened), 15)
        self.assertTrue(all(record.current_record.state == 255
                            and record.current_record.reference_bytes is None
                            and record.current_record.velocity_mode == 'compact'
                            and record.current_record.linear_velocity is not None
                            and record.current_record.angular_velocity is not None
                            and record.motor_records == () for record in flattened))
        self.assertEqual(self.world.instances, self.received_before)
        self.assertEqual(self.world.revision, self.revision_before)

    def test_joint_pivot_requires_exact_received_offset_and_composes_in_world_basis(self):
        posed = self._posed(.21, _world_yaw((4., 8., -6.), -.42))
        history = ChildPoseHistory()
        _, report = self._build(posed, history, 2., body_frame='joint_pivot')
        candidates = [key for key in report['body_frames'] if key != self.root_key
                      and self.world.instances[key]['properties'].get('HasJointOffset') is True]
        self.assertEqual(len(candidates), 15)
        key = candidates[0]
        props = self.world.instances[key]['properties']
        incoming_position = tuple(props['JointOffset'])
        self.assertEqual(tuple(props['JointOffset']), incoming_position)
        part_position, rotation = posed[key]
        expected = tuple(part_position[row] + sum(rotation[row * 3 + col] * incoming_position[col]
                                                   for col in range(3)) for row in range(3))
        self.assertEqual(report['body_frames'][key][0], expected)

        props['JointOffset'] = tuple(props['JointOffset'])
        props['JointOffset'] = (props['JointOffset'][0] + 1e-8,
                                props['JointOffset'][1], props['JointOffset'][2])
        with self.assertRaisesRegex(AnimationChildSourceError, 'exactly match'):
            self._build(posed, ChildPoseHistory(), 3., body_frame='joint_pivot')

    def test_second_actual_clip_pose_produces_nonzero_child_velocity(self):
        history = ChildPoseHistory()
        first = self._posed(.12)
        _, first_report = self._build(first, history, 100.)
        self.assertTrue(first_report['first_sample_zero_velocity'])
        self.assertTrue(all(value == (0., 0., 0.)
                            for key, value in first_report['linear_velocities'].items()
                            if key != self.root_key))
        second = self._posed(.39)
        _, second_report = self._build(second, history, 100.27)
        self.assertFalse(second_report['first_sample_zero_velocity'])
        limbs = [key for key in second_report['body_frames']
                 if self._part_name(key) in ('leftupperarm', 'rightupperarm',
                                             'leftupperleg', 'rightupperleg')]
        self.assertEqual(len(limbs), 4)
        self.assertTrue(any(any(abs(v) > 1e-4 for v in second_report['linear_velocities'][key])
                            or any(abs(v) > 1e-4 for v in second_report['angular_velocities'][key])
                            for key in limbs))

    def test_rejects_foreign_pose_missing_edge_and_dynamic_or_incomplete_constraints(self):
        posed = self._posed(.2)
        with self.assertRaisesRegex(AnimationChildSourceError, 'foreign instance'):
            self._build({**posed, (0xBAD, 0xBAD): posed[self.root_key]}, ChildPoseHistory(), 1.)

        with self.assertRaisesRegex(AnimationChildSourceError, 'exactly'):
            self._build({key: value for key, value in posed.items() if key != next(iter(set(posed) - {self.root_key}))},
                        ChildPoseHistory(), 2.)

        constraint_key, constraint = next(iter(self._constraints()))
        props = constraint['properties']
        old_kinematic = props['IsKinematic']
        props['IsKinematic'] = False
        with self.assertRaisesRegex(AnimationChildSourceError, 'explicitly kinematic'):
            self._build(posed, ChildPoseHistory(), 3.)
        props['IsKinematic'] = old_kinematic
        del props['IsKinematic']
        with self.assertRaisesRegex(AnimationChildSourceError, 'explicitly kinematic'):
            self._build(posed, ChildPoseHistory(), 4.)
        props['IsKinematic'] = old_kinematic

        old_a1 = props['Attachment1']
        props['Attachment1'] = (0xBAD, 0xBAD)
        with self.assertRaisesRegex(AnimationChildSourceError, 'inside own character'):
            self._build(posed, ChildPoseHistory(), 5.)
        props['Attachment1'] = old_a1

    def test_pose_history_resets_on_world_or_root_generation_change(self):
        history = ChildPoseHistory()
        self._build(self._posed(.1), history, 1.)
        different_world = copy.deepcopy(self.world)
        avatar = different_world.avatar(self.user_id)
        other = build_kinematic_child_records(
            different_world, self.user_id, avatar['root_key'],
            evaluate_own_avatar(different_world, self.user_id,
                                avatar['root']['properties']['CFrame'], self.sampler.sample(.2))['poses'],
            context_tag=0x1234, origin_index=255, velocity_mode='compact',
            body_frame='part', now=50., history=history)
        self.assertTrue(other[1]['first_sample_zero_velocity'])

    @staticmethod
    def _ref(value):
        if isinstance(value, tuple):
            return value
        if isinstance(value, dict) and isinstance(value.get('$ref'), str):
            scope, index = value['$ref'].split(':', 1)
            return int(scope), int(index)
        return None

    def _part_name(self, key):
        value = self.world.instances[key]['properties'].get('Name', b'')
        if isinstance(value, bytes):
            value = value.decode('utf-8', 'replace')
        return str(value).lower().replace(' ', '').replace('_', '')


def _walk(records):
    for record in records:
        yield record
        yield from _walk(record.children)


if __name__ == '__main__':
    unittest.main()
