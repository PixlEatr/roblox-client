#!/usr/bin/env python3
"""Send user-entered chat or read server replies through the local chat socket."""
import argparse
import json

from roblox_session import ROOT
from script_console import send_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--send', metavar='TEXT', help='Send this user-entered message once')
    inputs.add_argument('--channels', action='store_true')
    inputs.add_argument('--read', action='store_true', help='Read server chat receipts')
    inputs.add_argument('--status', metavar='MESSAGE_ID')
    parser.add_argument('--channel', default='RBXGeneral')
    parser.add_argument('--metadata', default='')
    parser.add_argument('--after', type=int, default=0, help='Receipt cursor from an earlier --read')
    parser.add_argument('--socket', default=str(ROOT/'secrets/client-chat.sock'))
    args = parser.parse_args()
    if args.send is not None:
        request = {'op': 'send', 'text': args.send, 'channel': args.channel, 'metadata': args.metadata}
    elif args.channels:
        request = {'op': 'channels'}
    elif args.read:
        request = {'op': 'read', 'after': args.after}
    else:
        request = {'op': 'status', 'message_id': args.status}
    try:
        result = send_request(args.socket, request)
    except (OSError, ValueError) as error:
        result = {'ok': False, 'error_type': type(error).__name__,
                  'error': 'No usable chat console; start game_session.py with --chat-console'}
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0 if result.get('ok') else 2


if __name__ == '__main__':
    raise SystemExit(main())
