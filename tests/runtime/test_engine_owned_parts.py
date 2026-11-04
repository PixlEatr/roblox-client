"""Owned body feedback is local, atomic, and guarded independently of avatars."""
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import persistent_runtime

ROOT = Path(__file__).resolve().parents[2]
IDENTITY = [1, 0, 0, 0, 1, 0, 0, 0, 1]


def cf(position):
    return {'$cframe': {'position': list(position), 'rotation': IDENTITY[:]}}


class EngineOwnedPartTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = json.loads((ROOT/'tests/fixtures/world_snapshot.json').read_text())
        self.snapshot['nodes'].append({'id': 'owned-box', 'class_name': 'Part',
            'name': 'OwnedBox', 'parent': 'scope:workspace', 'properties': {
                'NetworkOwnerV3': 5, 'Anchored': False, 'CFrame': cf((0, 10, 0)),
                'Size': {'$vector3': [1, 1, 1]},
                'AssemblyLinearVelocity': {'$vector3': [0, 0, 0]},
                'AssemblyAngularVelocity': {'$vector3': [0, 0, 0]}},
            'property_revisions': {'CFrame': self.snapshot['sequence']}})
        self.host = None
        self.addCleanup(lambda: self.host.close() if self.host is not None else None)

    def start(self):
        host = Path(os.environ.get('ENGINE_OWNED_PART_HOST', os.environ['ROBLOX_LUAU_RUST_HOST']))
        with patch.object(persistent_runtime, 'RUST_HOST', host):
            self.host = persistent_runtime.PersistentLuauHost(copy.deepcopy(self.snapshot))

    def execute(self, source):
        result = self.host.call({'op': 'execute', 'source': source, 'timeout_ms': 1000})
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['task_errors'], result)
        return result

    def frame(self, key='owned-box', x=1):
        return {'id': key, 'cframe': cf((x, 9, 3)),
                'linear_velocity': [0, -5, 0], 'angular_velocity': [0, 0, 0]}

    def update(self, frames=None, actions=(), revision=None):
        revision = self.snapshot['sequence'] if revision is None else revision
        return self.host.call({'op': 'update', 'timeout_ms': 100, 'delta': {
            'version': 1, 'delta': True, 'sequence': max(revision, self.snapshot['sequence']),
            'actions': list(actions), 'engine_owned_part_updates': {
                'scope': 5, 'source_revision': int(revision),
                'frames': [self.frame()] if frames is None else frames}}})

    def test_local_atomic_pose_velocity_feedback_and_received_update_wins(self):
        self.start()
        self.execute('''
getgenv().box=workspace.OwnedBox
getgenv().events=0
box:GetPropertyChangedSignal("CFrame"):Connect(function()
 events+=1
 assert(box.AssemblyLinearVelocity.Y==-5)
end)
''')
        result = self.update()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['engine_owned_part_status'], 'applied')
        self.assertEqual(result['engine_owned_part_frames_applied'], 1)
        self.assertEqual(result['mutations'], [])
        self.assertEqual(result['server_actions'], 0)
        self.assertEqual(result['world_revision'], self.snapshot['sequence'])
        self.execute('''
assert(events==1 and box.Position.X==1 and box.Position.Y==9)
assert(client.propertyOrigin(box,"CFrame")=="local")
assert(client.propertyOrigin(box,"AssemblyLinearVelocity")=="local")
''')
        result = self.host.call({'op': 'update', 'timeout_ms': 100, 'delta': {
            'version': 1, 'delta': True, 'actions': [{'kind': 'set', 'id': 'owned-box',
                'sequence': self.snapshot['sequence']+1, 'fields': {'CFrame': cf((8, 10, 4))}}]}})
        self.assertTrue(result['ok'], result)
        self.execute('assert(box.Position.X==8 and client.propertyOrigin(box,"CFrame")=="received")')

    def test_changed_owner_rejects_all_frames_without_partial_write(self):
        self.start()
        result = self.update(actions=[{'kind': 'set', 'id': 'owned-box',
            'sequence': self.snapshot['sequence']+1, 'fields': {'NetworkOwnerV3': 7}}])
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['engine_owned_part_status'], 'stale_authority')
        self.execute('assert(workspace.OwnedBox.Position.Y==10)')

    def test_malformed_second_frame_is_atomic(self):
        self.start()
        bad = self.frame('second'); bad['linear_velocity'] = [0, 0]
        result = self.update([self.frame(), bad])
        self.assertFalse(result['ok'], result)
        self.execute('assert(workspace.OwnedBox.Position.Y==10)')

    def test_anchored_character_or_jointed_targets_are_excluded(self):
        for case in ('anchored', 'character', 'joint', 'unsupported_force'):
            with self.subTest(case=case):
                original = copy.deepcopy(self.snapshot)
                owned = self.snapshot['nodes'][-1]
                if case == 'anchored': owned['properties']['Anchored'] = True
                if case == 'character': owned['parent'] = 'scope:character-1'
                if case in ('joint', 'unsupported_force'):
                    self.snapshot['nodes'].append({'id': 'link',
                        'class_name': 'WeldConstraint' if case == 'joint' else 'BodyPosition',
                        'name': 'Link', 'parent': 'owned-box',
                        'properties': {'Part0': {'$ref': 'owned-box'}, 'Part1': {'$ref': 'scope:root-1'}}})
                self.start()
                result = self.update()
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['engine_owned_part_status'], 'stale_authority')
                self.assertEqual(result['engine_owned_part_frames_applied'], 0)
                self.host.close(); self.host = None
                self.snapshot = original

    def test_received_pose_newer_than_sample_rejects_feedback(self):
        self.start()
        result = self.update(actions=[{'kind': 'set', 'id': 'owned-box',
            'sequence': self.snapshot['sequence']+1, 'fields': {'CFrame': cf((7, 8, 9))}}])
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['engine_owned_part_status'], 'stale_authority')
        self.execute('assert(workspace.OwnedBox.Position.X==7)')

    def test_owned_body_channel_cannot_repurpose_character_channel(self):
        self.start()
        result = self.update([self.frame('scope:root-1')])
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['engine_owned_part_status'], 'stale_authority')


if __name__ == '__main__':
    unittest.main()
