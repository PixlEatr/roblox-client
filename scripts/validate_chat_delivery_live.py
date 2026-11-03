"""One explicitly requested chat-input test with read-only QUIC ACK tracking.

Requires --send-test; no background retries. Captures and allocation stay in an
isolated private directory while the original account lease is held throughout.
Transport acknowledgment never counts as successful application delivery.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

import game_session
import join_allocation
import native_session
import probe_transport
from chat_console import ChatConsole
from client_session_lease import acquire_session_lease
from game_wire import frame
from reliable_delivery_probe import ReliableDeliveryProbe
from roblox_session import ROOT, RobloxSession, SessionError
from runtime_preflight import check_runtime_dependencies
from script_console import send_request


class ObservedSession(native_session.NativeSession):
    latest = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ObservedSession.latest = self
        self._watch_chat_send = False
        self._chat_watch = None

    def send(self, channel, payload, application=4):
        watched = self._watch_chat_send and application == 4 and channel == 1
        if watched and self._chat_watch is not None:
            raise RuntimeError('The single-message probe rejected an additional send')
        result = super().send(channel, payload, application)
        if watched:
            stream = self.quic._streams.get(self.outgoing[(application, channel)])
            sender = stream.sender if stream is not None else None
            stop = getattr(sender, '_buffer_stop', None)
            if type(stop) is int:
                self._chat_watch = (sender, ReliableDeliveryProbe(stop-len(frame(payload)), stop))
                self.update_chat_delivery()
        return result

    def update_chat_delivery(self):
        if self._chat_watch is None:
            return
        sender, probe = self._chat_watch
        previous = self.report.get('chat_transport_delivery', {})
        current = probe.summary(sender)
        if previous.get('transport_acknowledged'):
            # Preserve observed evidence even if the connection later closes.
            return
        if current['transport_acknowledged']:
            current['ack_observed_at_seconds'] = round(time.monotonic()-self.started, 6)
        self.report['chat_transport_delivery'] = current

    def _drain_events(self, on_ready, on_packet):
        super()._drain_events(on_ready, on_packet)
        self.update_chat_delivery()


class ObservedChatConsole(ChatConsole):
    ready_after_spawn_seconds = None

    def tick(self, client, world):
        delay = self.ready_after_spawn_seconds
        spawned_at = client.report.get('spawn_verified_at_seconds')
        if (not self.chat.ready_sent and delay is not None
                and (spawned_at is None or client.started is None
                     or time.monotonic()-client.started < spawned_at+delay)):
            # Diagnostic only: retain received control state while withholding
            # the one READY signal. Never resend it or change production policy.
            self.client = client
            if not self.failure:
                try:
                    self.chat.poll(world)
                except Exception as error:
                    self.failure = type(error).__name__
            self.console.poll()
            client.report['chat'] = {**self.chat.summary(), 'healthy': self.failure is None}
            return
        super().tick(client, world)
        if self.chat.ready_sent and 'chat_ready_sent_at_seconds' not in client.report:
            client.report['chat_ready_sent_at_seconds'] = round(time.monotonic()-client.started, 6)

    def runtime_submit(self, request):
        self.client._watch_chat_send = True
        try:
            return super().runtime_submit(request)
        finally:
            self.client._watch_chat_send = False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send-test', action='store_true', required=True,
                        help='Send exactly one ordinary local chat-input test message')
    parser.add_argument('--place', type=int, default=123974602339071)
    parser.add_argument('--channel', choices=('RBXGeneral', 'RBXGlobal'), default='RBXGeneral')
    parser.add_argument('--after-spawn-seconds', type=int, choices=range(0, 21),
                        help='Wait this many seconds after replicated character spawn before the one send')
    parser.add_argument('--ready-after-spawn-seconds', type=int, choices=range(0, 11),
                        help='Diagnostic only: defer the single chat-loaded signal until after spawn')
    args = parser.parse_args()
    preflight = check_runtime_dependencies()
    print(json.dumps({'runtime_preflight': preflight}), flush=True)
    if not preflight.get('ok'):
        raise SessionError('Offline runtime preflight failed')
    with acquire_session_lease(ROOT):
        detail = RobloxSession().request('games.roblox.com',
            '/v1/games/multiget-place-details?placeIds=' + str(args.place))
        rows = detail.get('data')
        if (detail['http_status'] != 200 or not isinstance(rows, list)
                or len(rows) != 1 or rows[0].get('placeId') != args.place
                or rows[0].get('isPlayable') is not True):
            raise SessionError('Target access was not verified')
        run = Path(tempfile.mkdtemp(prefix='chat-delivery-check-', dir=ROOT/'secrets'))
        (run/'secrets').mkdir(mode=0o700)
        (run/'outputs/gamejoin').mkdir(parents=True)
        (run/'outputs/gamejoin/target-selection.json').write_text(json.dumps({
            'selection': 'user_selected_place', 'is_playable': True, 'place_id': args.place}))
        join_allocation.write_private(ROOT/'secrets/chat-delivery-check-reference.json',
                                      {'run_path': str(run)})
        with tempfile.TemporaryDirectory(prefix='rbx-chat-ack-', dir='/tmp') as short:
            alias = Path(short)/'run'
            alias.symlink_to(run, target_is_directory=True)
            modules = (game_session, join_allocation, native_session, probe_transport)
            roots = [module.ROOT for module in modules]
            old_session, old_chat = game_session.NativeSession, game_session.ChatConsole
            old_ready_delay = ObservedChatConsole.ready_after_spawn_seconds
            ObservedChatConsole.ready_after_spawn_seconds = args.ready_after_spawn_seconds
            old_argv = sys.argv
            for module in modules:
                module.ROOT = alias
            game_session.NativeSession, game_session.ChatConsole = ObservedSession, ObservedChatConsole
            stop = threading.Event()
            submission = {'attempted': False, 'retried': False, 'physical_keyboard_test': False,
                          'channel': args.channel}
            if args.after_spawn_seconds is not None:
                submission['after_spawn_delay_seconds'] = args.after_spawn_seconds
            if args.ready_after_spawn_seconds is not None:
                submission['ready_after_spawn_delay_seconds'] = args.ready_after_spawn_seconds

            def submit_once():
                deadline = time.monotonic()+45
                while time.monotonic() < deadline and not stop.is_set():
                    if args.after_spawn_seconds is not None:
                        client = ObservedSession.latest
                        spawned_at = (client.report.get('spawn_verified_at_seconds')
                                      if client is not None else None)
                        if (spawned_at is None or client.started is None
                                or time.monotonic()-client.started < spawned_at+args.after_spawn_seconds):
                            stop.wait(.2)
                            continue
                    try:
                        ready = send_request(alias/'secrets/client-chat.sock', {'op': 'channels'})
                    except (OSError, ValueError):
                        stop.wait(.2)
                        continue
                    channels = ready.get('channels', [])
                    if not ready.get('initialized') or not any(
                            c.get('name') == args.channel and c.get('member')
                            and c.get('can_send_received') is not False for c in channels):
                        stop.wait(.2)
                        continue
                    submission['attempted'] = True
                    client = ObservedSession.latest
                    if client is not None and client.started is not None:
                        submission['submitted_at_seconds'] = round(time.monotonic()-client.started, 6)
                        submission['spawn_observed_at_seconds'] = client.report.get('spawn_verified_at_seconds')
                    try:
                        result = send_request(alias/'secrets/client-script.sock', {
                            'op': 'user_chat', 'channel': args.channel,
                            'text': ('Global chat connection check' if args.channel == 'RBXGlobal' else
                                'Chat delayed connection check'
                                if args.after_spawn_seconds is not None else 'Chat protocol connection check')})
                        submission['api_ok'] = result.get('ok') is True
                        job_id = result.get('job_id')
                        while type(job_id) is int and not stop.is_set() and time.monotonic() < deadline:
                            state = send_request(alias/'secrets/client-script.sock', {'op': 'status', 'job_id': job_id})
                            if state.get('completed') or state.get('state') in ('completed', 'failed'):
                                submission['runtime_completed'] = True
                                submission['runtime_ok'] = state.get('ok') is True
                                values = state.get('values', [])
                                submission['runtime_success_status'] = bool(values and values[0] == 'Success')
                                break
                            stop.wait(.2)
                    except Exception as error:
                        submission['error_type'] = type(error).__name__
                    return

            worker = threading.Thread(target=submit_once, daemon=True)
            try:
                sys.argv = ['game_session.py', '--refresh', '--protocol-sync', '--luau-range',
                    '--ticket', '--time-sync', '--bytecode-auth', '--require-spawn',
                    '--duration', '55', '--progress', '--script-console']
                worker.start()
                return game_session.main(report_name='chat-delivery-check')
            finally:
                stop.set()
                worker.join(timeout=9)
                client = ObservedSession.latest
                result = {'submission': submission, 'delivery_verified': False}
                if client is not None:
                    client.update_chat_delivery()
                    for key in ('chat_transport_delivery', 'chat', 'player_spawned',
                                'elapsed_seconds', 'local_close_requested', 'application_close',
                                'chat_ready_sent_at_seconds', 'spawn_verified_at_seconds',
                                'framework_request_at_seconds', 'framework_reply_at_seconds'):
                        if key in client.report:
                            result[key] = client.report[key]
                    result['delivery_verified'] = client.report.get('chat', {}).get('server_success', 0) > 0
                (ROOT/'outputs/chat-analysis/transport-delivery-live-result.json').write_text(
                    json.dumps(result, indent=2)+'\n')
                print(json.dumps(result), flush=True)
                for module, previous in zip(modules, roots):
                    module.ROOT = previous
                game_session.NativeSession, game_session.ChatConsole = old_session, old_chat
                ObservedChatConsole.ready_after_spawn_seconds = old_ready_delay
                sys.argv = old_argv
                alias.unlink()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'live_check_error_type': type(error).__name__}), flush=True)
        raise SystemExit(2)
