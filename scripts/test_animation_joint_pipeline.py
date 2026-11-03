"""Offline end-to-end check: native Luau track intents to received avatar poses.

The live capture and the public run clip are read-only inputs. This test never
joins a game, authenticates, or sends animation data anywhere.
"""
import copy
import json
import math
import os
import re
import time
import unittest
from pathlib import Path
from unittest import mock

from animation_assets import AnimationAssetQueue
from animation_joint_model import AnimationJointModel
import persistent_runtime
from world_snapshot import instance_id, make_world_snapshot, replay_world


ROOT = Path(__file__).resolve().parents[1]
CAPTURE_REPORT = ROOT / 'outputs/gamejoin/owned-animation-slot-probe3.json'
LAST_JOIN = ROOT / 'secrets/last-join.json'
RUN_CLIP = Path.home() / '.cache/roblox-client-research/animation-poses/run.json'


def _capture_user_id():
    """Read the private account context without returning or logging its ID."""
    values = json.loads(LAST_JOIN.read_text())
    matches = re.findall(r"[\\\"']?UserId[\\\"']?\s*[:=]\s*[\\\"']?(\d+)",
                         json.dumps(values, ensure_ascii=True))
    return list(dict.fromkeys(matches))


def _trim_to_local_avatar(world, user_id):
    """Keep services, own Player, and its character subtree in this local replay."""
    avatar = world.avatar(user_id)
    if not avatar['evidence']['player_spawned']:
        raise ValueError('captured own character is not spawned')
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
    # Preserve ancestors linking the own Player to its Players service.
    for root in tuple(roots):
        node = world.instances.get(root)
        seen = set()
        while node is not None and node.get('parent') is not None:
            parent = node['parent']
            if parent in seen:
                raise ValueError('captured world ancestry cycle')
            seen.add(parent)
            keep.add(parent)
            node = world.instances.get(parent)
    world.instances = {key: value for key, value in world.instances.items() if key in keep}
    world.creation = {key: value for key, value in world.creation.items() if key in keep}
    world.property_versions = {key: value for key, value in world.property_versions.items() if key in keep}
    return avatar


def _root_frame(position, yaw=0.):
    c, s = math.cos(yaw), math.sin(yaw)
    return {'position': list(position),
            'rotation': [c, 0., s, 0., 1., 0., -s, 0., c]}


