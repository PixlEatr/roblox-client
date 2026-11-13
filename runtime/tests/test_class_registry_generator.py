#!/usr/bin/env python3
"""Build-time byte-slice scanner checks; this is not a runtime dependency."""
import importlib.util
import pathlib
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts_generate_class_registry.py"
SPEC = importlib.util.spec_from_file_location("class_registry_generator", SCRIPT)
GENERATOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(GENERATOR)


class RegistrySliceTests(unittest.TestCase):
    def test_escaped_name_utf8_offsets_and_nested_values(self):
        raw = (
            b'{"ignored":{"x":[1,{"quote":"\\\""}]},"classes":'
            b'{"C\\u00e9":{"parent":"Base", "properties":{"X":'
            b'{"value":"caf\xc3\xa9","nested":[1,{"deep":true}]}}}}}'
        )
        root, _ = GENERATOR.object_members(raw, 0)
        start, end = next((start, end) for key, start, end in root if key == "classes")
        classes, _ = GENERATOR.object_members(raw, start)
        name, value_start, value_end = classes[0]
        self.assertEqual(name, "C\u00e9")
        self.assertEqual(raw[value_start:value_end],
                         b'{"parent":"Base", "properties":{"X":{"value":"caf\xc3\xa9","nested":[1,{"deep":true}]}}}')
        import json
        self.assertEqual(json.loads(raw[value_start:value_end]), {
            "parent": "Base", "properties": {"X": {"value": "caf\u00e9", "nested": [1, {"deep": True}]}}
        })
        self.assertEqual(value_start, raw.index(b'{"parent"'))
        self.assertGreater(value_end, value_start)

    def test_parent_null_is_distinct_from_missing_parent(self):
        raw = b'{"classes":{"NullParent":{"parent":null},"MissingParent":{}}}'
        entries, parents, _ = GENERATOR.registry_indexes(raw)
        self.assertEqual([name for name, _, _ in entries], ["MissingParent", "NullParent"])
        self.assertEqual(parents, [("NullParent", None)])


if __name__ == "__main__":
    unittest.main()
