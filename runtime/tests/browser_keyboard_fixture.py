"""Local browser proof fixture. Uses synthetic world data; never joins Roblox."""
from pathlib import Path
import json
import sys
import tempfile
import threading
import time
sys.path[:0]=[str(Path(__file__).resolve().parents[2]/'scripts')]
from browser_input import BrowserKeyboard
from persistent_runtime import PersistentClientScriptRuntime
from script_console import ScriptConsole,send_source
from test_world_snapshot import fixture
from web_console import WebConsole


def main():
    folder=tempfile.TemporaryDirectory(dir='/tmp',prefix='browser-keyboard-proof-')
    world,_=fixture();runtime=PersistentClientScriptRuntime(lambda:world,123)
    observations=[];lock=threading.Lock();original=runtime._record
    def record(result):
        recorded=original(result)
        entry={'movement_intents':result.get('movement_intents',[]),'task_errors':result.get('task_errors',[])}
        if any(entry.values()):
            with lock:observations.append(entry)
        return recorded
    runtime._record=record
    console=ScriptConsole(Path(folder.name)/'client.sock',runtime.execute,
                          status=lambda job:runtime.status(job,advance=False),events=runtime.events,
                          display=runtime.update_display,keyboard=BrowserKeyboard(runtime))
    stop=threading.Event();errors=[]
    def owner():
        try:
            while not stop.is_set():console.poll();runtime.poll();stop.wait(.002)
        except BaseException as error:errors.append(type(error).__name__+': '+str(error))
        finally:console.close();runtime.close()
    thread=threading.Thread(target=owner,daemon=True);thread.start()
    web=WebConsole(socket_path=console.path);web.start(open_browser=False)
    print(json.dumps({'url':web.url,'mode':'local_fixture_only'}),flush=True)
    try:
        for line in sys.stdin:
            command=line.strip()
            if command=='stop':break
            if command=='state':
                result=send_source(console.path,'''local u=game:GetService("UserInputService")
return browserInputBegins or 0,browserInputEnds or 0,browserInputKeys or "",u:IsKeyDown("W"),u:IsKeyDown("Space"),u.KeyboardEnabled''')
                with lock:captured=list(observations)
                print(json.dumps({'ok':result.get('ok'),'values':result.get('values'),
                                  'observations':captured,'executions':runtime.executions,'errors':errors}),flush=True)
    finally:
        web.close();stop.set();thread.join(5);folder.cleanup()
    return 0 if not errors else 1

if __name__=='__main__':raise SystemExit(main())
