#!/usr/bin/env python3
"""Replay a private capture without exposing user IDs or property values."""
import argparse
import base64
import collections
import json
from pathlib import Path

from roblox_session import ROOT
from world_replication import World
from world_schema import parse_schema
from replication_ping import ping_packet


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', default='outputs/gamejoin/full-ticket-first.json')
    args = parser.parse_args()
    report = json.loads((ROOT / args.report).read_text())
    world = None
    user = json.loads((ROOT / 'secrets/last-join.json').read_text())['body']['joinScript']['UserId']
    result = {'source_run_id': report['run_id'], 'place_id': report['place_id'],
              'all_replication_packets_decoded': True, 'application_pings_received': 0,
              'character_reference_ever_received': False}
    for i, entry in enumerate(json.loads((ROOT / report['private_capture']).read_text())):
        if entry['direction'] != 'received' or entry.get('application') != 4:
            continue
        payload = base64.b64decode(entry['data_base64'])
        if entry.get('channel') == 3 and payload[:1] == b'\x83':
            result['application_pings_received'] += len(ping_packet(payload))
            continue
        if entry.get('channel') != 1: continue
        if payload[:1] == b'\x97':
            world = World(parse_schema(payload))
            continue
        if payload[:1] == b'\x81':
            if world is None: raise ValueError('Globals arrived before schema')
            world.globals(payload)
            continue
        if payload[:1] != b'\x83': continue
        if world is None: raise ValueError('Capture lacks the replication schema')
        try:
            world.packet(payload)
            result['character_reference_ever_received'] |= world.avatar(user)['evidence']['character_reference_received']
        except (ValueError, IndexError, KeyError) as error:
            result.update({'all_replication_packets_decoded': False, 'stopped_at_capture_index': i,
                           'packet_bytes': len(payload), 'error_type': type(error).__name__,
                           'error': str(error), 'context': world.context})
            break
    if world is None: raise ValueError('Capture lacks the replication schema')
    players = [v for v in world.instances.values() if world.schema['classes'][v['class']]['name'] == 'Player']
    result.update(world.avatar(user)['evidence'])
    result.update({'instances': len(world.instances), 'items': dict(world.items),
                   'shared_strings': len(world.shared), 'tags': world.tags,
                   'game_account_accepted': any(v['properties'].get('UserId') == user for v in players),
                   'classes': dict(collections.Counter(world.schema['classes'][v['class']]['name'] for v in world.instances.values()))})
    print(json.dumps(result, indent=2))
    (ROOT / 'outputs/gamejoin/world-decode.json').write_text(json.dumps(result, indent=2) + '\n')
    return 0 if result['all_replication_packets_decoded'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
