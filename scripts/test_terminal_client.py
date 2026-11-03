from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from local_runtime import HOST
import terminal_client as client


class TerminalClientTests(unittest.TestCase):
    def test_pending_status_shows_progress_without_replaying_submission_output(self):
        result={'ok':True,'completed':False,'output':'OLD WARNING',
                'diagnostic':{'scheduler_state':'ready','resume_count':7,
                              'execution_ms':12.5,'slice_interruptions':6,
                              'yield_count':0,'frames':[]}}
        out=io.StringIO()
        with patch.object(client,'send_request',return_value=result),redirect_stdout(out):
            client.show_job_status(4)
        self.assertIn('Job 4: ready',out.getvalue())
        self.assertIn('Resumes: 7',out.getvalue())
        self.assertIn('execution time: 12.5 ms',out.getvalue())
        self.assertNotIn('OLD WARNING',out.getvalue())

    def test_numeric_target_and_share_target_route_to_existing_selectors(self):
        command = client.selection_command('123974602339071')
        self.assertEqual(command[-2:], ['--place-id', '123974602339071'])
        share = 'https://www.roblox.com/share?code=' + 'a' * 32 + '&type=Server'
        self.assertTrue(client.selection_command(share)[1].endswith('select_private_server.py'))
        for invalid in ('0', '-1', str(2**53), 'https://example.com/share?code=x',
                        share.replace('www.roblox.com', 'www.roblox.com.evil.test'),
                        share.replace('type=Server', 'type=Experience')):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                client.selection_command(invalid)

    def test_game_specific_framework_is_never_sent_to_other_games(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(client, 'ROOT', Path(directory)):
            self.assertNotIn('--framework-init', client.session_command(6252985844, 120))
            spec = Path(directory) / 'secrets/private-client-framework-rpc.json'
            spec.parent.mkdir()
            spec.write_text('{}')
            self.assertIn('--framework-init', client.session_command(6252985844, 120))
            self.assertNotIn('--framework-init', client.session_command(123, 120))
        generic = client.session_command(123, 0)
        self.assertNotIn('--framework-init', generic)
        self.assertIn('--script-console', generic)
        self.assertIn('--progress', generic)
        self.assertEqual(generic[generic.index('--duration') + 1], '0')

    def test_normal_terminal_enables_gravity_by_default_and_supports_opt_out(self):
        self.assertIn('--physics', client.session_command(123, 0))
        self.assertNotIn('--physics', client.session_command(123, 0, physics=False))
        self.assertTrue(client.parse_args([]).no_physics is False)
        self.assertTrue(client.parse_args(['--no-physics']).no_physics)

    def test_one_minute_walk_is_opt_in_and_requires_physics(self):
        self.assertNotIn('--walk-for-minute', client.session_command(123, 0))
        command = client.session_command(123, 0, walk_for_minute=True)
        self.assertIn('--walk-for-minute', command)
        self.assertIn('--physics', command)
        self.assertTrue(client.parse_args(['--walk-for-minute']).walk_for_minute)
        with self.assertRaises(ValueError):
            client.session_command(123, 0, physics=False, walk_for_minute=True)
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            client.parse_args(['--walk-for-minute', '--no-physics'])

    def test_active_console_blocks_target_mutation(self):
        import socket
        with tempfile.TemporaryDirectory(prefix='tc-') as directory:
            root = Path(directory)
            (root / 'secrets').mkdir()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(root / 'secrets/client-script.sock'))
                server.listen(1)
                with patch.object(client, 'ROOT', root), self.assertRaises(client.SessionError):
                    client.prepare_target('123')
            self.assertEqual(list((root / 'secrets').iterdir()), [root / 'secrets/client-script.sock'])

    def test_script_errors_keep_output_visible(self):
        out = io.StringIO()
        with redirect_stdout(out):
            client.show_result({'ok': False, 'output': 'before failure\n',
                                'values': [], 'error': 'authored failure',
                                'task_errors': ['task failure']})
        for value in ('before failure', 'authored failure', 'task failure'):
            self.assertIn(value, out.getvalue())

    def test_chat_command_uses_one_dedicated_user_input_request(self):
        captured = []
        follower = Mock()
        follower.display = Mock()
        result = {'ok': True, 'state': 'running', 'job_id': 17,
                  'runtime_sequence': 3}
        with patch.object(client, 'ROOT', Path('/tmp/chat-test-root')), \
                patch.object(client, 'send_request', side_effect=lambda path, request:
                             captured.append((path, request)) or result):
            client.submit_chat('hello ; print("not Luau")', follower)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0][0], Path('/tmp/chat-test-root/secrets/client-script.sock'))
        self.assertEqual(captured[0][1], {
            'op': 'user_chat', 'text': 'hello ; print("not Luau")'})
        follower.display.assert_called_once_with({**result, 'sequence': 3})

    def test_chat_command_global_route_is_explicit_and_general_stays_default(self):
        self.assertEqual(client.parse_chat_command(':chat hello there'),
                         ('hello there', 'RBXGeneral'))
        self.assertEqual(client.parse_chat_command(':chat --global hello there'),
                         ('hello there', 'RBXGlobal'))
        self.assertEqual(client.parse_chat_command(':chat --global'),
                         ('', 'RBXGlobal'))
        captured = []
        with patch.object(client, 'ROOT', Path('/tmp/chat-test-root')), \
                patch.object(client, 'send_request', side_effect=lambda path, request:
                             captured.append(request) or {'ok': True}):
            client.submit_chat('hello', channel='RBXGlobal')
        self.assertEqual(captured, [{
            'op': 'user_chat', 'text': 'hello', 'channel': 'RBXGlobal'
        }])

    def test_empty_chat_command_reports_usage_without_sending(self):
        output = io.StringIO()
        with patch.object(client, 'send_request') as send, redirect_stdout(output):
            client.submit_chat('   ')
        send.assert_not_called()
        self.assertIn('Usage: :chat [--global] <message text>', output.getvalue())

    def test_saveinstance_parser_preserves_quoted_paths_without_shell_expansion(self):
        output, object_path = client.parse_saveinstance_command(
            ':saveinstance "map; $(touch should-not-run).rbxlx" "Workspace/My Model"')
        self.assertEqual(output, str((client.ROOT / 'rove exports' / 'map; $(touch should-not-run).rbxlx').resolve()))
        self.assertEqual(object_path, 'Workspace/My Model')
        with self.assertRaises(ValueError):
            client.parse_saveinstance_command(':saveinstance')
        with self.assertRaises(ValueError):
            client.parse_saveinstance_command(':saveinstance a.rbxlx b c')
        with self.assertRaises(ValueError):
            client.parse_saveinstance_command(':saveinstance "unterminated')

    def test_saveinstance_launcher_uses_socket_path_and_never_shell(self):
        root = Path('/tmp/saveinstance-test-root')
        socket_path = root / 'secrets/client-script.sock'
        output_path = root / 'map.rbxlx'
        command = client.saveinstance_command(socket_path, output_path, 'Workspace/Model')
        self.assertEqual(command, [sys.executable, '-u',
                                   str(client.ROOT / 'scripts/run_ussi.py'),
                                   '--socket', str(socket_path),
                                   '--output', str(output_path),
                                   '--object', 'Workspace/Model'])
        completed = subprocess.CompletedProcess(command, 0, 'Saved model.rbxlx\n', '')
        output = io.StringIO()
        with patch.object(client.subprocess, 'run', return_value=completed) as run, \
                patch.object(client, 'stop') as stop, redirect_stdout(output):
            self.assertTrue(client.submit_saveinstance(socket_path, output_path, 'Workspace/Model'))
        self.assertEqual(run.call_args.args[0], command)
        self.assertEqual(run.call_args.kwargs['cwd'], client.ROOT)
        self.assertTrue(run.call_args.kwargs['capture_output'])
        self.assertNotIn('shell', run.call_args.kwargs)
        stop.assert_not_called()
        self.assertIn('Saved model.rbxlx', output.getvalue())

    def test_saveinstance_failure_is_reported_and_does_not_touch_session(self):
        command = ['runner']
        completed = subprocess.CompletedProcess(command, 2, 'Export failed safely\n', '')
        output = io.StringIO()
        socket_path = Path('/tmp/saveinstance.sock')
        with patch.object(client.subprocess, 'run', return_value=completed) as run, \
                patch.object(client, 'send_request') as send, \
                patch.object(client, 'stop') as stop, redirect_stdout(output):
            self.assertFalse(client.submit_saveinstance(socket_path, '/tmp/out.rbxlx'))
        run.assert_called_once()
        send.assert_not_called()
        stop.assert_not_called()
        self.assertIn('Export failed safely', output.getvalue())
        self.assertIn('[error]', output.getvalue())

    def test_launcher_opens_and_closes_browser_console_by_default(self):
        process = Mock(returncode=0)
        with patch.object(sys, 'argv', ['terminal_client.py']), \
                patch.object(sys.stdin, 'isatty', return_value=True), \
                patch('builtins.input', return_value='123'), \
                patch.object(client, 'prepare_target', return_value=123), \
                patch.object(client.subprocess, 'Popen', return_value=process), \
                patch.object(client, 'console') as terminal, \
                patch.object(client, 'stop') as stop, \
                patch('web_console.WebConsole') as browser, redirect_stdout(io.StringIO()):
            browser.return_value.start.return_value = 'http://127.0.0.1:12345/'
            self.assertEqual(client.main(), 0)
        browser.return_value.start.assert_called_once()
        browser.return_value.close.assert_called_once()
        terminal.assert_called_once_with(process, 1000, 0)
        stop.assert_called_once_with(process)

    @unittest.skipUnless(HOST.is_file(), 'build the local runtime first')
    def test_pasted_multiline_and_file_execute_in_real_luau_and_print(self):
        # This fixture never reads credentials, allocates a game or connects to Roblox.
        worker = '''
import sys,time
from pathlib import Path
from script_console import ScriptConsole
from script_runtime import ClientScriptRuntime
from test_world_snapshot import fixture
world,_=fixture()
runtime=ClientScriptRuntime(lambda:world,123)
server=ScriptConsole(Path(sys.argv[1]),runtime.execute)
print('{"milestone":"own_player_in_players","seconds":0.1}',flush=True)
try:
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        server.poll()
        time.sleep(.005)
except KeyboardInterrupt:
    pass
finally:
    server.close()
'''
        with tempfile.TemporaryDirectory(prefix='tc-') as directory:
            root = Path(directory)
            (root / 'secrets').mkdir()
            script = root / 'test script.luau'
            script.write_text('print("loaded file"); return 99')
            process = subprocess.Popen([sys.executable, '-u', '-c', worker,
                                        str(root / 'secrets/client-script.sock')],
                                       cwd=Path(__file__).parent, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, start_new_session=True)
            read_fd, write_fd = os.pipe()

            def type_commands():
                deadline = time.monotonic() + 5
                while not (root / 'secrets/client-script.sock').exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                time.sleep(.2)
                commands = (':paste\nlocal p=game:GetService("Players").LocalPlayer\n'
                            'print("fixture player",p.UserId)\nwarn("fixture warning")\n'
                            'return 42\n:end\n:load "' + str(script) + '"\n'
                            'error("fixture failure")\n:quit\n')
                try:
                    os.write(write_fd, commands.encode())
                finally:
                    os.close(write_fd)

            typer = threading.Thread(target=type_commands, daemon=True)
            typer.start()
            output = io.StringIO()
            try:
                with os.fdopen(read_fd, 'r') as keyboard, patch.object(client, 'ROOT', root), \
                        patch.object(sys, 'stdin', keyboard), redirect_stdout(output):
                    client.console(process, 1000)
            finally:
                client.stop(process)
                process.stdout.close()
                typer.join(timeout=6)
            for expected in ('own player in players', 'fixture player\t123',
                             'fixture warning', '[return] [42.0]', 'loaded file',
                             '[return] [99.0]', 'fixture failure'):
                self.assertIn(expected, output.getvalue())


if __name__ == '__main__':
    unittest.main()
