#!/usr/bin/env python3
"""Rove HTTP bridge for the local Luau script socket."""
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import secrets
import socket
import stat
import os
import threading
import time
from urllib.parse import urlsplit, parse_qs
import webbrowser

from roblox_session import ROOT
from script_console import send_source, send_request
from display_state import validate_display
from browser_input import validate_keyboard_request


MAX_BODY = 608 * 1024 * 1024
JUMP_SOURCE = '''local player = game:GetService("Players").LocalPlayer
local character = player and player.Character
local humanoid = character and character:FindFirstChildOfClass("Humanoid")
if not humanoid or humanoid.Parent ~= character then
    error("Own character Humanoid is unavailable")
end
humanoid.Jump = true
return true'''


class WebConsole:
    def __init__(self, *, socket_path=None, port=0, server=None):
        self.socket_path = Path(socket_path) if socket_path is not None else ROOT / 'secrets/client-script.sock'
        self.key = secrets.token_urlsafe(32)
        self.execution_lock = threading.Lock()
        self.render_lock = threading.Lock()
        self.frame_cache = None
        self.frame_time = 0
        self.frame_identity=None
        self.frame_pending=None
        self.frame_error=None
        self.frame_retry=0
        self.render_closed=False
        self.frame_serial=0
        self.image_cache={}
        from render_assets import RenderAssets
        self.render_assets = RenderAssets()
        self.owns_server = server is None
        self.server = server or ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.server.daemon_threads = True
        self.server.console = self
        self.port = self.server.server_address[1]
        self.url = f'http://127.0.0.1:{self.port}/'
        self.thread = None

    def render_frame(self):
        with self.render_lock:
            now=time.monotonic()
            identity=self.runtime_identity()
            if identity!=self.frame_identity:
                self.frame_cache=None
                self.frame_identity=identity
                self.frame_error=None
                self.frame_retry=0
                self.render_assets.observe({})
            if self.render_closed or identity is None:
                return {'ok':False,'error':'The client is disconnected.'}
            if self.frame_cache is not None and now-self.frame_time<.1:
                return self.frame_cache
            if self.frame_pending is None and now>=self.frame_retry:
                token=object()
                self.frame_pending=token
                threading.Thread(target=self._read_frame,args=(identity,token),daemon=True).start()
            result={'ok':True,'pending':True}
            if self.frame_cache is not None:
                result.update(frame=self.frame_cache['frame'],sample=self.frame_serial,
                              age_ms=round((now-self.frame_time)*1000))
            if self.frame_error:
                result['message']=self.frame_error
            return result

    def _read_frame(self,identity,token):
        try:
            result=send_request(self.socket_path,{'op':'render'},timeout=30)
            if not isinstance(result,dict):
                raise ValueError('The runtime returned an invalid game frame.')
            if not result.get('ok') or not isinstance(result.get('frame'),dict):
                raise ValueError(result.get('error') or 'The runtime did not return a game frame.')
            error=None
        except TimeoutError:
            result=None;error='The client is busy. Waiting for the next game frame…'
        except (OSError,ValueError,RuntimeError) as failure:
            result=None;error=str(failure) or 'The game frame is temporarily unavailable.'
        with self.render_lock:
            if self.frame_pending is not token:return
            self.frame_pending=None
            if self.render_closed or self.frame_identity!=identity or self.runtime_identity()!=identity:return
            self.frame_error=error
            if result is not None:
                self.frame_serial+=1
                self.frame_cache={**result,'sample':self.frame_serial,'age_ms':0}
                self.frame_time=time.monotonic()
                self.frame_retry=0
                self.render_assets.observe(result['frame'])
            else:
                self.frame_retry=time.monotonic()+1

    def stop_rendering(self):
        with self.render_lock:
            self.render_closed=True
            self.frame_pending=None
            self.frame_cache=None

    def start(self, *, open_browser=True):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        if open_browser:
            webbrowser.open(self.url)
        return self.url

    def close(self):
        if not self.owns_server:return
        self.stop_rendering()
        if self.thread is not None:
            self.server.shutdown()
            self.thread.join(timeout=2)
            self.thread = None
        self.server.server_close()

    def connected(self):
        try:
            from local_transport import probe
            probe(self.socket_path)
            return True
        except (TimeoutError,BlockingIOError):
            return True
        except (OSError, ValueError):
            return False

    def runtime_identity(self):
        try:
            info=self.socket_path.lstat()
            if (os.name == 'nt' and self.connected()) or (os.name != 'nt' and stat.S_ISSOCK(info.st_mode) and info.st_uid==os.getuid()):
                return f'{info.st_dev}:{info.st_ino}:{info.st_ctime_ns}'
        except OSError:pass
        return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    @property
    def console(self):
        return self.server.console

    def reply(self, status, body, content_type='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=True, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https://*.rbxcdn.com; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):pass

    def valid_host(self):
        return self.headers.get('Host') in (f'127.0.0.1:{self.console.port}', f'localhost:{self.console.port}')

    def do_GET(self):
        if not self.valid_host():
            self.reply(403, {'ok': False, 'error': 'Invalid local host'})
            return
        path = urlsplit(self.path).path
        if path=='/api/image':
            # Public asset thumbnails only; no session cookie or arbitrary URL
            # is forwarded. Asset bodies cannot become scripts in this origin.
            value=parse_qs(urlsplit(self.path).query).get('id',[''])[0]
            if not value.isascii() or not value.isdigit() or not 0<int(value)<2**53:
                return self.reply(400,{'error':'Invalid image asset'})
            try:
                import urllib.request
                cached=self.console.image_cache.get(value)
                if not cached or cached[0]<time.monotonic():
                    request=urllib.request.Request('https://thumbnails.roblox.com/v1/assets?assetIds='+value+'&size=420x420&format=Png&isCircular=false')
                    with urllib.request.urlopen(request,timeout=5) as reply:
                        data=json.loads(reply.read(262144))
                    url=(data.get('data') or [{}])[0].get('imageUrl')
                    parsed=urlsplit(url or '')
                    if parsed.scheme!='https' or not parsed.hostname or not parsed.hostname.endswith('.rbxcdn.com'):
                        return self.reply(404,{'error':'Image is not ready'})
                    if len(self.console.image_cache)>1000:self.console.image_cache.clear()
                    cached=(time.monotonic()+300,url);self.console.image_cache[value]=cached
                self.send_response(302);self.send_header('Location',cached[1]);self.send_header('Content-Length','0');self.send_header('Cache-Control','no-store');self.end_headers()
            except (OSError,ValueError,TypeError,KeyError):
                return self.reply(502,{'error':'Image thumbnail unavailable'})
            return
        if path == '/api/status':
            connected=self.console.connected()
            if not connected:
                self.console.frame_cache=None
            self.reply(200, {'connected': connected,'session':self.console.runtime_identity() if connected else None})
            return
        self.reply(404, {'ok':False,'error':'Use the Rove launcher for the interface'})

    def do_POST(self):
        if not self.valid_host():
            self.reply(403, {'ok': False, 'error': 'Invalid local host'})
            return
        origin = 'http://' + self.headers['Host']
        if (self.headers.get('Origin') != origin
                or not hmac.compare_digest(self.headers.get('X-Console-Key', '').encode(), self.console.key.encode())):
            self.reply(403, {'ok': False, 'error': 'Reload this local console page and try again'})
            return
        if self.path not in ('/api/execute', '/api/events', '/api/user-chat', '/api/display', '/api/jump', '/api/input', '/api/render', '/api/render-asset', '/api/gui', '/api/leave', '/api/move'):
            self.reply(404, {'ok': False, 'error': 'Not found'})
            return
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            self.reply(415, {'ok': False, 'error': 'Expected JSON'})
            return
        try:
            length = int(self.headers.get('Content-Length', '-1'))
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY or self.headers.get('Transfer-Encoding'):
            self.reply(413, {'ok': False, 'error': 'Invalid or oversized script request'})
            return
        if self.path == '/api/input' and length > 16384:
            self.reply(413, {'ok': False, 'error': 'Keyboard request exceeds 16 KiB'})
            return
        if self.path == '/api/render-asset' and length > 2048:
            return self.reply(413, {'ok': False, 'error': 'Visual asset request exceeds limit'})
        self.connection.settimeout(5)
        try:
            request = json.loads(self.rfile.read(length))
            if self.path == '/api/render-asset':
                if not isinstance(request,dict) or set(request) != ({'kind','reference','weight','style'} if request.get('kind')=='font' else {'kind','reference'}):
                    raise ValueError('Invalid visual asset request')
                if request['kind']=='font':
                    return self.reply(200,self.console.render_assets.get('font',request['reference'],weight=request['weight'],style=request['style']))
                return self.reply(200,self.console.render_assets.get(request['kind'],request['reference']))
            if self.path in ('/api/render','/api/leave','/api/gui','/api/move'):
                if not isinstance(request,dict):
                    raise ValueError('Expected an object')
                if self.path in ('/api/render','/api/leave') and request!={}:
                    raise ValueError('Unexpected session request fields')
                if self.path=='/api/gui':
                    kind=request.get('kind')
                    fields=({'id','kind','text'} if kind=='text' else
                            {'id','kind','token'} if kind=='image_loaded' else
                            {'id','kind','revision','text'} if kind=='text_edit' else
                            {'id','kind','revision'} if kind=='text_select' else
                            {'id','kind','revision','submitted'} if kind=='text_blur' else
                            {'id','kind','delta','position','steps'} if kind=='wheel' else
                            {'id','kind','delta'} if kind=='scroll' else {'id','kind'})
                    selection=kind=='text_select' or (kind=='text_edit' and 'editorRevision' in request)
                    if selection:
                        fields|={'editorRevision','cursor','anchor'}
                    if (set(request)!=fields
                            or kind not in ('click','image_loaded','text','text_focus','text_edit','text_select','text_blur','pointer_down','pointer_up','scroll','wheel') or not isinstance(request.get('id'),str)
                            or len(request['id'])>128 or (kind in ('text','text_edit') and
                            (not isinstance(request.get('text'),str) or len(request['text'].encode())>4096))):
                        raise ValueError('Invalid GUI action')
                    if kind=='image_loaded':
                        token=request['token']
                        if not isinstance(token,str) or len(token)!=32 or any(c not in '0123456789abcdef' for c in token):
                            raise ValueError('Invalid image load receipt')
                    if kind in ('text_edit','text_select','text_blur') and (type(request['revision']) is not int
                            or not 0<=request['revision']<=2**53-1
                            or (kind=='text_blur' and type(request['submitted']) is not bool)):
                        raise ValueError('Invalid TextBox interaction')
                    if selection and (type(request['editorRevision']) is not int or not 0<=request['editorRevision']<=2**53-1
                            or type(request['cursor']) is not int or not 1<=request['cursor']<=4097
                            or type(request['anchor']) is not int or not -1<=request['anchor']<=4097 or request['anchor']==0):
                        raise ValueError('Invalid TextBox selection')
                    if kind in ('scroll','wheel'):
                        delta=request.get('delta')
                        if (not isinstance(delta,list) or len(delta)!=2 or
                                any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>4096 for v in delta)):
                            raise ValueError('Invalid GUI scroll delta')
                    if kind=='wheel':
                        position=request.get('position');steps=request.get('steps')
                        if (not isinstance(position,list) or len(position)!=2 or
                                any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>32768 for v in position) or
                                type(steps) is not int or not -128<=steps<=128):
                            raise ValueError('Invalid GUI wheel input')
                if self.path=='/api/move':
                    vector=request.get('direction')
                    if (set(request)!={'client','sequence','direction'} or not isinstance(vector,list) or len(vector)!=3
                            or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>1 for v in vector)
                            or vector[1]!=0):
                        raise ValueError('Invalid movement direction')
                try:
                    if self.path=='/api/render':
                        result=self.console.render_frame()
                    elif self.path=='/api/move':
                        result=send_request(self.console.socket_path,{'op':'movement','movement':request})
                    else:
                        result=send_request(self.console.socket_path, {'op':'leave'} if self.path=='/api/leave'
                                            else {'op':'gui_action','action':request})
                except OSError:
                    self.reply(503,{'ok':False,'error':'Runtime disconnected'})
                    return
                self.reply(200,result)
                return
            if self.path == '/api/input':
                request = validate_keyboard_request(request)
                try:
                    result = send_request(self.console.socket_path, {'op': 'keyboard', 'keyboard': request})
                except OSError:
                    self.reply(503, {'ok': False, 'error': 'Runtime disconnected'})
                    return
                self.reply(200, result)
                return
            if self.path == '/api/events':
                if (not isinstance(request,dict) or set(request)!={'after'}
                        or type(request['after']) is not int or request['after']<0):
                    raise ValueError('Invalid runtime output cursor')
                try:
                    result = send_request(self.console.socket_path, {'op':'events','after':request['after']})
                except OSError:
                    self.reply(503,{'ok':False,'error':'Runtime disconnected'})
                    return
                self.reply(200,result)
                return
            if self.path == '/api/jump':
                if not isinstance(request, dict) or request != {'action': 'jump'}:
                    raise ValueError('Invalid jump request')
                if not self.console.connected():
                    self.reply(503, {'ok': False, 'error': 'Runtime disconnected'})
                    return
            elif self.path == '/api/display':
                request = validate_display(request)
            elif self.path == '/api/user-chat':
                if (not isinstance(request, dict)
                        or set(request) not in ({'text'}, {'text', 'channel'})):
                    raise ValueError('Invalid typed chat submission')
                from chat_protocol import _text
                _text(request['text'])
                if 'channel' in request and request['channel'] not in ('RBXGeneral', 'RBXGlobal'):
                    raise ValueError('Chat channel must be RBXGeneral or RBXGlobal')
            elif self.path != '/api/jump':
                if not isinstance(request, dict) or set(request) - {'source', 'timeout_ms'}:
                    raise ValueError('Invalid script request')
                source = request.get('source')
                timeout_ms = request.get('timeout_ms', 1000)
                if not isinstance(source, str) or not source.strip():
                    raise ValueError('Write a script first')
                if len(source.encode('utf-8')) > 100 * 1024 * 1024:
                    raise ValueError('Script exceeds 100 MiB')
                if type(timeout_ms) is not int or not 1 <= timeout_ms <= 2000:
                    raise ValueError('Script budget must be 1..2000 ms')
        except (ValueError, UnicodeError, OSError) as error:
            self.reply(400, {'ok': False, 'error': str(error)})
            return
        if not self.console.execution_lock.acquire(blocking=False):
            self.reply(409, {'ok': False, 'error': 'A script is already running'})
            return
        try:
            result = (send_request(self.console.socket_path, {'op': 'display', 'display': request})
                      if self.path == '/api/display' else
                      send_request(self.console.socket_path, {
                          'op': 'user_chat', 'text': request['text'],
                          **({'channel': request['channel']} if 'channel' in request else {})
                      })
                      if self.path == '/api/user-chat' else
                      send_source(self.console.socket_path, JUMP_SOURCE, 1000)
                      if self.path == '/api/jump' else
                      send_source(self.console.socket_path, source, timeout_ms))
            self.reply(200, result)
        except OSError:
            self.reply(503, {'ok': False, 'error': 'Runtime unavailable. Start or reconnect your client.'})
        except ValueError as error:
            self.reply(502, {'ok': False, 'error': str(error)})
        finally:
            self.console.execution_lock.release()


def main():
    from launcher import main as launch
    launch()


if __name__ == '__main__':main()
