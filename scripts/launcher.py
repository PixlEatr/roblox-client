#!/usr/bin/env python3
"""Local Rove launcher; account credentials stay in the existing backend file."""
if __name__ == '__main__':
    from start_ui import prepare_launcher
    prepare_launcher()

import argparse
from collections import deque
import hmac
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import subprocess
from platform_process import group_options
import sys
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import webbrowser

from roblox_session import ROOT, RobloxSession, SessionError
from terminal_client import prepare_target, session_command, selection_command, stop
from client_session_lease import acquire_session_lease

from web_console import Handler as RuntimeHandler, WebConsole

ASSETS = ROOT/'ui'


def image_url(value):
    if not isinstance(value, str):
        return None
    url = urlsplit(value)
    return value if url.scheme == 'https' and url.hostname and (url.hostname == 'rbxcdn.com' or url.hostname.endswith('.rbxcdn.com')) else None


class AccountSessionExpired(SessionError):
    pass


class AccountAPI:
    def __init__(self):
        self._session = None
        self.cache = {}
        self.lock = threading.RLock()

    @property
    def session(self):
        with self.lock:
            if self._session is None: self._session=RobloxSession()
            return self._session

    @session.setter
    def session(self, value): self._session=value

    def reset_account(self):
        with self.lock:
            self._session=None; self.cache.clear()

    def request(self, host, path, method='GET', payload=None):
        result = self.session.request(host, path, method, payload, csrf_retry=method in ('POST','DELETE'))
        if result['http_status'] not in (200,201,204) or result['challenge_present']:
            if result['http_status'] == 401:
                raise AccountSessionExpired('Your saved Roblox session expired. Connect your account again.')
            raise SessionError(f'Roblox could not load this section (HTTP {result["http_status"]}).')
        return result['data']

    def cached(self, key, loader, ttl=45):
        with self.lock:
            entry = self.cache.get(key)
            if entry and entry[0] > time.monotonic():
                return entry[1]
        value = loader()
        with self.lock:
            self.cache[key] = (time.monotonic()+ttl, value)
        return value

    def thumbnails(self, ids, kind='users/avatar-headshot', *, size='150x150'):
        if not ids:
            return {}
        query = {'userIds' if kind.startswith('users') else 'universeIds': ','.join(str(i) for i in ids[:100]),
                 'size':size, 'format':'Png', 'isCircular':'false'}
        try:
            data = self.request('thumbnails.roblox.com', '/v1/'+kind+'?'+urlencode(query))
            return {x['targetId']:image_url(x.get('imageUrl')) for x in data.get('data', [])}
        except SessionError:
            return {}

    def account(self):
        def load():
            user = self.request('users.roblox.com', '/v1/users/authenticated')
            uid = user.get('id')
            if type(uid) is not int:
                raise SessionError('Roblox returned an unexpected account response')
            return {'id':uid, 'name':user.get('name', ''), 'displayName':user.get('displayName') or user.get('name', ''),
                    'avatar':self.thumbnails([uid]).get(uid),
                    'savedPrivateServer':(ROOT/'secrets/private-server-link.json').is_file()}
        return self.cached('account', load, ttl=120)

    def friends(self):
        def load():
            uid = self.account()['id']
            data = self.request('friends.roblox.com', f'/v1/users/{uid}/friends')
            rows = data.get('data', [])[:200]
            ids = [x['id'] for x in rows if type(x.get('id')) is int]
            users = {}
            if ids:
                for start in range(0, len(ids), 100):
                    reply = self.request('users.roblox.com', '/v1/users', 'POST',
                                         {'userIds':ids[start:start+100], 'excludeBannedUsers':True})
                    users.update({x['id']:x for x in reply.get('data', [])})
            images = self.thumbnails(ids)
            presence, warning = {}, None
            if ids:
                try:
                    for start in range(0, len(ids), 50):
                        reply = self.request('presence.roblox.com', '/v1/presence/users', 'POST', {'userIds':ids[start:start+50]})
                        presence.update({x['userId']:x for x in reply.get('userPresences', [])})
                except SessionError:
                    warning = 'Presence is unavailable; friends are still shown.'
            friends = []
            for row in rows:
                uid = row.get('id')
                if type(uid) is not int:
                    continue
                p = presence.get(uid, {})
                user = users.get(uid, row)
                friends.append({'id':uid,'name':user.get('name') or str(uid), 'displayName':user.get('displayName') or user.get('name') or str(uid),
                                'avatar':images.get(uid), 'presence':p.get('userPresenceType'),
                                'location':p.get('lastLocation', ''), 'placeId':p.get('placeId')})
            friends.sort(key=lambda x:(-(x['presence'] or 0), x['displayName'].lower()))
            return {'friends':friends, 'warning':warning}
        return self.cached('friends', load)

    def game_details(self, place_ids):
        ids = list(dict.fromkeys(i for i in place_ids if type(i) is int and 0 < i < 2**53))[:20]
        if not ids:
            return []
        details = self.request('games.roblox.com', '/v1/games/multiget-place-details?'+urlencode([('placeIds',str(i)) for i in ids]))
        universes = [x['universeId'] for x in details if type(x.get('universeId')) is int]
        extra = {}
        if universes:
            try:
                reply = self.request('games.roblox.com', '/v1/games?'+urlencode({'universeIds':','.join(map(str,universes))}))
                extra = {x['id']:x for x in reply.get('data', [])}
            except SessionError:
                pass
        votes = {}
        if universes:
            try:
                reply = self.request('games.roblox.com', '/v1/games/votes?'+urlencode({'universeIds':','.join(map(str,universes))}))
                for vote in reply.get('data', []):
                    total = vote.get('upVotes',0)+vote.get('downVotes',0)
                    votes[vote['id']] = round(100*vote.get('upVotes',0)/total) if total else None
            except SessionError:
                pass
        images = self.thumbnails(universes, 'games/icons')
        result = []
        for row in details:
            universe = row.get('universeId')
            game = extra.get(universe, {})
            result.append({'placeId':row.get('placeId'), 'universeId':universe,
                           'name':game.get('name') or row.get('name', 'Experience'),
                           'creator':game.get('creator', {}).get('name') or row.get('builder', ''),
                           'description':game.get('description', ''), 'playing':game.get('playing'),
                           'visits':game.get('visits'), 'maxPlayers':game.get('maxPlayers'), 'votePercent':votes.get(universe),
                           'image':images.get(universe), 'playable':row.get('isPlayable') is True,
                           'reason':row.get('reasonProhibited')})
        return result

    def games(self):
        from launcher_discovery import recommendations
        data=recommendations(self)
        try:
            history=json.loads((ROOT/'outputs/launcher/history.json').read_text())[:8]
        except (OSError,ValueError):history=[]
        local=self.game_details([row.get('placeId') for row in history]) if history else []
        recent=local+[game for game in data['continue'] if game['placeId'] not in {g['placeId'] for g in local}]
        games=recent+[game for game in data['recommended'] if game['placeId'] not in {g['placeId'] for g in recent}]
        return {'games':games,'recentIds':[g['placeId'] for g in recent],
                'recommendationIds':[g['placeId'] for g in data['recommended']],
                'favoriteIds':[],'warning':None,'source':data['source']}

    def place(self, value):
        if isinstance(value, str) and value.startswith('https://www.roblox.com/games/'):
            value = urlsplit(value).path.split('/')[2]
        if not isinstance(value, str) or not value.isascii() or not value.isdigit() or not 0 < int(value) < 2**53:
            raise ValueError('Enter a place ID or Roblox game URL.')
        games = self.game_details([int(value)])
        if not games:
            raise SessionError('Experience was not found.')
        return games[0]


