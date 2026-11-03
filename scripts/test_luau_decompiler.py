"""Owned source -> real bytecode -> Tovek -> compile/behavior/export checks."""
import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest

from persistent_runtime import PersistentLuauHost
from run_ussi import ExportSession, run_export
from luau_decompiler import BINARY, decompile_bytecode, prepare_sources
from validate_ussi_export import parse_export

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = json.loads((ROOT/'runtime/tests/ussi_snapshot.json').read_text())
CASES = {
 'branches': 'local function f(x) if x>2 then return x*3 else return x+1 end end;return f(5),f(1)',
 'loops': 'local n=0;for i=1,8 do if i%2==0 then n+=i end end;local x=3;while x>0 do n+=x;x-=1 end;return n',
 'closures': 'local function factory() local n=0;return function(d)n+=d;return n end end;local a,b=factory(),factory();return a(2),a(5),b(3)',
 'multiple_returns': 'local function f(...)return select("#",...),... end;local n,a,b,c=f(1,nil,3);return n,a,b,c',
 'tables_methods': 'local t={n=4};function t:add(x)self.n+=x;return self.n end;return t:add(3),t:add(2)',
 'generic_loop': 'local t={2,4,6};local n=0;for i,v in ipairs(t) do n+=i*v end;return n',
 'recursion': 'local function factorial(n)if n<=1 then return 1 end;return n*factorial(n-1) end;return factorial(6)',
 'short_circuit': 'local hits=0;local function f()hits+=1;return 8 end;local a=false and f();local b=true or f();local c=nil or f();return a,b,c,hits',
 'repeat_loop': 'local n=0;repeat n+=1 until n==5;return n',
 'metamethod': 'local t=setmetatable({n=5},{__add=function(a,b)return a.n+b.n end});return t+t',
 'binary_strings': 'local s="A\\000\\255\\128z";return #s,string.byte(s,2,4)',
 'closed_upvalues': 'local out={};for i=1,3 do local x=i;out[i]=function()x+=10;return x end end;return out[1](),out[2](),out[1]()',
}


class DecompilerTests(unittest.TestCase):
    def setUp(self):
        self.host = PersistentLuauHost(copy.deepcopy(SNAPSHOT))
        self.addCleanup(self.host.close)
        self.session = ExportSession(host=self.host)

    def compile(self, source):
        result = self.session.execute('local m=Instance.new("ModuleScript");m.Source='+json.dumps(source)+';return crypt.base64encode(getscriptbytecode(m))')
        return base64.b64decode(result['values'][0])

    def validate(self, source):
        result = self.session.execute('local f,e=loadstring('+json.dumps(source)+');return f~=nil,e')
        if not result['values'][0]:
            raise ValueError('Recovered output does not compile')

    def run_source(self, source):
        return self.session.execute('return assert(loadstring('+json.dumps(source)+'))()')['values']

    @unittest.skipUnless(BINARY.is_file(), 'build the optional Tovek decompiler first')
    def test_owned_semantic_roundtrips(self):
        for name, source in CASES.items():
            with self.subTest(name=name):
                recovered = decompile_bytecode(self.compile(source))
                self.validate(recovered)
                self.assertEqual(self.run_source(recovered), self.run_source(source))

    @unittest.skipUnless(BINARY.is_file(), 'build the optional Tovek decompiler first')
    def test_actual_ussi_uses_recovered_bytecode_source(self):
        original = CASES['closures']
        bytecode = self.compile(original)
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['script_sources'] = {'fixture:source': {'bytecode_hex':bytecode.hex(), 'signature_verified':True}}
        # Use the actual fixture ModuleScript identity rather than a made-up path.
        script = next(n for n in snapshot['nodes'] if n['name']=='SourceProbe')
        snapshot['script_sources'] = {script['id']: snapshot['script_sources']['fixture:source']}
        sources, report = prepare_sources(snapshot, self.validate, object_path='Workspace/ExportFixture')
        self.assertEqual(report['recovered'], 1, report)
        self.assertFalse(report['failed'])
        host = PersistentLuauHost(snapshot)
        try:
            session = ExportSession(host=host)
            session.decompiled_sources, session.decompilation_report = sources, report
            with tempfile.TemporaryDirectory() as temp:
                for suffix in ('rbxm', 'rbxmx'):
                    output = Path(temp)/('fixture.'+suffix)
                    run_export(session, output, object_path='Workspace/ExportFixture')
                    script = next(n for n in parse_export(output)['instances'] if n['class']=='ModuleScript')
                    source = script['properties']['Source']
                    self.assertIn('Reconstructed locally by Tovek', source)
                    self.assertEqual(self.run_source(source), self.run_source(original))
        finally:
            host.close()

    def test_unverified_and_bad_bytecode_never_become_cached_source(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['script_sources'] = {
            'unverified': {'bytecode_hex':self.compile('return 5').hex()},
            'broken': {'bytecode_hex': '0d03ff', 'signature_verified':True},
            'authored': {'source':'return 17'},
        }
        sources, report = prepare_sources(snapshot, self.validate)
        self.assertFalse(sources)
        self.assertEqual(report['unavailable'], 1)
        self.assertEqual(report['preserved_source'], 1)
        self.assertEqual(len(report['failed']), 1)
        with self.assertRaises(ValueError):
            decompile_bytecode(b'\x09\x03garbage')


if __name__ == '__main__':
    unittest.main()
