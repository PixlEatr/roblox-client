#!/usr/bin/env python3
"""Run the game handshake and watch the world come in."""
import argparse
from contextlib import redirect_stdout
import io
import json
import time

from game_wire import CONTROL_CHANNEL, issued_early_auth, feature_configuration, protocol_sync
from join_allocation import main as refresh_allocation, write_private
from native_session import NativeSession
from login_packets import ticket_packet, verification_packet, server_id_packet
from roblox_session import ROOT, SessionError
from world_replication import World
from world_schema import parse_schema
from replication_out import streaming_ready, MarkerQueue
from replication_ping import ping_packet
from dummy_sync import APPLICATION as CLOCK_APPLICATION, REQUEST_CHANNEL as CLOCK_CHANNEL, TimeSyncProbe
from replication_rpc import FrameworkInitRpc
from bytecode_check import parse_program_check, BytecodeAuthHandler, BytecodeAuthError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--duration", type=int, default=12)
    parser.add_argument("--protocol-sync", action="store_true", help="Also send the recovered version/player-info packet")
    parser.add_argument("--luau-range", action="store_true", help="Negotiate the existing static reader's bytecode versions 13–14")
    parser.add_argument("--ticket", action="store_true", help="Send the issued account ticket and remaining normal connection packets")
    parser.add_argument("--streaming-ready", action="store_true", help="Report readiness after receiving the character's surrounding region")
    parser.add_argument("--progress", action="store_true", help="Print sanitized connection milestones while the test is running")
    parser.add_argument("--time-sync", action="store_true", help="Also exercise the normal native clock service with five bounded requests")
    parser.add_argument("--framework-init", action="store_true", help="Make the single statically verified Nothingness framework-state request")
    parser.add_argument('--bytecode-auth', action='store_true',
                        help='Verify and execute the server-issued signed program, then send its result')
    parser.add_argument('--require-spawn', action='store_true',
                        help='Require a server-replicated own Character for a successful exit')
    args = parser.parse_args()
    if args.luau_range and not args.protocol_sync:
        parser.error("--luau-range requires --protocol-sync")
    if args.ticket and not (args.protocol_sync and args.luau_range):
        parser.error("--ticket requires --protocol-sync --luau-range")
    if args.framework_init and not args.ticket:
        parser.error('--framework-init requires --ticket')
    if (args.bytecode_auth or args.require_spawn) and not args.ticket:
        parser.error('--bytecode-auth and --require-spawn require --ticket')
    if args.refresh:
        with redirect_stdout(io.StringIO()):
            result = refresh_allocation()
        if result != 0:
            allocation_report = json.loads((ROOT / 'outputs/gamejoin/allocation-check.json').read_text())
            if allocation_report.get('user_privacy_blocks_join'):
                raise SessionError('Roblox refused the join: User privacy does not allow join.')
            raise SessionError("A fresh ready allocation was not issued")
    session = NativeSession(duration=args.duration, capabilities=0x100000 if args.luau_range else 0)
    allocation_body = json.loads((ROOT / "secrets/last-join.json").read_text())["body"]
    join = allocation_body["joinScript"]
    early_auth = issued_early_auth(join["ClientTicket"])
    bytecode_auth = BytecodeAuthHandler(join['GameId']) if args.bytecode_auth else None
    session.report['client_environment_program_execution_enabled'] = bytecode_auth is not None
    if bytecode_auth is not None:
        session.report['client_environment_auth'] = bytecode_auth.summary()
    world = None
    last_state_check = 0.0
    milestones = set()
    markers = MarkerQueue()
    clock = TimeSyncProbe() if args.time_sync else None
    framework = None
    if args.framework_init:
        if session.place != 6252985844:
            raise SessionError('The verified framework request is specific to the selected Nothingness place')
        spec = json.loads((ROOT / 'secrets/private-client-framework-rpc.json').read_text())
        framework = FrameworkInitRpc(spec, join['UserId'], session.capabilities)
        session.report['framework_init_rpc'] = framework.summary()

    def milestone(name):
        if args.progress and name not in milestones:
            milestones.add(name)
            print(json.dumps({'milestone': name, 'place_id': session.place,
                              'seconds': round(time.monotonic() - session.started, 3)}), flush=True)

    def flush_markers(client):
        for reply in markers.drain(world):
            client.send(1, reply)
            client.report['replication_markers_acknowledged'] = client.report.get('replication_markers_acknowledged', 0) + 1
            client.report['marker_reply_after_initial_world'] = True
            client.report['last_marker_reply_at_seconds'] = round(time.monotonic() - client.started, 3)
            milestone('initial_world_marker_replied')

    def advance_framework(client):
        if framework is None:
            return
        request = framework.prepare(world)
        if request is not None:
            client.send(1, request)
            client.report['framework_request_sent'] = True
            client.report['framework_request_at_seconds'] = round(time.monotonic() - client.started, 3)
            milestone('framework_state_requested')
        if framework.poll(world):
            client.report['framework_reply_at_seconds'] = round(time.monotonic() - client.started, 3)
            if framework.state == 'succeeded':
                # Keep the result local. A reply alone does not mean
                # all the scripts or the character are ready.
                client.report['framework_return_value_present'] = bool(framework.result and framework.result[0] is not None)
                milestone('framework_state_reply_received')
            else:
                milestone('framework_request_server_error')
        client.report['framework_init_rpc'] = framework.summary()

    def ready(client):
        milestone('transport_connected')
        if clock is not None:
            client.send(CONTROL_CHANNEL, b'\0\0', application=CLOCK_APPLICATION)
            client.open_channel(CLOCK_APPLICATION, 1)
            client.open_unreliable_channel(CLOCK_APPLICATION, 2)
            client.open_unreliable_channel(CLOCK_APPLICATION, CLOCK_CHANNEL)
            clock.start(time.monotonic())
            client.report.update(clock.public_report())
            client.report['time_sync_clock_offset_estimated'] = False
        client.send(1, early_auth)
        client.report["early_auth_sent"] = True
        if args.protocol_sync:
            bytecode_range = (13, 14) if args.luau_range else None
            if bytecode_range is not None and not int(client.report["server_capabilities"], 16) & 0x100000:
                raise SessionError("Server does not support bytecode-range negotiation")
            client.send(1, protocol_sync(join, allocation_body.get("joinTicket"), bytecode_range))
            client.report["protocol_sync_sent"] = True
            client.report["bytecode_range"] = bytecode_range
            client.report["imported_script_execution_enabled"] = False
        if args.ticket:
            client.send(1, verification_packet())
            client.send(1, ticket_packet(join))
            client.send(1, server_id_packet())
            client.report["full_client_ticket_sent"] = True

    def packet(client, application, channel, payload):
        nonlocal world, last_state_check
        if clock is not None and application == CLOCK_APPLICATION and channel == CLOCK_CHANNEL:
            if clock.receive(payload, time.monotonic()):
                client.report.update(clock.public_report())
                milestone('native_time_sync_echo_received')
        elif application == 4 and channel == 3 and payload[:1] == b"\x83":
            pings = ping_packet(payload)
            client.report['application_pings_received'] = client.report.get('application_pings_received', 0) + len(pings)
            client.report['application_ping_replies_implemented'] = False
            client.report['last_application_ping_at_seconds'] = round(time.monotonic() - client.started, 3)
        elif application == 4 and channel == 1 and payload[:1] == b"\x93":
            config = feature_configuration(payload)
            client.report["server_feature_configuration_received"] = True
            client.report["server_feature_count"] = len(config)
        elif application == 4 and channel == 1 and payload[:1] == b"\x9b":
            check = parse_program_check(payload)
            client.report['client_environment_check'] = check.summary()
            client.report['client_environment_challenges_received'] = client.report.get('client_environment_challenges_received', 0) + 1
            if bytecode_auth is None:
                client.report['client_environment_challenge_handling'] = 'not_implemented'
                milestone('client_environment_verification_unimplemented')
            else:
                try:
                    response = bytecode_auth.handle(payload)
                except BytecodeAuthError as error:
                    client.report['client_environment_auth'] = bytecode_auth.summary()
                    client.report['client_environment_challenge_handling'] = 'failed'
                    if bytecode_auth.last_runtime_error:
                        write_private(ROOT / 'secrets/bytecode-runtime-last-error.json',
                                      bytecode_auth.last_runtime_error)
                    raise SessionError('Signed program verification/execution failed: ' + str(error)) from None
                client.send(1, response)
                client.report['client_environment_auth'] = bytecode_auth.summary()
                client.report['client_environment_responses_sent'] = client.report.get('client_environment_responses_sent', 0) + 1
                client.report['client_environment_challenge_handling'] = 'response_sent'
                client.report['client_environment_check'].update(
                    execution_implemented=True, response_implemented=True, response_bytes=len(response))
                milestone('signed_client_environment_program_answered')
        elif application == 4 and channel == 1 and payload[:1] == b"\x97":
            schema = parse_schema(payload)
            world = World(schema)
            client.report["network_schema_decoded"] = True
            client.report["schema_classes"] = len(schema['classes'])
        elif application == 4 and channel == 1 and payload[:1] == b"\x81":
            if world is None: raise SessionError('Globals arrived before their schema')
            world.globals(payload)
            client.report['globals_decoded'] = True
            client.report['server_character_auto_loads'] = bool(world.global_flags[6])
            client.report['top_containers'] = len(world.top_containers)
        elif application == 4 and channel == 1 and payload[:1] == b"\x84":
            if world is None or world.client_scope is None:
                raise SessionError('Replication marker arrived before globals')
            markers.add(payload)
            flush_markers(client)
        elif application == 4 and channel == 1 and payload[:1] == b"\x83":
            if world is None:
                raise SessionError('World data arrived before its schema')
            world.packet(payload)
            flush_markers(client)
            advance_framework(client)
            client.report['world_packets_decoded'] = client.report.get('world_packets_decoded', 0) + 1
            client.report['world_instances'] = len(world.instances)
            now = time.monotonic()
            if now - last_state_check >= 0.1:
                last_state_check = now
                evidence = world.avatar(join['UserId'])['evidence']
                client.report.update(evidence)
                if evidence['own_player_parented_to_players']:
                    milestone('own_player_in_players')
                if evidence['player_spawned']:
                    milestone('character_spawned')
                if evidence['player_spawned'] and 'spawn_verified_at_seconds' not in client.report:
                    client.report['spawn_verified_at_seconds'] = round(now - client.started, 3)
                if args.streaming_ready and evidence['spawn_region_received'] and not client.report.get('streaming_ready_sent'):
                    client.send(1, streaming_ready(world, join['UserId']))
                    client.report['streaming_ready_sent'] = True
                    client.report['streaming_ready_at_seconds'] = round(now - client.started, 3)
                if evidence['movement_ready'] and 'movement_ready_at_seconds' not in client.report:
                    client.report['movement_ready_at_seconds'] = round(now - client.started, 3)

    def tick(client, now):
        payload = clock.due(now, time.monotonic_ns() // 1000)
        if payload is not None:
            client.send_unreliable(CLOCK_CHANNEL, payload, application=CLOCK_APPLICATION)
            client.report.update(clock.public_report())

    result = session.run(ready, packet, on_tick=tick if clock is not None else None)
    keys = ("certificate_pin_verified", "quic_handshake_completed", "webtransport_session_established",
            "early_auth_sent", "protocol_sync_sent", "full_client_ticket_sent", "bytecode_range", "server_feature_count",
            "game_account_accepted", "player_spawned", "streaming_ready_sent", "root_unanchored_by_server", "movement_ready", "walk_seconds_verified",
            "own_player_parented_to_players", "character_reference_received", "world_packets_decoded",
            "time_sync_requests_sent", "time_sync_echoed_responses",
            "framework_request_sent", "framework_init_rpc", "framework_return_value_present",
            "client_environment_challenges_received", "client_environment_challenge_handling", "client_environment_check",
            "client_environment_auth", "client_environment_responses_sent",
            "application_pings_received", "application_ping_replies_implemented", "application_close", "termination", "elapsed_seconds")
    print(json.dumps({k: result[k] for k in keys if k in result}, indent=2))
    # Decide if we spawned using what the server actually sent.
    stage_complete = result.get("game_account_accepted", False) if args.protocol_sync else result.get("server_feature_configuration_received", False)
    if result.get('client_environment_challenges_received', 0):
        stage_complete = stage_complete and result.get('client_environment_challenge_handling') == 'response_sent'
    if framework is not None:
        stage_complete = stage_complete and framework.state == 'succeeded'
    if args.require_spawn:
        stage_complete = stage_complete and result.get('player_spawned', False)
    return 0 if stage_complete and not result.get("application_close") and not result.get("error_type") else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SessionError as error:
        print(json.dumps({'error_type': 'SessionError', 'error': str(error)}))
        raise SystemExit(1)
    except Exception as error:
        print(json.dumps({"error_type": type(error).__name__}))
        raise SystemExit(1)
