"""Bounded, asynchronous session timeline. Never records packet bodies or credentials."""
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time
import traceback
import uuid

from platform_files import open_nofollow

_PRIVATE = re.compile(r'cookie|ticket|password|secret|authorization|credential|payload|source_text|private_server|endpoint|address|token', re.I)
_OPAQUE = re.compile(r'https?://\S+|\b[0-9a-f]{32,}\b|_\|WARNING[^\s]*', re.I)


def safe(value, depth=0):
    if depth > 5: return '[depth limit]'
    if value is None or isinstance(value, (bool, int)): return value
    if isinstance(value, float): return value if math.isfinite(value) else None
    if isinstance(value, str): return _OPAQUE.sub('[redacted]', value[:512])
    if isinstance(value, dict):
        return {str(k)[:80]: safe(v, depth+1) for k,v in list(value.items())[:100]
                if isinstance(k,str) and not _PRIVATE.search(k)}
    if isinstance(value, (list,tuple)): return [safe(v,depth+1) for v in value[:64]]
    return '[omitted]'


def error_frames(error):
    # No exception message, source line, locals, full paths, or source code.
    # walk_tb also avoids linecache reading cloud-backed source during failure.
    return {'error_type': type(error).__name__, 'frames': [
        {'file': Path(frame.f_code.co_filename).name, 'line': line, 'function': frame.f_code.co_name}
        for frame,line in deque(traceback.walk_tb(error.__traceback__),maxlen=20)]}


def exit_reason(report):
    if report.get('error_type'): return 'local_exception'
    if report.get('interrupted'): return 'user_interrupt'
    if report.get('application_close'): return 'server_application_close'
    if report.get('termination'):
        return 'transport_idle_timeout' if report['termination'].get('reason') == 'Idle timeout' else 'transport_closed'
    if report.get('duration_limit_reached'): return 'configured_duration_reached'
    if report.get('traffic_limit_reached'): return 'configured_traffic_limit_reached'
    if report.get('local_close_requested'):
        if any(report.get(key,{}).get('close_called') for key in ('character_walk','experimental_physics_walk')):
            return 'walk_exercise_finished'
        return 'local_close_requested'
    return 'unknown'


_COUNTERS = (
    'datagrams_sent', 'datagrams_received', 'bytes_received', 'application_datagrams',
    'quic_keepalives_sent', 'quic_keepalives_acknowledged',
    'last_udp_receive_at_seconds', 'last_udp_send_at_seconds', 'last_quic_ack_at_seconds',
    'last_application_ping_at_seconds', 'application_ping_requests_received',
    'application_ping_responses_received', 'application_ping_replies_sent',
    'application_ping_replies_pending', 'application_ping_requests_unsupported',
    'application_ping_queue_overflows', 'application_ping_telemetry_blocks',
    'application_ping_telemetry_blocks_by_provider', 'application_ping_last_telemetry_block',
    'application_ping_missing_providers', 'application_ping_replies_implemented',
    'certificate_pin_verified', 'quic_handshake_completed', 'webtransport_session_established',
    'game_account_accepted', 'player_spawned', 'world_packets_decoded',
    'client_environment_challenges_received', 'client_environment_responses_sent',
    'client_environment_challenge_handling', 'local_scheduler',
    'application_close', 'termination', 'duration_limit_reached', 'traffic_limit_reached',
    'local_close_requested', 'interrupted', 'error_type', 'elapsed_seconds', 'session_age_seconds')


def public_state(report):
    return safe({key: report[key] for key in _COUNTERS if key in report})


class NullDiagnostics:
    enabled = False
    def emit(self, *args, **kwargs): pass
    def update(self, *args, **kwargs): pass
    def exception(self, *args, **kwargs): pass
    @contextmanager
    def stage(self, *args, **kwargs): yield


NULL = NullDiagnostics()
_current = ContextVar('session_diagnostics', default=NULL)


def current(): return _current.get()