class AttachedController:
    """Keep an existing launcher-owned game connected across UI development."""
    def __init__(self, url):
        import urllib.request
        import re
        parsed=urlsplit(url)
        if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or not parsed.port or parsed.path not in ('','/') or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Attach requires an existing 127.0.0.1 launcher URL')
        self.url=url.rstrip('/')
        with urllib.request.urlopen(self.url+'/',timeout=5) as response:
            html=response.read(2*1024*1024).decode()
        match=re.search(r'name="rove-key" content="([A-Za-z0-9_-]+)"',html)
        if not match:
            raise ValueError('Existing launcher did not provide its local key')
        self.key=match.group(1)

    def call(self,path,payload=None):
        import urllib.request
        headers={'X-Rove-Key':self.key,'Origin':self.url,'Content-Type':'application/json'}
        body=json.dumps(payload).encode() if payload is not None else None
        request=urllib.request.Request(self.url+path,data=body,headers=headers)
        try:
            with urllib.request.urlopen(request,timeout=8) as reply:
                return json.load(reply)
        except Exception:
            raise SessionError('The connected game launcher is unavailable.') from None

    def status(self):return self.call('/api/status')
    def play(self,target,game=None):return self.call('/api/play',{'target':target,'game':game or {}})
    def leave(self):return self.call('/api/leave',{})
    def close(self):pass  # The other launcher owns this session and its shutdown.