class AnimationJointPipelineTests(unittest.TestCase):
    def test_native_luau_intent_drives_actual_received_rig_without_world_mutation(self):
        if not CAPTURE_REPORT.is_file() or not LAST_JOIN.is_file() or not RUN_CLIP.is_file():
            self.skipTest('local private capture or cached public run clip is unavailable')
        if not Path(os.environ.get('ENGINE_POSE_PERSISTENT_HOST',
                                   str(persistent_runtime.RUST_HOST))).is_file():
            self.skipTest('engine-pose host is unavailable')

        clip = json.loads(RUN_CLIP.read_text())
        reference = 'rbxassetid://' + str(clip['asset_id'])
        self.assertEqual(clip.get('kind'), 'KeyframeSequence')
        self.assertGreater(clip.get('length', 0), 0)

        world = user_id = avatar = None
        for candidate in reversed(_capture_user_id()):
            try:
                candidate_id = int(candidate)
                candidate_world = replay_world(CAPTURE_REPORT, candidate_id)
                if candidate_world.avatar(candidate_id)['evidence']['player_spawned']:
                    world, user_id = candidate_world, candidate_id
                    break
            except (ValueError, KeyError, TypeError):
                continue
        if world is None:
            self.skipTest('no local replay matches the private capture context')
        avatar = _trim_to_local_avatar(world, user_id)
        received_before = copy.deepcopy(world.instances)
        revision_before = world.revision
        snapshot = make_world_snapshot(world, user_id, include_script_sources=False)

        # Populate the bounded queue from the already cached public clip. Its
        # injected fetcher cannot access the network.
        assets = AnimationAssetQueue(fetcher=lambda _: clip)
        self.addCleanup(assets.close)
        assets.submit([{'reference': reference}])
        deadline = time.monotonic() + 2.
        responses = []
        while time.monotonic() < deadline and not responses:
            responses = assets.drain()
            if not responses:
                time.sleep(.005)
        self.assertTrue(responses, 'cached clip did not load through the injected asset queue')
        self.assertIn('clip', responses[0])
        self.assertIsNotNone(assets.pose_clip(reference))

        source = '''
local players = game:GetService("Players")
local character = players.LocalPlayer.Character
local humanoid = character and character:FindFirstChildOfClass("Humanoid")
local animator = humanoid and humanoid:FindFirstChildOfClass("Animator")
assert(animator, "received own Animator is unavailable")
local animation = Instance.new("Animation")
animation.AnimationId = %r
pipelineTrack = animator:LoadAnimation(animation)
pipelineTrack.Looped = true
pipelineTrack.Priority = Enum.AnimationPriority.Core
pipelineTrack:Play(0, 1, 1)
return pipelineTrack.IsPlaying, pipelineTrack.Looped, pipelineTrack.Priority.Value,
       pipelineTrack.Animation.AnimationId
''' % reference
        # An isolated build can be selected without tying the test to a
        # developer-specific candidate path.
        host_path = Path(os.environ.get('ENGINE_POSE_PERSISTENT_HOST',
                                       str(persistent_runtime.RUST_HOST)))
        with mock.patch.object(persistent_runtime, 'RUST_HOST', host_path):
            host = persistent_runtime.PersistentLuauHost(snapshot)
        self.addCleanup(host.close)
        native = host.call({'op': 'execute', 'source': source, 'timeout_ms': 250})
        self.assertTrue(native['ok'], native.get('error'))
        self.assertEqual(native['values'], [True, True, 1000, reference])
        intents = native.get('animation_intents', [])
        self.assertEqual([entry['kind'] for entry in intents], ['Looped', 'Priority', 'Play'],
                         {key: native.get(key) for key in ('runtime_backend', 'animation_intents', 'animation_requests')})
        self.assertTrue(all(entry['animation_reference'] == reference for entry in intents))
        actor_ids = {entry['animator_id'] for entry in intents}
        self.assertEqual(len(actor_ids), 1)

        model = AnimationJointModel(user_id, assets.pose_clip)
        physics = {'predicted_position': avatar['root']['properties']['CFrame']['position'],
                   'predicted_yaw': 0.}
        t0 = 0.
        first = model.tick(world=world, physics_report=physics, entries=intents, now=t0)
        self.assertEqual(first.get('status'), 'ok', first)
        self.assertGreaterEqual(first.get('resolved_bodyparts', 0), 15)
        self.assertFalse(first['server_animation_confirmed'])

        # The source clip loops at its actual duration; choose two separated
        # interior fractions and compare real received limb CFrames.
        t1, t2 = clip['length'] * .19, clip['length'] * .57
        second = model.tick(world=world, physics_report=physics, entries=[], now=t1)
        third = model.tick(world=world, physics_report=physics, entries=[], now=t2)
        self.assertEqual(second.get('status'), 'ok', second)
        self.assertEqual(third.get('status'), 'ok', third)
        rig = model._rig
        names = {key: world.schema['classes'][node['class']]['name']
                 for key, node in world.instances.items()}
        def part_named(name):
            def normalized(key):
                value = world.instances[key]['properties'].get('Name', b'')
                if isinstance(value, bytes):
                    value = value.decode('utf-8', 'replace')
                return str(value).lower().replace(' ', '').replace('_', '')
            return next(key for key in rig.subtree if names[key] in ('Part', 'MeshPart')
                        and normalized(key) == name.lower().replace(' ', '').replace('_', ''))
        root_key = avatar['root_key']
        arm_keys = [part_named('LeftUpperArm'), part_named('RightUpperArm')]
        leg_keys = [part_named('LeftUpperLeg'), part_named('RightUpperLeg')]
        def changed(a, b, key):
            fa, fb = a['poses'][key], b['poses'][key]
            return any(abs(x-y) > 1e-5 for x, y in zip(fa[0] + fa[1], fb[0] + fb[1]))
        self.assertEqual(first['poses'][root_key], second['poses'][root_key])
        self.assertEqual(second['poses'][root_key], third['poses'][root_key])
        self.assertTrue(any(changed(second, third, key) for key in arm_keys),
                        'authored clip produced no changing arm world CFrame')
        self.assertTrue(any(changed(second, third, key) for key in leg_keys),
                        'authored clip produced no changing leg world CFrame')
        self.assertGreater(third['arm_pose_changes'],0)
        self.assertGreater(third['leg_pose_changes'],0)

        # Apply both local FK frames through the isolated native engine-pose
        # candidate, then ask ordinary Luau to read the actual Part CFrames.
        # The candidate overlays local engine properties only; it has no
        # network or server-authority path in this test.
        def apply_engine_poses(result):
            poses = result.get('engine_poses')
            self.assertIsInstance(poses, dict)
            applied = host.call({'op': 'update', 'delta': {
                'version': 1, 'delta': True, 'actions': [], 'engine_poses': poses},
                'timeout_ms': 250})
            self.assertTrue(applied['ok'], applied.get('error'))
            self.assertEqual(applied.get('engine_pose_status'), 'applied', applied)
            self.assertEqual(applied.get('engine_pose_frames_applied'), len(poses['frames']))
            self.assertEqual(applied.get('mutations'), [])
            self.assertEqual(applied.get('server_actions'), 0)
            return poses

        def read_limb_cframes():
            source = '''
local character = game:GetService("Players").LocalPlayer.Character
local left = character:FindFirstChild("LeftUpperArm")
local right = character:FindFirstChild("RightUpperArm")
assert(left and right, "received upper limbs are unavailable")
local lx,ly,lz,l00,l01,l02,l10,l11,l12,l20,l21,l22 = left.CFrame:GetComponents()
local rx,ry,rz,r00,r01,r02,r10,r11,r12,r20,r21,r22 = right.CFrame:GetComponents()
return lx,ly,lz,l00,l01,l02,l10,l11,l12,l20,l21,l22,
       rx,ry,rz,r00,r01,r02,r10,r11,r12,r20,r21,r22
'''
            result = host.call({'op': 'execute', 'source': source, 'timeout_ms': 250})
            self.assertTrue(result['ok'], result.get('error'))
            self.assertEqual(len(result['values']), 24, result.get('values'))
            self.assertEqual(result.get('mutations'), [])
            return result['values']

        def assert_readback_matches(values, poses):
            frames = {entry['id']: entry['cframe']['$cframe'] for entry in poses['frames']}
            limb_keys = [part_named('LeftUpperArm'), part_named('RightUpperArm')]
            for offset, key in zip((0, 12), limb_keys):
                expected = frames[instance_id(key)]
                for actual, wanted in zip(values[offset:offset + 12],
                                          expected['position'] + expected['rotation']):
                    self.assertAlmostEqual(actual, wanted, delta=1e-5)

        second_poses = apply_engine_poses(second)
        second_cframes = read_limb_cframes()
        assert_readback_matches(second_cframes, second_poses)
        third_poses = apply_engine_poses(third)
        third_cframes = read_limb_cframes()
        assert_readback_matches(third_cframes, third_poses)
        self.assertTrue(any(abs(a - b) > 1e-4 for a, b in
                            zip(second_cframes[:12], third_cframes[:12])),
                        'Luau left upper arm CFrame did not change across applied poses')
        self.assertTrue(any(abs(a - b) > 1e-4 for a, b in
                            zip(second_cframes[12:], third_cframes[12:])),
                        'Luau right upper arm CFrame did not change across applied poses')
        self.assertEqual(world.instances, received_before)
        self.assertEqual(world.revision, revision_before)

        # A genuine Stop intent is processed by the same pose runtime; after a
        # short fade it must leave no active local joint pose.
        stop_source = 'pipelineTrack:Stop(.05); return pipelineTrack.IsPlaying'
        stopped = host.call({'op': 'execute', 'source': stop_source, 'timeout_ms': 250})
        self.assertTrue(stopped['ok'], stopped.get('error'))
        stop_entries = stopped.get('animation_intents', [])
        self.assertIn('Stop', [entry['kind'] for entry in stop_entries])
        model.tick(world=world, physics_report=physics, entries=stop_entries, now=t2)
        final = model.tick(world=world, physics_report=physics, entries=[], now=t2 + .06)
        self.assertEqual(final.get('status'), 'no_active_pose', final)
        self.assertFalse(final['server_animation_confirmed'])
        self.assertEqual(world.instances, received_before)


if __name__ == '__main__':
    unittest.main()
