"""Get opcode names from the pinned Luau headers."""
from pathlib import Path
import hashlib
import re
ROOT=Path(__file__).resolve().parent.parent
def digest(data):
    return hashlib.sha256(data).hexdigest()

def opcode_metadata():
    header = ROOT / "vendor/luau/Common/include/Luau/Bytecode.h"
    utils = (ROOT / "vendor/luau/Common/include/Luau/BytecodeUtils.h").read_text()
    text = header.read_text()
    body = text.split("enum LuauOpcode", 1)[1].split("LOP__COUNT", 1)[0]
    names = re.findall(r"^\s*LOP_([A-Z0-9_]+),", body, re.M)
    if not names or len(names) != len(set(names)):
        raise RuntimeError("Unexpected opcode enum layout")
    length_body = utils.split("inline int getOpLength", 1)[1].split("default:", 1)[0]
    two_words = set(re.findall(r"case LOP_([A-Z0-9_]+):", length_body))
    versions = dict((key, int(value)) for key, value in re.findall(
        r"\b(LBC_(?:TYPE_)?VERSION_[A-Z]+) = (\d+)", text))
    return {
        "source": "vendor/luau/Common/include/Luau/Bytecode.h",
        "header_sha256": digest(header.read_bytes()),
        "versions": versions,
        "opcodes": [{"id": i, "name": name, "words": 2 if name in two_words else 1}
                    for i, name in enumerate(names)],
        "note": "Names and word lengths from the pinned source. Not all opcodes are valid in all versions. NEWCLOSURE and DUPCLOSURE also consume prototype-dependent CAPTURE instructions.",
    }