class GameController:
    def __init__(self):
        self.lock = threading.RLock()
        self.process = None
        self.cancel = False
        self.state = 'idle'
        self.error = None
        self.game = None
        self.events = deque(maxlen=25)
        self.worker_running = False

    def status(self):
        with self.lock:
            return {'state':self.state, 'error':self.error, 'game':self.game,
                    'events':list(self.events), 'editorUrl':'/workspace' if self.state=='playing' else None}

    def play(self, target, game=None):
        if target == 'saved-private':
            try:
                target = json.loads((ROOT/'secrets/private-server-link.json').read_text())['url']
            except (OSError, KeyError, ValueError):
                raise ValueError('No saved private-server link is available.') from None
        if not isinstance(target, str) or len(target) > 2048:
            raise ValueError('Invalid play target')
        selection_command(target)
        with self.lock:
            if self.worker_running or self.state in ('preparing', 'joining', 'playing', 'leaving'):
                raise ValueError('Leave the current session before joining another experience.')
            self.state, self.error, self.cancel = 'preparing', None, False
            self.worker_running = True
            self.game = {'name':str((game or {}).get('name') or 'Selected experience')[:160]}
            self.events.clear()
            self.events.append('Checking account access…')
            threading.Thread(target=self._join, args=(target,), daemon=True).start()
        return self.status()

    def _join(self, target):
        try:
            # Do not alter the selection belonging to another account session.
            with acquire_session_lease(ROOT):
                place = prepare_target(target)
            with self.lock:
                if self.cancel:
                    self.state = 'idle'
                    return
                selected = json.loads((ROOT/'outputs/gamejoin/target-selection.json').read_text())
                self.game = {'name':selected.get('display_name') or 'Experience', 'placeId':place}
                self.state = 'joining'
                self.process = subprocess.Popen(session_command(place, 0), cwd=ROOT,
                                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                                stdin=subprocess.DEVNULL, text=True, **group_options())
                process = self.process
            history_path = ROOT/'outputs/launcher/history.json'
            history_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                history = json.loads(history_path.read_text())
            except (OSError, ValueError):
                history = []
            history = [self.game]+[x for x in history if x.get('placeId') != place]
            history_path.write_text(json.dumps(history[:12]))
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except (ValueError, TypeError):
                    continue
                milestone = event.get('milestone') if isinstance(event, dict) else None
                if isinstance(milestone, str):
                    with self.lock:
                        self.events.append(milestone.replace('_', ' '))
                        if milestone == 'character_spawned':
                            self.state = 'playing'
            code = process.wait()
            with self.lock:
                self.state = 'idle' if self.cancel or code == 0 else 'error'
                if self.state == 'error':
                    self.error = f'The game client exited (code {code}). Check access or retry joining.'
        except Exception as error:
            with self.lock:
                self.state = 'idle' if self.cancel else 'error'
                self.error = (str(error) if isinstance(error, (SessionError, ValueError, RuntimeError))
                              else 'Could not start the game client. Check the terminal for setup issues.')
        finally:
            with self.lock:
                self.process = None
                self.worker_running = False

    def close(self):
        self.leave()
        with self.lock:
            process = self.process
        if process is not None:
            stop(process)

    def leave(self):
        with self.lock:
            if self.state in ('idle', 'error'):
                self.state, self.error = 'idle', None
                return self.status()
            self.cancel, self.state = True, 'leaving'
            process = self.process
        if process is not None:
            threading.Thread(target=stop, args=(process,), daemon=True).start()
        return self.status()


class Launcher:
    def __init__(self, port=5467, api=None, controller=None, socket_path=None):
        self.api = api if api is not None else AccountAPI()
        self.controller = controller or GameController()
        self.key = secrets.token_urlsafe(32)
        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.server.daemon_threads = True
        self.server.app = self
        self.port = self.server.server_address[1]
        self.url = f'http://127.0.0.1:{self.port}/'
        self.surface=WebConsole(socket_path=socket_path, server=self.server)
        self.surface.key=self.key

    def close(self):
        self.surface.stop_rendering()
        self.surface.close()
        self.controller.close()
        self.server.shutdown()
        self.server.server_close()


