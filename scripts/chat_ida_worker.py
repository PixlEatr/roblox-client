"""Short-lived static chat lookup worker; opens only the disposable chat IDB."""
import json
from pathlib import Path
import sys
import time
import traceback

import ida_pro
import idc

root = Path(idc.ARGV[1])
sys.path.insert(0, str(root/'scripts'))
import chat_ida_extract

last = None
deadline = time.monotonic()+600
while time.monotonic()<deadline:
    path = root/'outputs/chat-analysis/request.json'
    try:
        stamp = path.stat().st_mtime_ns
        if stamp != last:
            request = json.loads(path.read_text())
            if request.get('stop'): break
            chat_ida_extract.main()
            (root/'outputs/chat-analysis/worker-ready.json').write_text(json.dumps({'request_stamp':stamp}))
            last = stamp
    except Exception:
        traceback.print_exc()
    time.sleep(.1)
ida_pro.qexit(0)
