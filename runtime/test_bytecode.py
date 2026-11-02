"""Tests for trusted, locally compiled Luau bytecode loading only."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

RUNTIME = Path(__file__).resolve().parent
BUILD = RUNTIME / 'build'
HOST = BUILD / 'luau-host'
FIXTURE_COMPILER = BUILD / 'luau-source-to-bytecode'


@unittest.skipUnless(HOST.is_file() and FIXTURE_COMPILER.is_file(),
                     'build the local Luau host first')
class BytecodeLoaderTests(unittest.TestCase):
    def compile_fixture(self, directory, source='local a,b=...; return a+b,nil,true,"fixture"'):
        source_path = Path(directory) / 'fixture.luau'
        path = Path(directory) / 'fixture.luac'
        source_path.write_text(source, encoding='utf-8')
        subprocess.run([str(FIXTURE_COMPILER), str(source_path), str(path)], check=True)
        return path

    def invoke(self, path, *extra):
        result = subprocess.run([str(HOST), '--bytecode', str(path), *extra],
                                capture_output=True, check=False)
        return result.returncode, json.loads(result.stdout)

    def test_locally_compiled_fixture_runs_and_returns_scalars(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.compile_fixture(directory)
            code, result = self.invoke(path, '--', '2', '5')
        self.assertEqual(code, 0)
        self.assertEqual(result, {'ok': True, 'values': [7, None, True, 'fixture']})

    def test_unknown_header_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.compile_fixture(directory)
            path.write_bytes(b'\xff' + path.read_bytes()[1:])
            code, result = self.invoke(path)
        self.assertEqual(code, 2)
        self.assertFalse(result['ok'])
        self.assertIn('unsupported Luau bytecode version', result['error'])

    def test_truncated_known_version_is_rejected_by_vm_loader(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.compile_fixture(directory)
            path.write_bytes(path.read_bytes()[:2])
            code, result = self.invoke(path)
        self.assertEqual(code, 0)
        self.assertFalse(result['ok'])
        self.assertNotEqual(result['error'], '')

    def test_encoded_roblox_envelope_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'envelope.bin'
            path.write_bytes(b'RSB1' + bytes(24))
            code, result = self.invoke(path)
        self.assertEqual(code, 2)
        self.assertFalse(result['ok'])

    def test_compiled_engine_fixture_runs_with_explicit_local_model(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.compile_fixture(directory,
                'local w=game:GetService("Workspace"); return w.Parent==game,w:IsA("Model"),game.JobId')
            context = Path(directory) / 'context.txt'
            context.write_text('mode=offline\njob_id=offline-fixture-job\n'
                               'is_studio=false\nservices=Workspace,RunService\n', encoding='ascii')
            code, result = self.invoke(path, '--context-file', str(context))
        self.assertEqual(code, 0)
        self.assertEqual(result, {'ok': True, 'values': [True, True, 'offline-fixture-job']})


if __name__ == '__main__':
    unittest.main()
