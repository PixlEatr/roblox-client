"""Focused integration checks for the actual persistent Luau worker."""
import copy
import json
from pathlib import Path
import unittest

from local_runtime import RUST_HOST
from persistent_runtime import PersistentLuauHost
ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = json.loads((ROOT / 'tests/fixtures/world_snapshot.json').read_text())


@unittest.skipUnless(RUST_HOST.is_file(), 'build the Rust host first')
class SourceRuntimeConformanceTests(unittest.TestCase):
    def setUp(self):
        self.host = PersistentLuauHost(copy.deepcopy(SNAPSHOT))
        self.addCleanup(self.host.close)

    def run_luau(self, source):
        result = self.host.call({'op': 'execute', 'source': source, 'timeout_ms': 1000})
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['completed'], result)
        return result['values']

    def test_pcall_xpcall_keep_varargs_and_transform_errors(self):
        values = self.run_luau(r'''
local ok, first, middle, last = pcall(function(a,b)
    return a + b, nil, "tail"
end, 12, 30)
local failed, message, tag = xpcall(function()
    error("expected failure")
end, function(err)
    return "handled: " .. tostring(err)
end)
return ok, first, middle, last, failed,
    string.find(message, "handled:", 1, true) ~= nil, type(tag)
''')
        self.assertEqual(values, [True, 42, None, 'tail', False, True, 'nil'])

    def test_coroutine_wrap_yield_resume_and_return_values(self):
        values = self.run_luau(r'''
local run = coroutine.wrap(function(value)
    local sent = coroutine.yield(value + 1)
    return "finished", sent
end)
local first = run(40)
local second, third = run("reply")
local ok, errorValue = pcall(run)
return first, second, third, ok, type(errorValue) == "string"
''')
        self.assertEqual(values, [41, 'finished', 'reply', False, True])

    def test_wrapped_coroutine_state_survives_across_source_jobs(self):
        first = self.host.call({'op': 'execute', 'source': r'''
getgenv().resumeLater = coroutine.wrap(function()
    local sent = coroutine.yield("paused")
    return "resumed", sent
end)
return resumeLater()
''', 'timeout_ms': 1000})
        self.assertTrue(first['ok'], first)
        self.assertEqual(first['values'], ['paused'])
        second = self.run_luau('return resumeLater("payload")')
        self.assertEqual(second, ['resumed', 'payload'])

    def test_table_freeze_clone_and_readonly_semantics(self):
        values = self.run_luau(r'''
local original = {child={value=9}, key="kept"}
table.freeze(original)
local copy = table.clone(original)
local sourceWrite = pcall(function() original.key="changed" end)
copy.key = "copy"
copy.extra = true
return table.isfrozen(original), table.isfrozen(copy), sourceWrite,
    original.key, copy.key, original.child == copy.child, copy.extra
''')
        self.assertEqual(values, [True, False, False, 'kept', 'copy', True, True])

    def test_buffer_binary_reads_writes_and_copies(self):
        values = self.run_luau(r'''
local b = buffer.create(8)
buffer.writeu8(b, 0, 0xFE)
buffer.writei16(b, 1, -321)
buffer.writef32(b, 3, 3.5)
local copy = buffer.create(6)
buffer.copy(copy, 0, b, 1, 6)
local bytes = buffer.tostring(copy)
return buffer.len(b), buffer.readu8(b,0), buffer.readi16(b,1),
    buffer.readf32(b,3), buffer.len(copy), buffer.readi16(copy,0),
    string.sub(bytes, 1, 2) == string.char(0xBF, 0xFE)
''')
        self.assertEqual(values[:3], [8, 254, -321])
        self.assertAlmostEqual(values[3], 3.5)
        self.assertEqual(values[4:], [6, -321, True])

    def test_utf8_iterates_unicode_codepoints_and_byte_offsets(self):
        values = self.run_luau(r'''
local text = "Aλ😀"
local count = utf8.len(text)
local codepoints = {}
for position, codepoint in utf8.codes(text) do
    codepoints[#codepoints+1] = {position, codepoint}
end
return count, #codepoints, codepoints[1][1], codepoints[2][1],
    codepoints[3][1], codepoints[2][2], utf8.char(65,955,128512) == text,
    utf8.offset(text, 3)
''')
        self.assertEqual(values, [3, 3, 1, 2, 4, 955, True, 4])

    def test_loadstring_chunk_uses_persistent_globals_and_retains_environment(self):
        first = self.run_luau(r'''
getgenv().loadedRuns = 5
local chunk, problem = loadstring("loadedRuns += 1; return loadedRuns", "=persistent-check")
assert(chunk, problem)
getgenv().loadedChunk = chunk
return chunk(), getfenv(chunk) == getgenv()
''')
        self.assertEqual(first, [6, True])
        second = self.run_luau('return loadedChunk(), loadedRuns, getfenv(loadedChunk) == getgenv()')
        self.assertEqual(second, [7, 7, True])


if __name__ == '__main__':
    unittest.main()
