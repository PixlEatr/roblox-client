#!/usr/bin/env python3
"""Find/start the shared localhost backend without owning its game lifetime."""
import json
from pathlib import Path
import re
import subprocess
from platform_process import group_options
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]


def ready(port):
    url=f'http://127.0.0.1:{port}'
    try:
        with urllib.request.urlopen(url+'/',timeout=.5) as reply:
            html=reply.read(2*1024*1024).decode()
        match=re.search(r'name="rove-key" content="([A-Za-z0-9_-]+)"',html)
        if not match or 'name="rove-ui-version" content="2"' not in html:return None
        request=urllib.request.Request(url+'/api/sessions',headers={'X-Rove-Key':match[1]})
        with urllib.request.urlopen(request,timeout=.5) as reply:
            data=json.load(reply)
        return url+'/' if isinstance(data.get('sessions'),list) else None
    except (OSError,ValueError):return None


def main():
    for port in (5468,5467,5470):
        url=ready(port)
        if url:
            print(url,flush=True);return
    # The backend survives closing a display. Explicit Leave disconnects the
    # game; window lifetime must never interrupt a terminal-owned session.
    import socket
    port=None
    for candidate in (5468,5470):
        with socket.socket() as probe:
            try:probe.bind(('127.0.0.1',candidate));port=candidate;break
            except OSError:pass
    if port is None:raise SystemExit('No free local launcher port (5468 or 5470)')
    command=[sys.executable,str(ROOT/'scripts/launcher.py'),'--port',str(port),'--no-open']
    try:
        with urllib.request.urlopen('http://127.0.0.1:5467/api/status',timeout=.5) as reply:
            if json.load(reply).get('state') is not None:command+=['--attach','http://127.0.0.1:5467']
    except (OSError,ValueError):pass
    log=ROOT/'outputs/desktop/backend.log';log.parent.mkdir(parents=True,exist_ok=True)
    with log.open('ab') as output:
        child=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=output,stderr=output,**group_options())
    for _ in range(80):
        url=ready(port)
        if url:print(url,flush=True);return
        if child.poll() is not None:break
        time.sleep(.1)
    raise SystemExit('The local backend could not start. See outputs/desktop/backend.log')


if __name__=='__main__':main()
