#!/usr/bin/env python3
"""Optional bounded normal DummyService probe; separate from game replication."""
import argparse
from contextlib import redirect_stdout
import io
import json
import time

from dummy_sync import APPLICATION, REQUEST_CHANNEL, TimeSyncProbe
from game_wire import CONTROL_CHANNEL
from join_allocation import main as refresh_allocation
from native_session import NativeSession
from roblox_session import SessionError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh', action='store_true', help='Refresh the existing selected target through normal allocation')
    parser.add_argument('--duration', type=int, default=8, choices=range(1, 11), metavar='1..10')
    args = parser.parse_args()
    if args.refresh:
        with redirect_stdout(io.StringIO()):
            result = refresh_allocation()
        if result != 0:
            raise SessionError('A fresh ready allocation was not issued')
    session = NativeSession(duration=args.duration)
    probe = TimeSyncProbe()
    from session_wire_clock import SessionWireClock
    wire_clock = SessionWireClock()
    session.report.update({'probe_service': 'DummyService', 'probe_application': APPLICATION,
                           'time_sync_clock_offset_estimated': False,
                           'time_sync_application_started': False, 'time_sync_channels_opened': False})

    def ready(client):
        # WT success produces native connection event 0; the app wrapper sends
        # CTRL0000 then immediately passes event 0 to DummyClient (10211b43c).
        # Native startup does not wait for a server control echo.
        client.send(CONTROL_CHANNEL, b'\0\0', application=APPLICATION)
        client.open_channel(APPLICATION, 1)
        client.open_unreliable_channel(APPLICATION, 2)
        client.open_unreliable_channel(APPLICATION, 3)
        client.report['time_sync_application_started'] = True
        client.report['time_sync_channels_opened'] = True
        probe.start(time.monotonic())

    def packet(client, application, channel, payload):
        if application == APPLICATION and channel == REQUEST_CHANNEL:
            probe.receive(payload, time.monotonic())
            client.report.update(probe.public_report())

    def tick(client, now):
        payload = probe.due(now, wire_clock.microseconds())
        if payload is not None:
            client.send_unreliable(REQUEST_CHANNEL, payload, application=APPLICATION)
            client.report.update(probe.public_report())

    # Existing transport capture remains private, including opaque reply words.
    session.report.update(probe.public_report())
    result = session.run(ready, packet, report_name='time-sync-probe', on_tick=tick)
    keys = ('certificate_pin_verified', 'quic_handshake_completed', 'webtransport_session_established',
            'time_sync_application_started', 'time_sync_channels_opened',
            'time_sync_requests_sent', 'time_sync_echoed_responses',
            'time_sync_measured_rtt_ms', 'time_sync_clock_offset_estimated', 'application_close',
            'termination', 'elapsed_seconds')
    print(json.dumps({key: result[key] for key in keys if key in result}, indent=2))
    return 0 if (result.get('time_sync_echoed_responses', 0) > 0
                 and not result.get('error_type') and not result.get('application_close')) else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'error_type': type(error).__name__}))
        raise SystemExit(1)