class SessionDiagnostics:
    enabled = True

    def __init__(self, directory, *, level='verbose', max_bytes=8*1024*1024,
                 backups=4, queue_limit=8192, heartbeat=1.):
        if level not in ('normal','verbose') or min(max_bytes,queue_limit) < 1 or not 0 <= backups <= 20 or heartbeat <= 0:
            raise ValueError('Invalid diagnostics settings')
        directory=Path(directory); directory.mkdir(parents=True, exist_ok=True)
        self.run_id=uuid.uuid4().hex[:12]
        self.path=directory/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+self.run_id+'.jsonl')
        self.level=level; self.max_bytes=max_bytes; self.backups=backups
        self.queue=deque(); self.queue_limit=queue_limit; self.dropped=0
        self.condition=threading.Condition(); self.stopping=False; self.failure=None
        self.started=time.monotonic(); self.heartbeat=heartbeat
        self.owner=threading.get_ident(); self.active='session.setup'; self.active_since=self.started
        self.latest={}; self.sequence=0; self.size=0
        self.last_progress=self.started
        self.file=self._open()
        self.thread=threading.Thread(target=self._writer,name='session-log-writer',daemon=True)
        self.thread.start()

    def _open(self):
        return os.fdopen(open_nofollow(self.path, os.O_WRONLY|os.O_CREAT|os.O_EXCL), 'w', encoding='utf-8')

    def _record(self, event, fields):
        self.sequence+=1
        return {'utc': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                'elapsed_s': round(time.monotonic()-self.started,6), 'seq': self.sequence,
                'event': event, 'stage': self.active, **safe(fields)}

    def emit(self, event, *, verbose=False, **fields):
        if verbose and self.level!='verbose': return
        with self.condition:
            if self.stopping or self.failure: return
            record=self._record(event,fields)
            if len(self.queue)>=self.queue_limit:
                self.queue.popleft(); self.dropped+=1
            self.queue.append(record); self.condition.notify()

    def update(self, report):
        state=public_state(report)
        with self.condition:
            self.latest=state; self.last_progress=time.monotonic()

    def exception(self, event, error): self.emit(event, **error_frames(error))

    @contextmanager
    def stage(self, name, **fields):
        started=time.monotonic()
        with self.condition:
            previous=(self.active,self.active_since)
            self.active=name; self.active_since=started
        self.emit('stage.enter',verbose=True,**fields)
        try: yield
        except BaseException as error:
            self.exception('stage.error',error)
            raise
        finally:
            elapsed=time.monotonic()-started
            self.emit('stage.exit',verbose=elapsed<.25, duration_ms=round(elapsed*1000,3), slow=elapsed>=.25)
            with self.condition: self.active,self.active_since=previous

    def _write(self, record):
        line=json.dumps(record,ensure_ascii=True,separators=(',',':'))+'\n'
        if self.size and self.size+len(line)>self.max_bytes:
            self.file.close()
            if self.backups:
                for n in range(self.backups,0,-1):
                    source=Path(str(self.path)+f'.{n-1}') if n>1 else self.path
                    if source.exists(): os.replace(source,Path(str(self.path)+f'.{n}'))
            else: self.path.unlink()
            self.file=self._open(); self.size=0
        self.file.write(line); self.size+=len(line)

    def _writer(self):
        next_heartbeat=time.monotonic()+self.heartbeat
        try:
            while True:
                with self.condition:
                    if not self.queue and not self.stopping:
                        self.condition.wait(max(0,next_heartbeat-time.monotonic()))
                    batch=list(self.queue); self.queue.clear()
                    stopping=self.stopping
                    now=time.monotonic()
                    # Record snapshot/stack in the background even if a callback
                    # is blocked and the game loop cannot produce a heartbeat.
                    heartbeat=None
                    if now>=next_heartbeat:
                        duration=now-self.active_since
                        fields={'state':self.latest,'stage_duration_s':round(duration,3),
                                'state_age_s':round(now-self.last_progress,3),
                                'dropped_events':self.dropped}
                        if duration>=2 and (self.active!='transport.loop' or now-self.last_progress>=2):
                            frame=sys._current_frames().get(self.owner)
                            frames=[]
                            while frame is not None and len(frames)<16:
                                frames.append({'file':Path(frame.f_code.co_filename).name,
                                               'line':frame.f_lineno,'function':frame.f_code.co_name})
                                frame=frame.f_back
                            fields['thread_frames']=frames
                        heartbeat=self._record('heartbeat',fields)
                        next_heartbeat=now+self.heartbeat
                for record in batch: self._write(record)
                if heartbeat: self._write(heartbeat)
                if heartbeat or stopping: self.file.flush()
                if stopping: break
        except Exception as error:
            self.failure=type(error).__name__
            print(json.dumps({'diagnostic_log_error':self.failure}), file=sys.stderr, flush=True)
        finally:
            try: self.file.close()
            except OSError: pass

    def close(self):
        self.emit('logging.stopped',dropped_events=self.dropped)
        with self.condition:
            self.stopping=True; self.condition.notify()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            print(json.dumps({'diagnostic_log_error':'writer_flush_timeout'}),file=sys.stderr,flush=True)


@contextmanager
def session_logging(root, *, level='verbose', **configuration):
    if level=='off':
        yield NULL
        return
    try: logger=SessionDiagnostics(Path(root)/'outputs/logs',level=level)
    except OSError as error:
        print(json.dumps({'diagnostic_log_error':type(error).__name__}),file=sys.stderr,flush=True)
        yield NULL
        return
    token=_current.set(logger)
    print(json.dumps({'diagnostic_log':str(logger.path),'log_level':level}),flush=True)
    logger.emit('session.start',configuration=configuration,python=sys.version.split()[0],platform=sys.platform)
    try: yield logger
    except BaseException as error:
        logger.exception('session.failed',error)
        raise
    finally:
        logger.close(); _current.reset(token)
