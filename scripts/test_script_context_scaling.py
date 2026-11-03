"""Real-worker regressions for script eligibility during partial replication."""
import unittest

from persistent_runtime import PersistentLuauHost
from test_script_execution import add_script, snapshot


class ScriptContextTests(unittest.TestCase):
    def without_character(self):
        data = snapshot()
        data['initial_replication_complete'] = False
        player = next(node for node in data['nodes'] if node['id'] == data['local_player'])
        player['properties'].pop('Character')
        return data

    def test_large_partial_world_with_many_dormant_local_scripts(self):
        data = self.without_character()
        for index in range(40000):
            data['nodes'].append({'id': f'bulk:{index}', 'name': 'Folder',
                                  'class_name': 'Folder', 'parent': 'scope:workspace',
                                  'properties': {}})
        for index in range(1200):
            add_script(data, f'Stored{index}', 'error("stored script must not run")',
                       kind='LocalScript')
        add_script(data, 'Gui', 'shared.guiReady=true', kind='LocalScript', parent='gui')
        # The normal five-second worker guard remains enabled. Previously the
        # missing Character lookup scanned all 40k nodes for each LocalScript.
        with PersistentLuauHost(data) as host:
            self.assertTrue(host.call({'op': 'poll', 'timeout_ms': 1000})['ok'])
            result = host.call({'op': 'execute', 'source': 'return shared.guiReady',
                                'timeout_ms': 1000})
            self.assertTrue(result['ok'], result.get('error'))
            self.assertEqual(result['values'], [True])
            self.assertEqual(result['script_runtime']['failed'], 0)
            self.assertEqual(result['script_runtime']['local_scripts'], 1)

    def test_character_context_refreshes_after_replication(self):
        data = self.without_character()
        data['nodes'].append({'id': 'other-character', 'name': 'OtherCharacter',
                              'class_name': 'Model', 'parent': 'scope:workspace',
                              'properties': {}})
        add_script(data, 'FirstCharacter', 'shared.first=(shared.first or 0)+1; task.wait(100)',
                   kind='LocalScript', parent='scope:character-1')
        add_script(data, 'SecondCharacter', 'shared.second=(shared.second or 0)+1; task.wait(100)',
                   kind='LocalScript', parent='other-character')
        add_script(data, 'Gui', 'shared.gui=(shared.gui or 0)+1', kind='LocalScript', parent='gui')
        with PersistentLuauHost(data) as host:
            def values():
                self.assertTrue(host.call({'op': 'poll', 'timeout_ms': 1000})['ok'])
                result = host.call({'op': 'execute', 'source': 'return shared.first,shared.second,shared.gui',
                                    'timeout_ms': 1000})
                self.assertTrue(result['ok'], result.get('error'))
                return result

            self.assertEqual(values()['values'], [None, None, 1])
            for sequence, character, expected in (
                    (8, 'scope:character-1', [1, None, 1]),
                    (9, 'other-character', [1, 1, 1]),
                    (10, None, [1, 1, 1]),
                    (11, 'scope:character-1', [2, 1, 1])):
                result = host.call({'op': 'update', 'delta': {
                    'version': 1, 'delta': True, 'sequence': sequence, 'actions': [{
                        'sequence': sequence, 'kind': 'set', 'id': data['local_player'],
                        'fields': {'Character': {'$ref': character} if character else None}}]}})
                self.assertTrue(result['ok'], result.get('error'))
                result = values()
                self.assertEqual(result['values'], expected)
                states = {row['id']: row['state'] for row in result['script_runtime']['details'].values()}
                if sequence >= 9:
                    self.assertEqual(states['FirstCharacter'], 'running' if sequence == 11 else 'stopped')
                if sequence >= 10:
                    self.assertEqual(states['SecondCharacter'], 'stopped')


if __name__ == '__main__':
    unittest.main()
