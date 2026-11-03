"""Offline provenance and enum parity check for the USSI enum slice."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
META = ROOT / "runtime/tests/references/enums"
SOURCE = (ROOT / "runtime/export_enums.inc").read_text()


def parse_items(filename):
    text = (META / filename).read_text()
    names = re.findall(r"^  - name: (.+)$", text, re.M)
    values = list(map(int, re.findall(r"^    value: (\d+)$", text, re.M)))
    if len(names) != len(values):
        raise AssertionError(f"unpaired enum names/values in {filename}")
    return list(zip(names, values))


class UssiEnumMetadataTests(unittest.TestCase):
    def test_pinned_source_hashes_and_all_items_are_present(self):
        provenance = json.loads((META / "creator-docs-provenance.json").read_text())
        self.assertEqual(provenance["repository"], "https://github.com/Roblox/creator-docs")
        self.assertRegex(provenance["resolved_commit"], r"^[0-9a-f]{40}$")
        for enum_name, filename in (("SecurityCapability", "SecurityCapability.yaml"),
                                    ("Material", "Material.yaml"),
                                    ("CollisionFidelity", "CollisionFidelity.yaml")):
            items = parse_items(filename)
            self.assertEqual(len(items), len({name for name, _ in items}))
            self.assertEqual(len(items), len({value for _, value in items}))
            block = SOURCE.split(f"kUsi{enum_name}Items[] = {{", 1)[1].split("};", 1)[0]
            actual = [(name, int(value)) for name, value in re.findall(r'\{"([^\"]+)", (\d+)\}', block)]
            self.assertEqual(actual, items, enum_name)
            self.assertEqual(provenance["files"][filename], __import__("hashlib").sha256((META / filename).read_bytes()).hexdigest())

    def test_known_export_startup_values(self):
        materials = dict(parse_items("Material.yaml"))
        collision = dict(parse_items("CollisionFidelity.yaml"))
        self.assertEqual(materials["Plastic"], 256)
        self.assertEqual(materials["WoodPlanks"], 528)
        self.assertEqual(materials["Air"], 1792)
        self.assertEqual(collision, {"Default": 0, "Hull": 1, "Box": 2, "PreciseConvexDecomposition": 3, "Tunable": 4})


if __name__ == "__main__":
    unittest.main()
