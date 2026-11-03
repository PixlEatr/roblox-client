#!/usr/bin/env python3
"""Run the pinned user-supplied sUNC suite in the custom Luau runtime.

Defaults to an offline fixture with network/clipboard writes denied by macOS.
The requested place ID is recorded, never injected into the fixture as if joined.
Supply an actual captured world with --snapshot to inspect its real bindings.
This runner does not log into Roblox, join a game, or manufacture a test score.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]
FIXTURES=ROOT/'runtime/tests/sunc'


def actual_place(snapshot):
    # Only use an actual DataModel property; requested metadata is not evidence.
    for node in snapshot.get('nodes',[]):
        if node.get('class_name')=='DataModel':
            value=node.get('properties',{}).get('PlaceId')
            if type(value) is int:return value
    return None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',type=Path,default=ROOT/'runtime/build/unc-headless-luau')
    parser.add_argument('--snapshot',type=Path,default=ROOT/'runtime/tests/unc_integration_snapshot.json')
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/runtime-analysis/sunc/latest.json')
    parser.add_argument('--require-place',action='store_true',help='Require the snapshot to contain the requested real PlaceId')
    parser.add_argument('--timeout-ms',type=int,default=30000)
    args=parser.parse_args()
    if not 1<=args.timeout_ms<=30000:parser.error('timeout must be 1..30000 ms')
    manifest=json.loads((FIXTURES/'manifest.json').read_text());source=FIXTURES/'suite.luau'
    if not source.is_file():parser.error('Add the optional suite.luau identified by runtime/tests/sunc/manifest.json first')
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    if digest!=manifest['sha256']:parser.error('pinned sUNC source changed; review it before updating its manifest')
    snapshot=json.loads(args.snapshot.read_text());place=actual_place(snapshot)
    if args.require_place and place!=manifest['requested_place_id']:
        parser.error('snapshot does not establish the requested place; join/capture it first')
    environment=os.environ.copy();environment['RCR_LUAU_LARGE_TEST']='1'
    # The VM's file APIs are virtual. Deny native external effects as well while
    # evaluating an obfuscated test against a supplied offline world.
    profile='(version 1)(allow default)(deny network*)(deny file-write*)(deny process-exec (literal "/usr/bin/pbcopy"))'
    # Python supplies the wall deadline here; enter the worker directly because
    # macOS denies its supervisor's nested spawn under this restrictive profile.
    command=['/usr/bin/sandbox-exec','-p',profile,str(args.host.resolve()),'--worker','--source',str(source),
             '--world-snapshot',str(args.snapshot.resolve()),'--timeout-ms',str(args.timeout_ms),'--memory-mb','512']
    started=time.monotonic()
    try:
        completed=subprocess.run(command,capture_output=True,env=environment,timeout=args.timeout_ms/1000+8)
        try:result=json.loads(completed.stdout)
        except ValueError:result={'ok':False,'error':'worker returned no valid JSON','stderr':completed.stderr.decode(errors='replace')[:4096]}
        code=completed.returncode
    except subprocess.TimeoutExpired:
        result={'ok':False,'error':'sUNC runner wall deadline exceeded','timed_out':True};code=124
    report={'source':manifest,'host':str(args.host.resolve()),'host_sha256':hashlib.sha256(args.host.read_bytes()).hexdigest(),
            'snapshot':str(args.snapshot.resolve()),'snapshot_sha256':hashlib.sha256(args.snapshot.read_bytes()).hexdigest(),
            'requested_place_id':manifest['requested_place_id'],'snapshot_place_id':place,
            'place_verified':place==manifest['requested_place_id'],'mode':'offline_restricted',
            'elapsed_seconds':time.monotonic()-started,'exit_code':code,'result':result,
            'score':None,'live_server_acceptance':False}
    # Obfuscated output is preserved verbatim; process completion is not a score.
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(result.get('output',''),end='')
    for event in result.get('console_events',[]):
        if event.get('kind')=='print':print(event.get('text',''),end='')
    if result.get('error'):print('sUNC stopped:',result['error'])
    print(f'Elapsed: {report["elapsed_seconds"]:.3f}s; mode: offline; score: not established')
    print(f'Report: {args.output.resolve()}')
    return 0 if code==0 and result.get('ok') else 1


if __name__=='__main__':raise SystemExit(main())
