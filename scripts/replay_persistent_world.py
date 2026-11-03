"""Replay a private saved world stream into the local VM; never opens a socket."""
import argparse
import base64
import json
import os
from pathlib import Path
import time

from persistent_runtime import PersistentClientScriptRuntime
from world_replication import World
from world_schema import parse_schema

ROOT = Path(__file__).resolve().parents[1]


def replay(capture, user_id):
    world = None
    runtime = PersistentClientScriptRuntime(lambda: world, user_id)
    report = {'mode': 'offline_world_replay', 'network_used': False,
              'world_packets': 0, 'watcher_started': False, 'task_errors': 0}

    def check(result):
        if result is None:
            return
        # synchronize() can run callbacks before poll() returns its own result.
        # Count both responses, including errors from that earlier update.
        report['task_errors'] = runtime.stats.get('task_errors_total', 0)
        if not result['ok'] or report['task_errors']:
            private=ROOT/'secrets/persistent-replay-last-failure.json'
            with os.fdopen(os.open(private,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'w') as stream:
                os.fchmod(stream.fileno(),0o600)
                json.dump({'result':result,'messages':list(runtime.messages),'counts':report},stream)
            raise RuntimeError('Persistent replay or callback failed')

    try:
        for row in capture:
            if (row.get('direction') != 'received' or row.get('application') != 4
                    or row.get('channel') != 1):
                continue
            payload = base64.b64decode(row['data_base64'], validate=True)
            if payload[:1] == b'\x97':
                if world is not None:
                    raise ValueError('Replay contains a second world schema')
                world = World(parse_schema(payload))
            elif world is not None and payload[:1] == b'\x81':
                world.globals(payload)
            elif world is not None and payload[:1] == b'\x83':
                world.packet(payload)
                report['world_packets'] += 1
            else:
                continue
            if not report['watcher_started']:
                if not world.avatar(user_id)['evidence']['own_player_parented_to_players']:
                    continue
                check(runtime.execute((ROOT / 'runtime/scripts/persistent_watch.luau').read_text()))
                report['watcher_started'] = True
            else:
                check(runtime.poll(force=True))

        if not report['watcher_started']:
            raise RuntimeError('No local player was available in this capture')
        result = runtime.execute((ROOT / 'runtime/scripts/persistent_status.luau').read_text())
        check(result)
        deadline = time.monotonic() + 8
        while not result.get('completed') and time.monotonic() < deadline:
            time.sleep(.01)
            check(runtime.poll(force=True))
            result = runtime.status(result['job_id'])
            check(result)
        if not result.get('completed'):
            raise RuntimeError('Persistent observer did not finish before deadline')
        values = result['values']
        report.update(observer_completed=True, ticks=values[0], character_events=values[1],
                      character_already_present=values[2], remote_events=values[3],
                      player_identity_retained=values[4], world_revision=values[5],
                      **{k: runtime.stats[k] for k in ('server_actions', 'server_events',
                         'unresolved_server_events') if k in runtime.stats})
        return report
    finally:
        runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--account-context', type=Path, default=ROOT / 'secrets/last-join.json')
    parser.add_argument('--report', type=Path,
                        default=ROOT / 'outputs/audit/persistent-offline-replay.json')
    args = parser.parse_args()
    # Read only the account ID from private context; never publish its contents.
    user_id = json.loads(args.account_context.read_text())['body']['joinScript']['UserId']
    report = replay(json.loads(args.capture.read_text()), user_id)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
