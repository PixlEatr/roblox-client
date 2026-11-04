import json
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from start_ui import runtime_files
import launcher_setup

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(sys.platform == 'darwin', 'macOS command launcher')
class UIStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rove startup ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('Start.command', 'Setup Rove.command', 'Rove UI.command',
                     'rove_ui.py', 'rove.py', 'scripts/start_ui.py', 'scripts/platform_environment.py'):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        (self.root / 'scripts/setup_terminal.py').write_text('''
import json, os
from pathlib import Path
import sys, venv
root=Path(__file__).resolve().parents[1]
counter=root/'setups.txt'
counter.write_text(str(int(counter.read_text())+1) if counter.exists() else '1')
if os.environ.get('ROVE_TEST_SETUP_EXIT'):raise SystemExit(int(os.environ['ROVE_TEST_SETUP_EXIT']))
assert sys.argv[1:]==['--with-ui'],sys.argv
venv.EnvBuilder(with_pip=False).create(root/'.venv')
packages=root/'.venv/lib'/('python'+str(sys.version_info.major)+'.'+str(sys.version_info.minor))/'site-packages'
packages.mkdir(parents=True,exist_ok=True)
for name in ('aioquic','cryptography','OpenSSL','zstandard','blake3'):(packages/(name+'.py')).touch()
for name in ('runtime/build/luau-host','rust-client/target/release/headless-luau',
             'animation-codec/target/release/animation-codec','ui/avatar-viewer.js','ui/world-viewer.js'):
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.touch()
''')
        opened = '''
import json
from pathlib import Path
import sys
if '--help' in sys.argv:
    print('Fixture launcher help')
else:
    (Path(__file__).resolve().parents[1]/'opened.json').write_text(json.dumps(sys.argv[1:]))
'''
        (self.root / 'scripts/open_ui.py').write_text(opened)
        (self.root / 'scripts/launcher.py').write_text(
            'from start_ui import prepare_launcher\nprepare_launcher()\n' + opened)

    def run_entry(self, name='rove_ui.py', args=('--browser',), extra_env=None):
        command = ['/bin/zsh' if name.endswith('.command') else sys.executable,
                   str(self.root / name), *args]
        result = subprocess.run(command, cwd='/', env={**os.environ, **(extra_env or {})},
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        return result

    def test_python_runs_setup_once_then_opens_ui_with_arguments(self):
        for _ in range(2):
            result = self.run_entry(args=('--browser', 'argument with spaces'))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.root / 'setups.txt').read_text(), '1')
        self.assertEqual(json.loads((self.root / 'opened.json').read_text()),
                         ['--browser', 'argument with spaces'])

    def test_failed_setup_stops_before_opening_ui_without_waiting_for_input(self):
        result = self.run_entry(extra_env={'ROVE_TEST_SETUP_EXIT': '17'})
        self.assertEqual(result.returncode, 17, result.stdout + result.stderr)
        self.assertFalse((self.root / 'opened.json').exists())

    def test_missing_runtime_is_repaired_on_the_next_launch(self):
        first = self.run_entry('Rove UI.command')
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        (self.root / 'rust-client/target/release/headless-luau').unlink()
        second = self.run_entry('Start.command')
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual((self.root / 'setups.txt').read_text(), '2')

    def test_direct_python_launcher_bootstraps_and_reexecutes_in_venv(self):
        result = self.run_entry('scripts/launcher.py', ('--port', '0'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads((self.root / 'opened.json').read_text()), ['--port', '0'])
        self.assertEqual((self.root / 'setups.txt').read_text(), '1')

    def test_help_does_not_install_or_open_anything(self):
        result = self.run_entry(args=('--help',))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Fixture launcher help', result.stdout)
        self.assertFalse((self.root / 'setups.txt').exists())
        self.assertFalse((self.root / 'opened.json').exists())


class UIReadinessTests(unittest.TestCase):
    def test_setup_recognizes_bundles_in_the_single_ui_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for path in runtime_files(root).values():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            with patch.object(launcher_setup, 'read_private', side_effect=FileNotFoundError):
                status = launcher_setup.readiness(root)
            self.assertTrue(status['runtimeReady'], status)
            self.assertFalse(status['accountConfigured'])

    def test_replacement_ui_preserves_an_active_launcher_on_an_older_port(self):
        import desktop_backend
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(desktop_backend,'ROOT',Path(folder)), \
                 patch.object(desktop_backend,'ready',side_effect=[None,None,None,'http://127.0.0.1:5470/']), \
                 patch('socket.socket') as socket, \
                 patch.object(desktop_backend.urllib.request,'urlopen',return_value=io.BytesIO(b'{"state":"playing"}')) as request, \
                 patch.object(desktop_backend.subprocess,'Popen') as spawn:
                socket.return_value.__enter__.return_value.bind.side_effect=[OSError('occupied'),None]
                self.assertEqual(desktop_backend.ensure_backend(),'http://127.0.0.1:5470/')
                self.assertEqual(spawn.call_args.args[0][-2:],['--attach','http://127.0.0.1:5468'])
                request.assert_called_once_with('http://127.0.0.1:5468/api/status',timeout=.5)


if __name__ == '__main__':
    unittest.main()
