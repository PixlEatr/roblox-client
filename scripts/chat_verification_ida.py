"""Bounded disposable-DB chat lookup, including instruction-level evidence."""
import json
from pathlib import Path
import sys
import time
import traceback

import ida_funcs
import ida_pro
import idautils
import idc

root = Path(idc.ARGV[1])
sys.path.insert(0, str(root / 'scripts'))
import chat_ida_extract

out = root / 'outputs/chat-analysis'
last = None
deadline = time.monotonic() + 600
while time.monotonic() < deadline:
    path = out / 'request.json'
    try:
        stamp = path.stat().st_mtime_ns
        if stamp != last:
            request = json.loads(path.read_text())
            if request.get('stop'):
                break
            chat_ida_extract.main()
            for address in request.get('functions', []):
                function = ida_funcs.get_func(address)
                if function and function.end_ea - function.start_ea <= 65536:
                    lines = [f'{ea:#x}: {idc.generate_disasm_line(ea, 0)}'
                             for ea in idautils.FuncItems(function.start_ea)]
                    (out / f'{address:x}.asm').write_text('\n'.join(lines) + '\n')
            (out / 'verification-worker-ready.json').write_text(
                json.dumps({'request_stamp': stamp}))
            last = stamp
    except Exception:
        traceback.print_exc()
    time.sleep(.1)
ida_pro.qexit(0)
