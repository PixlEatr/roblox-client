"""Result collection through a real VM's bottom Frame/readonly TextBox UI.

The link and clipboard sink here are offline fixtures, never published results.
"""
import copy
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from capture_sunc_result import capture, read_macos_clipboard, result_control, share_url
from persistent_runtime import PersistentLuauHost
from test_world_snapshot import fixture
from world_snapshot import make_world_snapshot


class CaptureSuncResultTests(unittest.TestCase):
    def setUp(self):
        world, _ = fixture()
        self.host = PersistentLuauHost(make_world_snapshot(world, 123))
        self.addCleanup(self.host.close)
        response = self.host.call({'op': 'execute', 'source': '''
local screen=Instance.new("ScreenGui",game:GetService("CoreGui"))
screen.Name="sUNC_melon_result"
local panel=Instance.new("Frame",screen)
panel.Name="result_frame";panel.AnchorPoint=Vector2.new(.5,1)
panel.Position=UDim2.new(.5,0,1,-40);panel.Size=UDim2.fromOffset(360,42)
local text=Instance.new("TextBox",panel)
text.Name="link_textbox";text.Text="r.sunc.su/offline-fixture"
text.TextEditable=false;text.Size=UDim2.fromScale(1,1)
collectorPresses=0;collectorReleases=0
panel.InputBegan:Connect(function(input)
    assert(input.UserInputType==Enum.UserInputType.MouseButton1)
    collectorPresses+=1;collectorCopied=text.Text
end)
panel.InputEnded:Connect(function(input)
    assert(input.UserInputState==Enum.UserInputState.End)
    collectorReleases+=1
end)
collectorPanel=panel;collectorText=text;collectorScreen=screen
return true
''', 'timeout_ms': 1000})
        self.assertTrue(response['ok'], response)
        self.host.call({'op': 'update', 'delta': {'version': 1, 'delta': True, 'actions': [],
            'display': {'size': [1280, 720], 'device_insets': [0,0,0,0], 'core_insets': [0,0,0,0]}}})

    def clipboard(self):
        return self.host.call({'op': 'execute', 'source': 'return collectorCopied'})['values'][0]

    def request(self, command):
        # Match ScriptConsole's source-request shape while using the real VM.
        if 'source' in command:
            command = {'op': 'execute', **command}
        return self.host.call(command)

    def test_observe_then_click_real_bottom_frame_and_verify_copied_text(self):
        observed = capture(self.request)
        self.assertEqual(observed['state'], 'panel_observed')
        self.assertEqual(observed['panel_box'][:4], [460, 638, 360, 42])
        self.assertFalse(observed['clipboard_verified'])
        self.assertEqual(self.host.call({'op': 'execute', 'source': 'return collectorPresses'})['values'], [0])
        copied = capture(self.request, read_clipboard=self.clipboard)
        self.assertEqual(copied['state'], 'copied')
        self.assertTrue(copied['clipboard_verified'])
        self.assertEqual(copied['share_url'], 'https://r.sunc.su/offline-fixture')
        self.assertEqual(self.host.call({'op': 'execute', 'source': 'return collectorPresses,collectorReleases'})['values'], [1, 1])

    def test_hidden_or_offscreen_panel_never_clicks(self):
        for code in ('collectorScreen.Enabled=false',
                     'collectorScreen.Enabled=true;collectorPanel.Position=UDim2.fromOffset(0,-1000)',
                     'collectorPanel.Position=UDim2.new(.5,0,1,20)'):
            self.host.call({'op': 'execute', 'source': code})
            result = capture(self.request, read_clipboard=lambda: self.fail('clipboard must not be read'))
            self.assertEqual(result['state'], 'waiting_for_panel')
        self.assertEqual(self.host.call({'op': 'execute', 'source': 'return collectorPresses'})['values'], [0])

    def test_unrelated_clipboard_is_not_included_or_accepted(self):
        result = capture(self.request, read_clipboard=lambda: 'private unrelated text', clipboard_timeout=0)
        self.assertEqual(result['state'], 'copy_failed')
        self.assertFalse(result['clipboard_verified'])
        self.assertNotIn('private unrelated text', str(result))

    def test_clipboard_failure_is_not_reported_as_a_disconnected_session(self):
        def unavailable():
            raise OSError('clipboard helper is unavailable')
        result = capture(self.request, read_clipboard=unavailable)
        self.assertEqual(result['state'], 'copy_failed')
        self.assertEqual(result['reason'], 'clipboard_unavailable')
        self.assertEqual(self.host.call({'op': 'execute', 'source': 'return collectorReleases'})['values'], [1])

    def test_matching_game_gui_without_result_screen_is_not_clicked(self):
        self.host.call({'op': 'execute', 'source': 'collectorScreen.Name="OtherGameGui"'})
        result = capture(self.request, read_clipboard=lambda: self.fail('clipboard must not be read'))
        self.assertEqual(result['reason'], 'panel_provenance_unavailable')
        self.assertEqual(self.host.call({'op': 'execute', 'source': 'return collectorPresses'})['values'], [0])

    def test_ambiguous_and_zero_viewport_are_not_interpreted_as_results(self):
        response = self.host.call({'op': 'render'})
        duplicates = copy.deepcopy(response['frame']['gui'])
        for node in duplicates:
            node['id'] += '-second'
            if node.get('pointerTarget'):
                node['pointerTarget'] += '-second'
        response['frame']['gui'] += duplicates
        self.assertEqual(result_control(response)['reason'], 'ambiguous_panels')
        response['frame']['viewport'] = [0, 0]
        self.assertEqual(result_control(response)['reason'], 'display_unavailable')

    def test_lookalike_or_incomplete_url_is_not_a_share_link(self):
        for url in ('r.sunc.su/', 'https://r.sunc.su.evil.invalid/id',
                    'https://r.sunc.su@evil.invalid/id', 'https://r.sunc.su/id?token=value',
                    'http://r.sunc.su/id', 'result r.sunc.su/id', 'r.sunc.su/../id'):
            self.assertIsNone(share_url(url), url)
        self.assertEqual(share_url('r.sunc.io/observed-id'), 'https://r.sunc.io/observed-id')


class ClipboardReaderTests(unittest.TestCase):
    def run_reader(self, source):
        # Exercise real pipes and process cleanup, without touching the user's
        # clipboard. The platform command is replaced only in this test.
        spawn = subprocess.Popen
        children = []
        def fixture_process(command, **kwargs):
            self.assertEqual(command, ['/usr/bin/pbpaste'])
            child = spawn([sys.executable, '-c', source], **kwargs)
            children.append(child)
            return child
        with patch('capture_sunc_result.subprocess.Popen', side_effect=fixture_process):
            value = read_macos_clipboard()
        self.assertIsNotNone(children[0].poll())
        self.assertTrue(children[0].stdout.closed)
        return value

    def test_bounded_reader_returns_text_and_discards_oversized_contents(self):
        self.assertEqual(self.run_reader("import os;os.write(1,b'r.sunc.su/offline-fixture')"),
                         'r.sunc.su/offline-fixture')
        self.assertIsNone(self.run_reader("import os,time;os.write(1,b'x'*100000);time.sleep(10)"))
        self.assertIsNone(self.run_reader("import os;os.write(1,b'\\xff')"))

    def test_unresponsive_clipboard_process_is_terminated(self):
        started = time.monotonic()
        self.assertIsNone(self.run_reader('import time;time.sleep(10)'))
        self.assertLess(time.monotonic()-started, 4)


if __name__ == '__main__':
    unittest.main()
