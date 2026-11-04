"""Rust supervisor, pinned VM parity, and actual local-console integration."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from local_runtime import HOST, RUST_HOST
from script_console import ScriptConsole
from script_runtime import ClientScriptRuntime
from test_world_snapshot import fixture

SNAPSHOT = json.loads((ROOT / 'tests/fixtures/world_snapshot.json').read_text())


@unittest.skipUnless(RUST_HOST.is_file(), 'cargo build --release in rust-client first')
class RustHostTests(unittest.TestCase):
    def execute(self, source, *, snapshot=None, extra=(), host=RUST_HOST):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.luau'
            path.write_text(source)
            command = [str(host), '--source', str(path)]
            if snapshot is not None:
                world = Path(directory) / 'world.json'
                world.write_text(json.dumps(snapshot, allow_nan=False))
                command += ['--world-snapshot', str(world)]
            run = subprocess.run([*command, *extra], capture_output=True, timeout=6)
            return run.returncode, json.loads(run.stdout)

    def test_actual_luau_source_compilation_and_arguments(self):
        code, result = self.execute('local a,b=...; return a+b,nil,true,"source ran"',
                                    extra=['--', '2', '5'])
        self.assertEqual(code, 0)
        self.assertEqual(result['values'], [7, None, True, 'source ran'])

    def test_unicode_source_and_snapshot_paths_in_both_hosts(self):
        with tempfile.TemporaryDirectory(prefix='rove-path-') as directory:
            root=Path(directory)/'測試 folder';root.mkdir()
            source=root/'привет.luau';world=root/'世界.json'
            source.write_text('return "héllo 🌍", workspace.ClassName',encoding='utf-8')
            world.write_text(json.dumps(SNAPSHOT),encoding='utf-8')
            for host in (HOST,RUST_HOST):
                with self.subTest(host=host.name):
                    run=subprocess.run([str(host),'--source',str(source),'--world-snapshot',str(world)],
                                       capture_output=True,timeout=8)
                    self.assertEqual(run.returncode,0,run.stderr)
                    self.assertEqual(json.loads(run.stdout)['values'],['héllo 🌍','Workspace'])

    def test_world_behavior_matches_existing_pinned_host(self):
        source = (ROOT / 'tests/fixtures/world_script.luau').read_text()
        code, result = self.execute(source, snapshot=SNAPSHOT)
        old_code, old_result = self.execute(source, snapshot=SNAPSHOT, host=HOST)
        self.assertEqual(code, 0)
        self.assertEqual(old_code, 0)
        # The Rust source host additionally creates PlayerScripts and executes
        # its bootstrap. Compare world semantics independent of local IDs and
        # bootstrap telemetry, while still asserting clean integrity reports.
        def normalize(report):
            report = copy.deepcopy(report)
            for key in ('runtime_protected_integrity', 'runtime_thread_integrity'):
                self.assertEqual(report.pop(key)['failures'], 0)
            for key in ('console_events', 'teleport_queue'):
                self.assertEqual(report.pop(key, []), [])
            report['mutations'] = [m for m in report['mutations']
                if not (m['kind'] == 'create' and m['node']['class_name'] == 'PlayerScripts')]
            ids = {m['node']['id']: f'created:{i}' for i,m in enumerate(report['mutations'])
                   if m['kind'] == 'create'}
            def visit(value):
                if isinstance(value, str): return ids.get(value, value)
                if isinstance(value, list): return [visit(v) for v in value]
                if isinstance(value, dict): return {k:visit(v) for k,v in value.items()}
                return value
            return visit(report)
        result, old_result = normalize(result), normalize(old_result)
        self.assertEqual(result, old_result)

    def test_runtime_error_preserves_completed_local_writes(self):
        code, result = self.execute('local p=Instance.new("Part"); p.Anchored=true; error("stopped")',
                                    snapshot=SNAPSHOT)
        self.assertEqual(code, 1)
        self.assertFalse(result['ok'])
        self.assertEqual(result['mutations'][-1]['value'], True)
        self.assertIn('stopped', result['error'])

    def test_deadline_stops_loop_even_inside_pcall(self):
        started = time.monotonic()
        code, result = self.execute('pcall(function() while true do end end); return "escaped"',
                                    extra=['--timeout-ms', '25'])
        self.assertEqual(code, 1)
        self.assertTrue(result['timed_out'])
        self.assertLess(time.monotonic() - started, 3)

    def test_both_supervisors_stop_a_loop_and_run_the_next_script(self):
        for host in (HOST,RUST_HOST):
            with self.subTest(host=host.name):
                _,result=self.execute('while true do end',host=host,extra=['--timeout-ms','25'])
                self.assertFalse(result['ok'])
                self.assertTrue(result['timed_out'])
                code,result=self.execute('return 17',host=host)
                self.assertEqual(code,0)
                self.assertEqual(result['values'],[17])

    def test_invalid_or_oversized_input_rejected(self):
        for source, snapshot in [('x' * (128 * 1024 + 1), None), ('return true', {'version': 90})]:
            code, result = self.execute(source, snapshot=snapshot)
            self.assertNotEqual(code, 0)
            self.assertFalse(result['ok'])

    def test_new_vm_does_not_leak_globals(self):
        self.assertTrue(self.execute('privateGlobal=42; return privateGlobal')[1]['ok'])
        result = self.execute('return privateGlobal,io,require,os.exit')[1]
        self.assertEqual(result['values'], [None, None, None, None])

    def test_loadstring_environment_and_closure_helpers_execute_real_source(self):
        code, result = self.execute('''
getgenv().authoredValue=40
shared.count=2
_G.fixture="inside one command"
local f,compileError=loadstring("local x=...; return authoredValue+x", "=authored chunk")
assert(f and not compileError)
local bad,message=loadstring("local =", "=bad chunk")
return f(shared.count),bad==nil,type(message),iscclosure(loadstring),
    islclosure(f),getgenv()~=getfenv(0),_G.fixture
''')
        self.assertEqual(code, 0)
        self.assertEqual(result['values'], [42, True, 'string', True, True, True, 'inside one command'])

    def test_rust_base64_aliases_preserve_binary_and_reject_malformed_input(self):
        code, result = self.execute('''
local binary=string.char(0,255,254).."hello"
local encoded=base64.encode(binary)
assert(crypt.base64.decode(encoded)==binary)
assert(crypt.base64encode(binary)==encoded)
assert(crypt.base64_encode(binary)==encoded)
assert(crypt.base64decode(encoded)==binary)
assert(crypt.base64_decode(encoded)==binary)
assert(base64encode(binary)==encoded and base64_encode(binary)==encoded)
assert(base64decode(encoded)==binary and base64_decode(encoded)==binary)
local malformed=pcall(base64.decode,"??")
return base64.encode("hello"),base64.decode(""),malformed
''')
        self.assertEqual(code, 0)
        self.assertEqual(result['values'], ['aGVsbG8=', '', False])

    def test_rust_zstd_roundtrip_aliases_and_bounds(self):
        import base64
        import zstandard
        # Independent encoder: output limit is 32 MiB; input limit is 256 KiB.
        expanding = base64.b64encode(zstandard.ZstdCompressor().compress(b'A'*(32*1024*1024+1))).decode()
        code, result = self.execute('local overflowFrame=base64.decode('+json.dumps(expanding)+')\n'+'''
local binary=string.char(0,255,254).."hello"
local compressed=zstdcompress(binary)
assert(compressed~=binary and zstddecompress(compressed)==binary)
assert(crypt.zstdcompress(binary)==compressed)
assert(crypt.zstddecompress(compressed)==binary)
assert(zstddecompress(zstdcompress(""))=="")
local limit=string.rep("L",256*1024)
assert(zstddecompress(zstdcompress(limit))==limit)
local malformed=pcall(zstddecompress,"not zstd")
local truncated=pcall(zstddecompress,string.sub(compressed,1,#compressed-1))
local oversizedInput=pcall(zstdcompress,string.rep("x",256*1024+1))
local oversizedCompressed=pcall(zstddecompress,string.rep("x",256*1024+1))
local acceptedFrame=base64.decode("KLUv/aABAAQAVAAAEEFBAQD7/znAAgIAEEEJAABB")
assert(#zstddecompress(acceptedFrame)==256*1024+1)
local oversizedOutput,message=pcall(zstddecompress,overflowFrame)
return #compressed>0,malformed,truncated,oversizedInput,oversizedCompressed,
    oversizedOutput,type(message)
''')
        self.assertEqual(code, 0)
        self.assertEqual(result['values'], [True, False, False, False, False, False, 'string'])

    def test_crypt_sha256_and_hmac_are_binary_safe(self):
        import hashlib
        import hmac
        code, result = self.execute('''
local binary=string.char(0,255,254).."hello"
local longKey=string.rep("k",100)
return crypt.hash("abc","sha256"),
    crypt.hmac("The quick brown fox jumps over the lazy dog","key","sha-256"),
    hmac(binary,longKey,"sha256"),
    crypt.hash(binary,"sha256"),
    (pcall(crypt.hash,"abc","unsupported-hash"))
''')
        self.assertEqual(code, 0)
        binary = b'\x00\xff\xfehello'
        self.assertEqual(result['values'], [
            hashlib.sha256(b'abc').hexdigest(),
            hmac.new(b'key', b'The quick brown fox jumps over the lazy dog', hashlib.sha256).hexdigest(),
            hmac.new(b'k' * 100, binary, hashlib.sha256).hexdigest(),
            hashlib.sha256(binary).hexdigest(),
            False,
        ])

    def test_loaded_source_obeys_same_deadline(self):
        code, result = self.execute('assert(loadstring("while true do end"))()',
                                    extra=['--timeout-ms', '25'])
        self.assertEqual(code, 1)
        self.assertTrue(result['timed_out'])

    def test_rust_submit_runs_real_scripts_and_persists_local_effects(self):
        world, _ = fixture()
        original = copy.deepcopy(world.instances)
        runtime = ClientScriptRuntime(lambda: world, 123)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'console.sock'
            console = ScriptConsole(path, runtime.execute)
            try:
                for source, expected in [
                    ('local p=Instance.new("Part",workspace); p.Name="RustConsolePart"; '
                     'p.Anchored=true; print("Rust console source"); return p.Anchored', [True]),
                    ('local p=workspace:FindFirstChild("RustConsolePart"); '
                     'return p.Anchored,client.propertyOrigin(p,"Anchored")', [True, 'local']),
                ]:
                    script = Path(directory) / 'test.luau'
                    script.write_text(source)
                    process = subprocess.Popen([str(RUST_HOST), 'submit', '--script', str(script),
                                                '--socket', str(path)], stdout=subprocess.PIPE,
                                               stderr=subprocess.PIPE)
                    deadline = time.monotonic() + 6
                    try:
                        while process.poll() is None and time.monotonic() < deadline:
                            console.poll()
                            time.sleep(.002)
                        output, error = process.communicate(timeout=1)
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.wait()
                    self.assertEqual(process.returncode, 0, error)
                    result = json.loads(output)
                    self.assertEqual(result['values'], expected)
                    self.assertFalse(result['mutations_replicated_to_server'])
                self.assertEqual(console.requests, 2)
                self.assertEqual(world.instances, original)
            finally:
                console.close()
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
