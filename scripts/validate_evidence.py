#!/usr/bin/env python3
"""Independently validate marker instructions with Capstone and file hashes."""
import hashlib
import json
from pathlib import Path
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM

root = Path(__file__).resolve().parent.parent
layout = json.loads((root / "outputs/macho-layout.json").read_text())
evidence = json.loads((root / "outputs/static-evidence.json").read_text())
manifest = json.loads((root / "outputs/ida/manifest.json").read_text())
provenance = json.loads((root / "outputs/provenance.json").read_text())
data = Path(provenance["copy"]).read_bytes()
assert hashlib.sha256(data).hexdigest() == evidence["sha256"] == manifest["sha256"] == provenance["sha256"]
assert hashlib.sha256(Path(provenance["source"]).read_bytes()).hexdigest() == provenance["sha256"]
decoder = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
decoder.detail = True
text = layout["text"]
checked = []
for marker in evidence["marker_results"]:
    for reference in marker["candidate_references"]:
        first, last = reference["base_instruction"], reference["instruction_address"]
        offset = text["offset"] + first - text["address"]
        instructions = list(decoder.disasm(data[offset:offset + last - first + 4], first))
        base, add = instructions[0], instructions[-1]
        if reference["method"] == "ADR":
            assert base.mnemonic == "adr"
            actual = base.operands[1].imm
        else:
            assert base.mnemonic == "adrp" and add.mnemonic == "add"
            register = base.operands[0].reg
            assert register == add.operands[1].reg
            for instruction in instructions[1:-1]:
                assert register not in instruction.regs_access()[1], "Intervening register overwrite"
            actual = base.operands[1].imm + (add.operands[2].imm << add.operands[2].shift.value)
        assert actual == marker["address"]
        checked.append({"instruction": last, "string_address": actual})
for f in manifest["functions"]:
    assert f["start"] in layout["functions"] and f.get("decompiled") and not f.get("error")
    assert all(m["confirmed"] for m in f["markers"])
    for extension in ["c", "asm", "json"]:
        assert (root / ("outputs/ida/%x.%s" % (f["start"], extension))).is_file()
assert manifest["database_saved"] and Path(manifest["database"]).is_file()
result = {"source_unchanged": True, "copy_unchanged": True, "sha256": provenance["sha256"],
          "capstone_checked_references": len(checked), "ida_confirmed_references": len(checked),
          "decompiled_functions": len(manifest["functions"]), "database_saved": True,
          "references": checked}
(root / "outputs/validation.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps({k: v for k, v in result.items() if k != "references"}, indent=2))
