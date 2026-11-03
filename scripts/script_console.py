"""Owner-only Unix socket for sending Luau source to the running custom client."""
import json
import base64
import hashlib
import secrets
import os
from pathlib import Path
import socket
import stat
import time


# JSON escaping can expand 100 MiB of source sixfold; allow metadata too.
MAX_FRAME = 608 * 1024 * 1024


class ScriptConsole:
    def __init__(self, path, execute, status=None, *, dispatch=None, events=None, user_chat=None, display=None, export_snapshot=None, keyboard=None, inspect_received=None, render=None, gui_action=None, leave=None):
        self.path = Path(path)
        self.execute = execute
        self.status = status
        self.dispatch = dispatch
        self.events = events
        self.user_chat = user_chat
        self.display = display
        self.export_snapshot = export_snapshot
        self.inspect_received = inspect_received
        self.keyboard = keyboard
        self.render = render
        self.gui_action = gui_action
        self.leave = leave
        from render_movement import SurfaceMovement
        self.movement=SurfaceMovement(execute)
        self._export_snapshots = {}
        self.clients = {}
        self.requests = 0
        if self.path.exists():
            metadata = self.path.lstat()
            if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
                raise ValueError('Console path is not an owned socket')
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.settimeout(.2)
                probe.connect(str(self.path))
            except ConnectionRefusedError:
                self.path.unlink()
            else:
                raise ValueError('A client script console is already running')
            finally:
                probe.close()
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self.path))
        self.path.chmod(0o600)
        self.inode = self.path.stat().st_ino
        self.server.listen(4)
        self.server.setblocking(False)

    def _snapshot_request(self, request):
        now = time.monotonic()
        self._export_snapshots = {key: value for key, value in self._export_snapshots.items()
                                  if value[0] > now}
        op = request['op']
        if op == 'export_snapshot':
            if set(request) != {'op'} or self.export_snapshot is None:
                raise ValueError('Snapshot export unavailable; restart the client with updated code')
            if len(self._export_snapshots) >= 2:
                raise ValueError('Too many snapshot exports in progress')
            data = json.dumps(self.export_snapshot(), ensure_ascii=True, allow_nan=False).encode()
            if len(data) > 8*1024*1024:
                raise ValueError('Export snapshot exceeds 8 MiB')
            token = secrets.token_hex(16)
            self._export_snapshots[token] = (now + 60, data)
            return {'ok': True, 'values': [], 'token': token, 'bytes': len(data),
                    'sha256': hashlib.sha256(data).hexdigest()}
        if op == 'export_snapshot_release':
            if set(request) != {'op', 'token'} or not isinstance(request['token'], str):
                raise ValueError('Invalid snapshot release')
            self._export_snapshots.pop(request['token'], None)
            return {'ok': True, 'values': []}
        if set(request) != {'op', 'token', 'offset'} or not isinstance(request['token'], str):
            raise ValueError('Invalid snapshot chunk request')
        entry = self._export_snapshots.get(request['token'])
        if entry is None:
            raise ValueError('Export snapshot expired or unknown')
        data = entry[1]
        offset = request['offset']
        if type(offset) is not int or not 0 <= offset < len(data):
            raise ValueError('Invalid snapshot offset')
        return {'ok': True, 'values': [],
                'data': base64.b64encode(data[offset:offset+65536]).decode('ascii')}

    def _drop(self, conn):
        conn.close()
        self.clients.pop(conn, None)

    def poll(self):
        try:self.movement.poll()
        except (ValueError,RuntimeError,OSError):pass
        if self.keyboard is not None:
            self.keyboard.poll()
        while len(self.clients) < 4:
            try:
                conn, _ = self.server.accept()
            except BlockingIOError:
                break
            try: conn.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
            except OSError: pass
            conn.setblocking(False)
            self.clients[conn] = {'data': bytearray(), 'out': None, 'started': time.monotonic()}
        for conn, state in list(self.clients.items()):
            try:
                if time.monotonic() - state['started'] > 60:
                    self._drop(conn)
                    continue
                if state['out'] is None:
                    closed = False
                    complete = False
                    # Drain a bounded batch, rather than one small socket
                    # fragment per game tick, so large scripts can finish.
                    for _ in range(64):
                        try:
                            data = conn.recv(256 * 1024)
                        except BlockingIOError:
                            break
                        if not data:
                            self._drop(conn)
                            closed = True
                            break
                        state['data'].extend(data)
                        if len(state['data']) > MAX_FRAME:
                            raise ValueError('Source request exceeds size limit')
                        if b'\n' in data:
                            complete = True
                            break
                    if closed:
                        continue
                    if not complete:
                        continue
                    frame, extra = bytes(state['data']).split(b'\n', 1)
                    if extra:
                        raise ValueError('One request per connection is supported')
                    request = json.loads(frame)
                    if not isinstance(request, dict):
                        raise ValueError('Invalid source request')
                    if request.get('op') in ('export_snapshot', 'export_snapshot_chunk', 'export_snapshot_release'):
                        result = self._snapshot_request(request)
                    elif request.get('op') == 'movement':
                        if set(request)!={'op','movement'}:
                            raise ValueError('Malformed movement request')
                        result=self.movement.handle(request['movement'])
                    elif request.get('op') == 'render':
                        if set(request)!={'op'} or self.render is None:
                            raise ValueError('Live rendering requires restarting this session with the updated client')
                        result=self.render()
                    elif request.get('op') == 'gui_action':
                        if set(request)!={'op','action'} or self.gui_action is None:
                            raise ValueError('GUI interactions are unavailable')
                        result=self.gui_action(request['action'])
                    elif request.get('op') == 'leave':
                        if set(request)!={'op'} or self.leave is None:
                            raise ValueError('Leaving this legacy session requires its terminal or original launcher')
                        self.leave()
                        result={'ok':True,'values':[],'state':'leaving'}
                    elif request.get('op') == 'inspect_received':
                        if set(request)!={'op','path'} or self.inspect_received is None:
                            raise ValueError('Received-instance inspection unavailable or malformed')
                        result=self.inspect_received(request['path'])
                    elif self.dispatch is not None:
                        result = self.dispatch(request)
                    elif request.get('op') == 'keyboard':
                        if set(request) != {'op', 'keyboard'}:
                            raise ValueError('Malformed keyboard request')
                        if self.keyboard is None:
                            raise ValueError('Restart the terminal client to enable keyboard controls')
                        result = self.keyboard.handle(request['keyboard'])
                    elif request.get('op') == 'display':
                        if set(request) != {'op', 'display'} or self.display is None:
                            raise ValueError('Display updates are unavailable or malformed')
                        result = self.display(request['display'])
                    elif request.get('op') == 'user_chat':
                        fields = set(request)
                        if (fields not in ({'op', 'text'}, {'op', 'text', 'channel'})
                                or self.user_chat is None):
                            raise ValueError('Typed chat input is unavailable or malformed')
                        if 'channel' not in request:
                            result = self.user_chat(request['text'])
                        else:
                            if request['channel'] not in ('RBXGeneral', 'RBXGlobal'):
                                raise ValueError('Chat channel must be RBXGeneral or RBXGlobal')
                            result = self.user_chat(request['text'], request['channel'])
                    elif request.get('op')=='events':
                        if set(request)!={'op','after'} or self.events is None:
                            raise ValueError('Runtime output events are unavailable or malformed')
                        result=self.events(request['after'])
                    elif request.get('op')=='status':
                        if set(request)!={'op','job_id'} or self.status is None:
                            raise ValueError('Persistent status queries are unavailable or malformed')
                        result=self.status(request['job_id'])
                    else:
                        if set(request)-{'source','timeout_ms'}:raise ValueError('Invalid source request')
                        result = self.execute(request.get('source'),
                                              timeout_ms=request.get('timeout_ms', 1000))
                    self.requests += 1
                    state['out'] = json.dumps(result, ensure_ascii=True, allow_nan=False).encode() + b'\n'
                    if len(state['out']) > 1024 * 1024:
                        raise ValueError('Script response exceeds size limit')
                sent = conn.send(state['out'])
                state['out'] = state['out'][sent:]
                if not state['out']: self._drop(conn)
            except BlockingIOError:
                continue
            except (ConnectionError, OSError):
                self._drop(conn)
            except Exception as error:
                state['out'] = json.dumps({'ok': False, 'values': [],
                                          'error_type': type(error).__name__,
                                          'error': str(error)}, ensure_ascii=True).encode() + b'\n'

    def close(self):
        try:self.movement.close()
        except (RuntimeError,ValueError,OSError):pass
        if self.keyboard is not None:
            self.keyboard.close()
        self._export_snapshots.clear()
        for conn in list(self.clients): self._drop(conn)
        self.server.close()
        try:
            if self.path.stat().st_ino == self.inode: self.path.unlink()
        except FileNotFoundError:
            pass


def send_source(path, source, timeout_ms=1000):
    return send_request(path,{'source':source,'timeout_ms':timeout_ms})


def send_request(path, request):
    frame = json.dumps(request, ensure_ascii=True).encode() + b'\n'
    if len(frame) > MAX_FRAME: raise ValueError('Source request exceeds size limit')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        try: conn.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
        except OSError: pass
        conn.settimeout(60 if len(frame) > 1024 * 1024 else 8)
        conn.connect(str(path))
        conn.sendall(frame)
        data = bytearray()
        while b'\n' not in data:
            chunk = conn.recv(65536)
            if not chunk: raise ValueError('Client closed the console without a result')
            data.extend(chunk)
            if len(data) > 1024 * 1024: raise ValueError('Console response exceeds size limit')
    return json.loads(data.split(b'\n', 1)[0])
