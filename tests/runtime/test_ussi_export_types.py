"""Compiled-host coverage for the isolated USSI enum/value transport bindings."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import persistent_runtime

SNAPSHOT = json.loads((ROOT / "tests/fixtures/world_snapshot.json").read_text())
HOST_CANDIDATES = [
    Path(os.environ["LUAU_TEST_HOST"]) if os.environ.get("LUAU_TEST_HOST") else None,
    Path("/tmp/ussi-candidate/headless-luau"),
]
HOST = next((p for p in HOST_CANDIDATES if p is not None and p.is_file()), None)


def enum_names(filename):
    import re
    text = (ROOT / "tests/fixtures/references/enums" / filename).read_text()
    return re.findall(r"^  - name: (.+)$", text, re.M)


@unittest.skipUnless(HOST, "build the candidate Luau host or set LUAU_TEST_HOST")
class UssiExportTypeHostTests(unittest.TestCase):
    def setUp(self):
        with mock.patch.object(persistent_runtime, "RUST_HOST", HOST):
            self.host = persistent_runtime.PersistentLuauHost(SNAPSHOT)
        self.addCleanup(self.host.close)

    def execute(self, source):
        result = self.host.call({"op": "execute", "source": source, "timeout_ms": 2000})
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(result.get("completed", True), result)
        return result

    def test_enum_registry_items_are_canonical_typed_and_complete(self):
        expected = {
            "SecurityCapability": ("SecurityCapability.yaml", "Basic", 7),
            "Material": ("Material.yaml", "Plastic", 256),
            "CollisionFidelity": ("CollisionFidelity.yaml", "Default", 0),
        }
        for enum, (filename, first_name, first_value) in expected.items():
            with self.subTest(enum=enum):
                names = enum_names(filename)
                self.assertEqual(self.execute(f'''
local e=Enum.{enum}
local items=e:GetEnumItems()
assert(typeof(e)=="Enum" and tostring(e)=="{enum}")
assert(#items=={len(names)})
local item=e["{first_name}"]
assert(item.Name=="{first_name}" and item.Value=={first_value})
assert(item.EnumType==e and rawequal(item,e:FromName("{first_name}")))
assert(rawequal(item,e:FromValue({first_value})))
assert(typeof(item)=="EnumItem")
local found=false
for _,candidate in items do if candidate==item then found=true end end
assert(found and not pcall(function()item.Value=0 end))
return tostring(item),item.Name,item.Value
''')["values"], [f"Enum.{enum}.{first_name}", first_name, first_value])

    def test_security_capabilities_value_constructor_tostring_and_equality(self):
        expected_names = enum_names("SecurityCapability.yaml")
        result = self.execute('''
local items=Enum.SecurityCapability:GetEnumItems()
local all=SecurityCapabilities.new(unpack(items))
local again=SecurityCapabilities.new(unpack(items))
local empty=SecurityCapabilities.new()
local basic=SecurityCapabilities.new(Enum.SecurityCapability.Basic)
local names={}
for _,item in items do names[#names+1]=item.Name end
assert(typeof(all)=="SecurityCapabilities")
assert(all==again and not (all==empty) and empty==SecurityCapabilities.new())
assert(tostring(empty)=="")
assert(tostring(basic)=="Basic")
assert(tostring(all)==table.concat(names," | "))
assert(not pcall(function()all.extra=true end))
return tostring(all),tostring(basic),typeof(all)
''')
        expected_string = " | ".join(expected_names)
        self.assertEqual(result["values"], [expected_string, "Basic", "SecurityCapabilities"])

    def test_enum_and_capability_values_cross_bindable_and_result_transport(self):
        result = self.execute('''
local event=Instance.new("BindableEvent")
getgenv().received=nil
local caps=SecurityCapabilities.new(Enum.SecurityCapability.Plugin,Enum.SecurityCapability.Basic)
event.Event:Connect(function(item,value) received={item,value} end)
event:Fire(Enum.Material.WoodPlanks,caps)
return Enum.Material.WoodPlanks,caps
''')
        self.assertEqual(result["values"][0], {
            "$enum": {"type": "Material", "name": "WoodPlanks", "value": 528}
        })
        metadata = (ROOT / "tests/fixtures/references/enums/SecurityCapability.yaml").read_text()
        import re
        metadata_values = list(map(int, re.findall(r"^    value: (\d+)$", metadata, re.M)))
        public_names = enum_names("SecurityCapability.yaml")
        public_items = dict(zip(public_names, metadata_values))
        result_mask = {"Plugin", "Basic"}
        cap_items = [{"name": name, "value": public_items[name]} for name in public_names if name in result_mask]
        self.assertEqual(result["values"][1], {"$securityCapabilities": cap_items})
        later = self.execute('''
assert(typeof(received[1])=="EnumItem")
assert(received[1]==Enum.Material.WoodPlanks)
assert(typeof(received[2])=="SecurityCapabilities")
assert(received[2]==SecurityCapabilities.new(Enum.SecurityCapability.Plugin,Enum.SecurityCapability.Basic))
return received[1],received[2]
''')
        self.assertEqual(later["values"], result["values"])


if __name__ == "__main__":
    unittest.main()
