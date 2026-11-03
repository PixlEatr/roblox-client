"""Exhaustive Opium API registration conformance in the real Rust world host."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from local_runtime import RUST_HOST

INVENTORY = json.loads((ROOT / 'runtime/executor-source-inventory.json').read_text())
SNAPSHOT = json.loads((ROOT / 'runtime/tests/world_snapshot.json').read_text())
LIBRARY_GLOBALS = {
    'CacheLib': 'cache',
    'DebugLib': 'debug',
    'HttpLib': 'http',
    'Base64Lib': 'base64',
    'WSLib': 'WebSocket',
    'FsLib': 'fs',
    'CryptLib': 'crypt',
}
TABLE_GLOBALS = {'Drawing', 'shared', '_G'}
ALIASES = (
    ('base64encode', 'base64_encode'),
    ('base64decode', 'base64_decode'),
    ('getexecutorname', 'identifyexecutor'),
    ('queueonteleport', 'queue_on_teleport'),
    ('toclipboard', 'setclipboard'),
)


def quoted(value):
    return json.dumps(value, ensure_ascii=True)


@unittest.skipUnless(RUST_HOST.is_file(), 'cargo build --release in rust-client first')
class OpiumApiSurfaceTests(unittest.TestCase):
    def execute(self, source):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            script = path / 'surface.luau'
            snapshot = path / 'world.json'
            script.write_text(source)
            snapshot.write_text(json.dumps(SNAPSHOT, allow_nan=False))
            run = subprocess.run(
                [str(RUST_HOST), '--source', str(script), '--world-snapshot', str(snapshot),
                 '--timeout-ms', '1000'],
                capture_output=True, timeout=6,
            )
        try:
            result = json.loads(run.stdout)
        except json.JSONDecodeError as error:
            self.fail(f'Rust host returned invalid JSON (exit {run.returncode}): '
                      f'{run.stdout!r}; stderr: {run.stderr!r}; {error}')
        self.assertEqual(run.returncode, 0, result)
        self.assertTrue(result['ok'], result)
        self.assertFalse(result.get('task_errors'), result)
        return result['values']

    def test_all_inventory_globals_libraries_and_aliases(self):
        globals_ = INVENTORY['global_registration_candidates']
        libraries = INVENTORY['library_registration_candidates']
        self.assertEqual(len(globals_), 299, 'the Opium global inventory must remain exhaustive')
        self.assertEqual(len(set(globals_)), len(globals_), 'duplicate global inventory entries')
        self.assertEqual(set(libraries), set(LIBRARY_GLOBALS),
                         'all seven Opium library inventories must remain covered')

        source = [
            'local environment=getfenv(0)',
            'local failures={}',
            'local function expect(path,expected,value)',
            ' local actual=type(value)',
            ' if actual~=expected then',
            '  table.insert(failures,path..": expected "..expected..", got "..actual)',
            ' end',
            'end',
            'local function global(name) return rawget(environment,name) end',
        ]
        for name in globals_:
            expected = 'table' if name in TABLE_GLOBALS else 'function'
            source.append(f'expect("global {name}",{quoted(expected)},global({quoted(name)}))')
        for inventory_name, global_name in LIBRARY_GLOBALS.items():
            source.append(f'expect("library {global_name}","table",global({quoted(global_name)}))')
            for member in libraries[inventory_name]:
                path = f'{global_name}.{member}'
                source.append(
                    f'do local library=global({quoted(global_name)}); '
                    f'expect("library member {path}","function",'
                    f'type(library)=="table" and rawget(library,{quoted(member)}) or nil) end'
                )
        for left, right in ALIASES:
            source.append(
                f'if global({quoted(left)})~=global({quoted(right)}) then '
                f'table.insert(failures,"alias {left} == {right}: expected exact function identity") end'
            )
        source.append('return #failures,table.concat(failures,"\\n")')
        count, report = self.execute('\n'.join(source))
        self.assertIsInstance(report, str, report)
        self.assertEqual(count, len(report.splitlines()) if report else 0)
        if count:
            self.fail(f'Opium API surface has {count} registration gaps:\n' +
                      '\n'.join(f' - {failure}' for failure in report.splitlines()))
        self.assertEqual(report, '')


if __name__ == '__main__':
    unittest.main()