class Handler(RuntimeHandler):
    def log_message(self, *args):
        pass

    @property
    def app(self):
        return self.server.app

    def reply(self, status, body, mime='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=True).encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' https://*.rbxcdn.com; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src https://css.rbxcdn.com; connect-src 'self' https://*.rbxcdn.com; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        try:
            self.end_headers()
            if self.command!='HEAD':self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):pass

    def valid_host(self):
        return self.headers.get('Host') in (f'127.0.0.1:{self.app.port}', f'localhost:{self.app.port}')

    def do_HEAD(self):
        return self.do_GET()

    def do_GET(self):
        if self.path.startswith('/api/runtime/'):
            self.path='/api/'+self.path[len('/api/runtime/'):]
            return RuntimeHandler.do_GET(self)
        if not self.valid_host():
            return self.reply(403, {'error':'Invalid local host'})
        parsed = urlsplit(self.path)
        files = {'/':('index.html','text/html; charset=utf-8'),
                 '/workspace':('index.html','text/html; charset=utf-8'),
                 '/workspace/':('index.html','text/html; charset=utf-8'),
                 '/app.js':('app.js','text/javascript; charset=utf-8'),
                 '/avatar-viewer.js':('avatar-viewer.js','text/javascript; charset=utf-8'),
                 '/avatar-ui.js':('avatar-ui.js','text/javascript; charset=utf-8'),
                 '/account-pages.js':('account-pages.js','text/javascript; charset=utf-8'),
                 '/sessions-ui.js':('sessions-ui.js','text/javascript; charset=utf-8'),
                 '/setup-ui.js':('setup-ui.js','text/javascript; charset=utf-8'),
                 '/setup-ui.css':('setup-ui.css','text/css; charset=utf-8'),
                 '/style.css':('style.css','text/css; charset=utf-8')}
        if re.fullmatch(r'/[A-Za-z0-9][A-Za-z0-9_.-]*\.(?:js|css)',parsed.path):
            name=parsed.path[1:]
            asset=ASSETS/name
            if asset.is_symlink() or not asset.is_file():
                return self.reply(404,{'error':'UI file not found. Restart Rove after updating.'})
            files[parsed.path]=(name,'text/javascript; charset=utf-8' if name.endswith('.js') else 'text/css; charset=utf-8')
        files['/keyboard-codes.json']=('keyboard-codes.json','application/json')
        for asset in ('navigation.svg','common.svg','games.svg','actions.svg','wordmark.svg','chevron.svg'):
            files['/assets/'+asset] = ('assets/'+asset, 'image/svg+xml')
        if parsed.path in files:
            name, mime = files[parsed.path]
            return self.reply(200, (ASSETS/name).read_bytes().replace(b'__LAUNCHER_KEY__', self.app.key.encode()), mime)
        if parsed.path == '/api/status':
            return self.reply(200, self.app.controller.status())
        if not hmac.compare_digest(self.headers.get('X-Rove-Key',''), self.app.key):
            return self.reply(403, {'error':'Reload the launcher to reconnect.'})
        try:
            query=parse_qs(parsed.query)
            if parsed.path=='/api/setup':
                from launcher_setup import readiness
                return self.reply(200,readiness(ROOT))
            if parsed.path=='/api/sessions':
                connected=self.app.surface.connected()
                status=self.app.controller.status()
                return self.reply(200,{'sessions':[{'id':'current','connected':connected,
                    'url':self.app.url+'workspace','editorUrl':self.app.url+'workspace',
                    'name':((status.get('game') or {}).get('name') if status.get('state') in ('joining','playing') else None) or 'Terminal session'}],
                    'singleAccount':True})
            if parsed.path.startswith('/api/pages/'):
                from launcher_account_pages import messages,message,inventory,trades,trade_detail,communities,community,shop
                page=parsed.path.rsplit('/',1)[-1]
                if page=='messages':return self.reply(200,messages(self.app.api,query.get('tab',['Inbox'])[0],query.get('page',['0'])[0]))
                if page=='message':return self.reply(200,message(self.app.api,query.get('id',[''])[0]))
                if page=='inventory':return self.reply(200,inventory(self.app.api,query.get('category',['hats'])[0],query.get('cursor',[''])[0]))
                if page=='trades':return self.reply(200,trades(self.app.api,query.get('status',['Inbound'])[0],query.get('cursor',[''])[0]))
                if page=='trade':return self.reply(200,trade_detail(self.app.api,query.get('id',[''])[0]))
                if page=='communities':return self.reply(200,communities(self.app.api,query.get('q',[''])[0]))
                if page=='community':return self.reply(200,community(self.app.api,query.get('id',[''])[0]))
                if page=='shop':return self.reply(200,shop(self.app.api,query.get('q',[''])[0],query.get('category',['shirts'])[0],query.get('cursor',[''])[0]))
                return self.reply(404,{'error':'Not found'})
            if parsed.path == '/api/account':
                try:
                    return self.reply(200, self.app.api.account())
                except AccountSessionExpired as error:
                    return self.reply(401, {'error':str(error)})
            if parsed.path == '/api/friends':
                return self.reply(200, self.app.api.friends())
            if parsed.path == '/api/games':
                return self.reply(200, self.app.api.games())
            if parsed.path in ('/api/avatar','/api/wardrobe','/api/avatar-model'):
                from launcher_avatar import appearance,wardrobe,model3d
                query=parse_qs(parsed.query)
                if parsed.path=='/api/avatar':return self.reply(200,appearance(self.app.api,refresh=query.get('refresh')==['1']))
                if parsed.path=='/api/avatar-model':return self.reply(200,model3d(self.app.api))
                return self.reply(200,wardrobe(self.app.api,query.get('category',['recent'])[0],query.get('cursor',[''])[0]))
            if parsed.path == '/api/search':
                from launcher_discovery import search
                query=parse_qs(parsed.query)
                return self.reply(200,search(self.app.api,query.get('q',[''])[0],query.get('cursor',[''])[0]))
            if parsed.path == '/api/profile':
                from launcher_social import profile
                uid=parse_qs(parsed.query).get('user',[''])[0]
                return self.reply(200,profile(self.app.api,uid))
            if parsed.path == '/api/people':
                from launcher_social import people
                query=parse_qs(parsed.query)
                return self.reply(200,people(self.app.api,query.get('user',[''])[0],query.get('kind',['friends'])[0]))
            if parsed.path == '/api/game':
                value = parse_qs(parsed.query).get('place', [''])[0]
                return self.reply(200, self.app.api.place(value))
            self.reply(404, {'error':'Not found'})
        except SessionError as error:
            self.reply(400, {'error':str(error)})
        except (ValueError, OSError):
            self.reply(400, {'error':'Could not load this Roblox section. Try refreshing.'})

    def do_POST(self):
        if self.path.startswith('/api/runtime/'):
            self.path='/api/'+self.path[len('/api/runtime/'):]
            return RuntimeHandler.do_POST(self)
        if (not self.valid_host() or self.headers.get('Origin') != 'http://'+self.headers.get('Host','')
                or not hmac.compare_digest(self.headers.get('X-Rove-Key',''), self.app.key)):
            return self.reply(403, {'error':'Invalid local request'})
        try:
            length = int(self.headers.get('Content-Length','0'))
            path = urlsplit(self.path).path
            if not 0 < length <= (65536 if path=='/api/setup/account' else 4096):
                raise ValueError('Invalid request size')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Invalid request')
            path = urlsplit(self.path).path
            if path=='/api/setup/account':
                if set(data)!={'session'}: raise ValueError('Invalid account request')
                if self.app.controller.status().get('state') not in ('idle','error'):
                    raise ValueError('Leave the current game before changing accounts.')
                from launcher_setup import connect_account
                try: result=connect_account(ROOT,data['session'])
                except RuntimeError:
                    raise ValueError('Leave the current game before changing accounts.') from None
                self.app.api.reset_account()
                return self.reply(200,result)
            if path.startswith('/api/actions/'):
                from launcher_account_pages import message_action,trade_action,community_action,purchase_quote,purchase
                action=path.rsplit('/',1)[-1]
                operations={'message':message_action,'trade':trade_action,'community':community_action,'quote':purchase_quote,'purchase':purchase}
                if action not in operations:return self.reply(404,{'error':'Not found'})
                return self.reply(200,operations[action](self.app.api,data))
            if path == '/api/avatar/apply':
                from launcher_avatar import apply
                return self.reply(200,apply(self.app.api,data))
            if path == '/api/play':
                if set(data) != {'target', 'game'} or not isinstance(data['game'], dict):
                    raise ValueError('Invalid play request')
                return self.reply(200, self.app.controller.play(data['target'], data['game']))
            if path == '/api/leave':
                return self.reply(200, self.app.controller.leave())
            self.reply(404, {'error':'Not found'})
        except (ValueError, SessionError) as error:
            self.reply(400, {'error':str(error)})
        except OSError:
            self.reply(500,{'error':'Could not access local account storage. Check the project folder permissions.'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=5467)
    parser.add_argument('--no-open', action='store_true')
    parser.add_argument('--attach', help='Existing local launcher whose game connection should be retained')
    args = parser.parse_args()
    try:
        app = Launcher(args.port, controller=AttachedController(args.attach) if args.attach else None)
    except (OSError, SessionError) as error:
        raise SystemExit('Launcher could not start: '+str(error)) from None
    thread = threading.Thread(target=app.server.serve_forever, daemon=True)
    thread.start()
    print('Rove launcher: '+app.url, flush=True)
    if not args.no_open:
        webbrowser.open(app.url)
    try:
        while thread.is_alive():
            thread.join(.5)
    except KeyboardInterrupt:
        pass
    finally:
        app.close()


if __name__ == '__main__':
    main()
