#!/usr/bin/env python3
"""Open a bounded private-server chat check; only the human submits chat.

Run state stays in its own owner-only directory. The normal saved alt cookie is
read by RobloxSession, never copied or printed. No message is sent on startup.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile

import game_session
import join_allocation
import native_session
import probe_transport
from roblox_session import ROOT, SessionError
from runtime_preflight import check_runtime_dependencies
from web_console import WebConsole


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration', type=int, default=300)
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    if not 30 <= args.duration <= 600:
        parser.error('Use a bounded duration from 30 to 600 seconds')
    if not 0 <= args.port <= 65535:
        parser.error('Port must be 0..65535')
    preflight = check_runtime_dependencies()
    print(json.dumps({'runtime_preflight': preflight}), flush=True)
    if preflight.get('ok') is not True:
        raise SessionError('Offline runtime dependency check failed; no join attempted')
    invite = json.loads((ROOT / 'secrets/private-server-target.json').read_text())
    if invite.get('status') != 'Valid' or type(invite.get('placeId')) is not int:
        raise SessionError('The saved private-server invite is not valid')
    run = Path(tempfile.mkdtemp(prefix='typed-chat-check-', dir=ROOT / 'secrets'))
    (run / 'secrets').mkdir(mode=0o700)
    (run / 'outputs/gamejoin').mkdir(parents=True)
    join_allocation.write_private(run / 'secrets/private-server-target.json', invite)
    target = {'selection': 'user_selected_private_server', 'is_playable': True,
              'place_id': invite['placeId']}
    (run / 'outputs/gamejoin/target-selection.json').write_text(json.dumps(target))
    flags = []
    if invite['placeId'] == 6252985844:
        spec = json.loads((ROOT / 'secrets/private-client-framework-rpc.json').read_text())
        join_allocation.write_private(run / 'secrets/private-client-framework-rpc.json', spec)
        flags.append('--framework-init')
    # macOS limits Unix socket paths to 104 bytes. Keep the actual captures in
    # secrets/, with an owner-only temporary alias for short socket filenames.
    short_directory = tempfile.TemporaryDirectory(prefix='rbx-chat-', dir='/tmp')
    short_root = Path(short_directory.name) / 'run'
    short_root.symlink_to(run, target_is_directory=True)
    # These four modules own allocation/session files. Code, pinned binaries,
    # and the existing alt credential reader retain the normal project root.
    for module in (game_session, join_allocation, native_session, probe_transport):
        module.ROOT = short_root
    console = WebConsole(socket_path=short_root / 'secrets/client-script.sock', port=args.port)
    reference = {'run_path': str(run), 'url': console.url,
                 'socket_path': str(console.socket_path)}
    join_allocation.write_private(ROOT / 'secrets/typed-chat-check-reference.json', reference)
    print(json.dumps({'console_url': console.start(open_browser=False),
                      'message_sent_automatically': False}), flush=True)
    sys.argv = ['game_session.py', '--refresh', '--protocol-sync', '--luau-range',
                '--ticket', '--time-sync', '--bytecode-auth', '--require-spawn',
                '--duration', str(args.duration), '--progress', '--script-console', *flags]
    try:
        return game_session.main(report_name='typed-chat-check', account_root=ROOT)
    except Exception as error:
        join_allocation.write_private(run / 'secrets/validation-error.json',
                                      {'error_type': type(error).__name__, 'error': str(error)})
        raise
    finally:
        console.close()
        short_root.unlink()
        short_directory.cleanup()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        # Third-party error strings may contain connection details.
        print(json.dumps({'live_check_error_type': type(error).__name__}), flush=True)
        raise SystemExit(2)
