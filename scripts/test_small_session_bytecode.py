"""Short unsigned session modules: real compiler/VM fixtures, no fake signatures."""
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from bytecode_container import _inspector
from persistent_runtime import PersistentLuauHost
from script_catalog import prepare_script, script_catalog, CodePreparationQueue
from test_bytecode_container import container_fixture
from test_script_execution import snapshot, add_script
from test_world_snapshot import fixture

ROOT = Path(__file__).resolve().parents[1]
COMPILER = ROOT/'runtime/build/luau-source-to-bytecode'


def compiled(source):
    with tempfile.TemporaryDirectory() as folder:
        src, out = Path(folder)/'source.luau', Path(folder)/'code'
        src.write_text(source)
        subprocess.run([str(COMPILER), str(src), str(out)], check=True,
                       capture_output=True, timeout=5)
        plain = out.read_bytes()
    module, _ = _inspector().inspect(plain, '1')
    encoded = bytearray(plain)
    for prototype in module['prototypes']:
        for instruction in prototype['instructions']:
            pos = prototype['code_offset'] + instruction['pc']*4
            encoded[pos] = instruction['opcode']*227 & 255
    return plain, bytes(encoded)


@unittest.skipUnless(COMPILER.is_file(), 'build the local fixture compiler')
class SmallSessionBytecodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plain, cls.encoded = compiled('return {answer=42}')
        cls.container = container_fixture(cls.encoded)

    def prepare(self, blob=None, **kwargs):
        return prepare_script({'Bytecode': self.container if blob is None else blob}, **kwargs)

    def test_requires_explicit_session_policy_and_never_claims_signature(self):
        self.assertNotIn('bytecode_hex', self.prepare())
        entry = self.prepare(allow_small_unsigned=True)
        self.assertEqual(bytes.fromhex(entry['bytecode_hex']), self.plain)
        self.assertIs(entry['signature_verified'], False)
        self.assertEqual(entry['provenance'], 'session_small_bytecode')
        self.assertNotIn('bytecode_hex', self.prepare(allow_small_unsigned=1))

    def test_requires_valid_container_and_full_structure_without_trailer(self):
        damaged = bytearray(self.container); damaged[-1] ^= 1
        for blob in (bytes(damaged), self.encoded,
                     container_fixture(self.encoded+b'X'*24),
                     container_fixture(self.encoded[:-1]),
                     container_fixture(b'\0compiler error')):
            with self.subTest(length=len(blob)):
                self.assertNotIn('bytecode_hex', self.prepare(blob, allow_small_unsigned=True))

    def test_larger_unsigned_module_still_rejected(self):
        _, encoded = compiled('return "'+'a'*300+'"')
        self.assertGreaterEqual(len(encoded), 256)
        self.assertNotIn('bytecode_hex', self.prepare(container_fixture(encoded), allow_small_unsigned=True))

    def test_policy_change_invalidates_catalog_cache(self):
        world, _ = fixture()
        world.schema['classes'].append({'name':'ModuleScript','properties':[]})
        world.instances[(1,20)] = {'class':len(world.schema['classes'])-1,
                                  'properties':{'Bytecode':self.container}}
        self.assertNotIn('bytecode_hex', script_catalog(world)['1:20'])
        world.allow_small_unsigned_scripts = True
        self.assertEqual(script_catalog(world)['1:20']['provenance'], 'session_small_bytecode')
        world.allow_small_unsigned_scripts = False
        self.assertNotIn('bytecode_hex', script_catalog(world)['1:20'])

    def test_background_preparation_preserves_session_policy(self):
        world, _ = fixture()
        world.schema['classes'].append({'name':'ModuleScript','properties':[]})
        world.instances[(1,20)] = {'class':len(world.schema['classes'])-1,
                                  'properties':{'Bytecode':self.container}}
        world.allow_small_unsigned_scripts = True
        world._script_preparation_queue = CodePreparationQueue()
        self.addCleanup(world._script_preparation_queue.close)
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            entry = script_catalog(world)['1:20']
            if entry.get('reason') != 'verification_pending': break
            time.sleep(.005)
        self.assertEqual(entry.get('provenance'), 'session_small_bytecode', entry)

    def test_actual_vm_requires_module_and_preserves_cached_table(self):
        data = snapshot(); add_script(data,'ShortModule',None)
        data['script_sources']['ShortModule'] = self.prepare(allow_small_unsigned=True)
        host = PersistentLuauHost(data); self.addCleanup(host.close)
        result = host.call({'op':'execute','source':
            'local m=game:GetService("ReplicatedStorage").ShortModule; '
            'local a=require(m);return a.answer, a==require(m)', 'timeout_ms':1000})
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [42, True])

    def test_worker_rejects_missing_admission_or_oversized_short_claim(self):
        base = self.prepare(allow_small_unsigned=True)
        for change in ({'admission':''}, {'provenance':''},
                       {'bytecode_hex':base['bytecode_hex']+'00'*256}):
            entry = {**base, **change}
            data = snapshot(); add_script(data,'ShortModule',None)
            data['script_sources']['ShortModule'] = entry
            with self.assertRaisesRegex(RuntimeError, 'unverified or oversized'):
                PersistentLuauHost(data)


if __name__ == '__main__': unittest.main()
