"""Offline checks for useful, bounded, credential-free disconnect timelines."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from aioquic.quic.events import ConnectionTerminated
from session_diagnostics import SessionDiagnostics, exit_reason, public_state
from native_session import NativeSession
import test_session_duration as duration_tests


class SessionDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory(prefix='session-diagnostics-')
        self.addCleanup(self.directory.cleanup)

    def logger(self, **kwargs):
        log=SessionDiagnostics(self.directory.name,**kwargs)
        self.addCleanup(log.close)
        return log

    def records(self, log):
        files=sorted(Path(self.directory.name).glob(log.path.name+'*'),reverse=True)
        return [json.loads(line) for path in files for line in path.read_text().splitlines()]

    def test_packet_metadata_and_errors_do_not_expose_bodies_or_credentials(self):
        log=self.logger()
        session=NativeSession.__new__(NativeSession)
        session.diagnostics=log; session.started=time.monotonic(); session.duration=1
        session.capture=[]; session.report={'messages':[]}
        session.record('received',4,1,b'private-body-test-cookie-value')
        log.emit('fixture',cookie='cookie-secret',ticket='ticket-secret',payload='body-secret',
                 source_text='source-secret',endpoint='private-host',value=b'opaque')
        try: raise ValueError('EXCEPTION_CONTAINS_A_PRIVATE_TICKET')
        except ValueError as error: log.exception('fixture.error',error)
        log.close(); rows=self.records(log); text=json.dumps(rows)
        for secret in ('private-body-test-cookie-value','cookie-secret','ticket-secret','body-secret',
                       'source-secret','private-host','EXCEPTION_CONTAINS_A_PRIVATE_TICKET'):
            self.assertNotIn(secret,text)
        packet=next(r for r in rows if r['event']=='application.frame')
        self.assertEqual(packet['message_id'],ord('p'))
        self.assertEqual(packet['bytes'],len(b'private-body-test-cookie-value'))
        failure=next(r for r in rows if r['event']=='fixture.error')
        self.assertEqual(failure['error_type'],'ValueError')
        self.assertEqual(failure['frames'][-1]['file'],'test_session_diagnostics.py')
        self.assertGreater(failure['frames'][-1]['line'],0)

    def test_sixty_second_local_limit_is_explicit_in_saved_timeline(self):
        log=self.logger()
        fixture=duration_tests.SessionDurationTests()
        session=fixture.session(60); session.diagnostics=log
        with redirect_stdout(io.StringIO()): report=fixture.run_with_clock(session,lambda *_:None)
        log.close()
        ended=next(r for r in self.records(log) if r['event']=='session.ended')
        self.assertEqual(ended['reason'],'configured_duration_reached')
        self.assertEqual(ended['state']['elapsed_seconds'],60)
        self.assertEqual(report['disconnect_category'],'configured_duration_reached')

    def test_peer_close_preserves_code_without_private_reason(self):
        log=self.logger()
        session=NativeSession.__new__(NativeSession)
        session.diagnostics=log; session.report={}; session.http=None; session.finished=False
        session.handle_event(ConnectionTerminated(error_code=42,frame_type=7,
            reason_phrase='sensitive peer text'),None,None)
        log.close()
        event=next(r for r in self.records(log) if r['event']=='transport.terminated')
        self.assertEqual(event['error_code'],42)
        self.assertNotIn('sensitive peer text',json.dumps(self.records(log)))
        self.assertEqual(exit_reason(session.report),'transport_closed')

    def test_distinct_exit_causes_and_ping_block_state(self):
        cases=[({'termination':{'reason':'Idle timeout'}},'transport_idle_timeout'),
               ({'application_close':{'reason_class':1,'reason_code':2},'local_close_requested':True},'server_application_close'),
               ({'traffic_limit_reached':True},'configured_traffic_limit_reached'),
               ({'interrupted':True},'user_interrupt'),
               ({'error_type':'ValueError'},'local_exception'),
               ({'local_close_requested':True,'character_walk':{'close_called':True}},'walk_exercise_finished')]
        for report,expected in cases: self.assertEqual(exit_reason(report),expected)
        state=public_state({'application_ping_last_telemetry_block':{
            'provider':'integrity','reason':'native_integrity_state_unavailable'},'ClientTicket':'private'})
        self.assertEqual(state['application_ping_last_telemetry_block']['reason'],'native_integrity_state_unavailable')
        self.assertNotIn('ClientTicket',state)

    def test_heartbeat_reports_blocked_stage_without_waiting_for_game_loop(self):
        log=self.logger(heartbeat=.02)
        observed=threading.Event(); original=log._write
        def write(record):
            original(record)
            if record['event']=='heartbeat' and record.get('thread_frames'): observed.set()
        log._write=write
        with log.stage('game.physics'):
            with log.condition: log.active_since=time.monotonic()-3
            self.assertTrue(observed.wait(1))
        log.close()
        heartbeat=next(r for r in self.records(log) if r['event']=='heartbeat')
        self.assertEqual(heartbeat['stage'],'game.physics')
        self.assertGreaterEqual(heartbeat['stage_duration_s'],3)
        self.assertTrue(heartbeat['thread_frames'])

    def test_rotation_retains_bounded_tail_and_final_cause(self):
        log=self.logger(max_bytes=1500,backups=2)
        for n in range(100): log.emit('udp.received',datagram_number=n,bytes=1400)
        log.emit('session.ended',reason='server_application_close'); log.close()
        files=list(Path(self.directory.name).glob(log.path.name+'*'))
        self.assertEqual(len(files),3)
        self.assertTrue(all(p.stat().st_size<=1500 for p in files))
        self.assertIn('session.ended',[r['event'] for r in self.records(log)])

    def test_slow_disk_does_not_block_producers_or_grow_queue(self):
        log=self.logger(queue_limit=8)
        entered=threading.Event(); release=threading.Event(); original=log._write
        def slow(record):
            entered.set(); release.wait(2); original(record)
        log._write=slow; log.emit('first')
        self.assertTrue(entered.wait(1))
        try:
            for n in range(100): log.emit('fixture',number=n)
            self.assertLessEqual(len(log.queue),8)
            self.assertGreater(log.dropped,0)
        finally: release.set()
        log.close()
        self.assertEqual(self.records(log)[-1]['event'],'logging.stopped')

    def test_normal_mode_omits_packet_spam(self):
        log=self.logger(level='normal')
        log.emit('udp.received',verbose=True,bytes=1)
        log.emit('game.ping_state',application_ping_telemetry_blocks=1)
        log.close()
        self.assertNotIn('udp.received',[r['event'] for r in self.records(log)])
        self.assertIn('game.ping_state',[r['event'] for r in self.records(log)])


if __name__=='__main__': unittest.main()
