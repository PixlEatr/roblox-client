#!/usr/bin/env python3
"""Add exact Mach-O function boundaries to the next bounded IDA pass."""
import argparse
import bisect
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("addresses", nargs="+", type=lambda s: int(s, 16))
    parser.add_argument("--label", default="game_authentication")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    starts = json.loads((root / "outputs/macho-layout.json").read_text())["functions"]
    path = root / "scripts/followup-functions.json"
    entries = json.loads(path.read_text())
    known = {e["start"] for e in entries}
    for address in args.addresses:
        index = bisect.bisect_left(starts, address)
        if index >= len(starts) - 1 or starts[index] != address:
            raise SystemExit("Not an exact function start: " + hex(address))
        if address not in known:
            entries.append({"start": address, "end": starts[index + 1], "label": args.label})
            known.add(address)
    path.write_text(json.dumps(entries, indent=2) + "\n")
    print(json.dumps({"selected_followups": len(entries)}))


if __name__ == "__main__":
    main()
