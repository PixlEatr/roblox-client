import json
from pathlib import Path
import socket
import stat
import tempfile
import threading
import unittest

from script_console import ScriptConsole, send_source


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir='/tmp', prefix='luau-console-test-')
        self.path = Path(self.folder.name)/'client.sock'
        def execute(source, **kwargs):
            if source == 'bad': raise ValueError('authored test failure')
            return {'ok': True, 'values': [source], 'output': '', 'mutations': []}
        self.console = ScriptConsole(self.path, execute)
        self.stop = threading.Event()
        def run():
            while not self.stop.is_set():
                self.console.poll()
                self.stop.wait(.001)
        self.thread = threading.Thread(target=run)
        self.thread.start()

    def tearDown(self):
        self.stop.set()
        self.thread.join(2)
        self.console.close()
        self.folder.cleanup()

    def test_source_roundtrip_and_owner_only_socket(self):
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        result = send_source(self.path, 'return "hello"')
        self.assertTrue(result['ok'])
        self.assertEqual(result['values'], ['return "hello"'])

    def test_script_error_does_not_stop_the_console(self):
        self.assertFalse(send_source(self.path, 'bad')['ok'])
        self.assertTrue(send_source(self.path, 'return 1')['ok'])

    def test_received_inspection_stays_separate_from_source_execution(self):
        from script_console import send_request
        seen=[]
        self.console.inspect_received=lambda path: seen.append(path) or {'ok':True,'values':[],'revision':42}
        self.console.execute=lambda *a,**kw: self.fail('Inspection executed source')
        result=send_request(self.path,{'op':'inspect_received','path':['Workspace','Part']})
        self.assertEqual(result['revision'],42);self.assertEqual(seen,[['Workspace','Part']])
        self.assertFalse(send_request(self.path,{'op':'inspect_received','path':[],'extra':True})['ok'])

    def test_source_larger_than_old_frame_limit_transfers_intact(self):
        self.console.execute = lambda source, **kwargs: {'ok': True, 'values': [len(source)]}
        source = '--' + 'x' * (2 * 1024 * 1024) + '\nreturn 42'
        self.assertEqual(send_source(self.path, source)['values'], [len(source)])

    def test_snapshot_transfer_is_detached_chunked_and_released(self):
        from run_ussi import capture_console_snapshot
        snapshot = {'nodes': [{'name': 'owned', 'value': 'x'*1_100_000}]}
        self.console.export_snapshot = lambda: snapshot
        captured = capture_console_snapshot(self.path)
        self.assertEqual(captured, snapshot)
        self.assertFalse(self.console._export_snapshots)

    def test_snapshot_transfer_rejects_unknown_tokens_and_bad_offsets(self):
        from script_console import send_request
        self.console.export_snapshot = lambda: {'nodes': []}
        begin = send_request(self.path, {'op': 'export_snapshot'})
        for token, offset in [('unknown', 0), (begin['token'], -1),
                              (begin['token'], True), (begin['token'], begin['bytes'])]:
            result = send_request(self.path, {'op': 'export_snapshot_chunk',
                                             'token': token, 'offset': offset})
            self.assertFalse(result['ok'], result)
        send_request(self.path, {'op': 'export_snapshot_release', 'token': begin['token']})
        self.assertFalse(self.console._export_snapshots)

    def test_active_socket_cannot_be_replaced(self):
        inode = self.path.stat().st_ino
        with self.assertRaises(ValueError): ScriptConsole(self.path, lambda *_: None)
        self.assertEqual(self.path.stat().st_ino, inode)
        self.assertTrue(send_source(self.path, 'still running')['ok'])


if __name__ == '__main__':
    unittest.main()
