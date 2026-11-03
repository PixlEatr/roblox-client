"""Bounded Luau worker interface for authored code and verified server programs.

Each call launches a fresh process/VM. The module never reads cookies or
captures itself. The live handler authenticates original server code before
passing its converted representation here. Build runtime/build/luau-host first.
"""

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

from session_integrity import observe_runtime_oneshot_report

HOST = Path(os.environ.get('ROBLOX_LUAU_HOST',
                           Path(__file__).resolve().parents[1] / 'runtime' / 'build' / 'luau-host'))
RUST_HOST = Path(os.environ.get('ROBLOX_LUAU_RUST_HOST',
                                Path(__file__).resolve().parents[1] / 'rust-client' / 'target' / 'release' / 'headless-luau'))
MAX_SOURCE_BYTES = 100 * 1024 * 1024


def run_source(source, arguments=(), *, timeout_ms=250, memory_mb=512,
               model_context=None, world_snapshot=None):
    """Return the host's structured result for locally supplied source.

    Engine-specific API support is limited to the host's documented surface.
    Success here says nothing about acceptance by any external server.
    """
    if not isinstance(source, str):
        raise TypeError('source must be Luau source text')
    return _run_input(source.encode('utf-8'), 'source', arguments, timeout_ms,
                      memory_mb, model_context, world_snapshot=world_snapshot)


def run_bytecode(bytecode, arguments=(), *, timeout_ms=250, memory_mb=512,
                 model_context=None, session_job_id=None):
    """Run trusted compiler-produced standard bytecode, not a Roblox envelope.

    The VM loader is not a full bytecode verifier. Resource limits do not make
    arbitrary or externally supplied bytecode safe to execute.
    """
    if not isinstance(bytecode, bytes):
        raise TypeError('bytecode must be trusted compiler-produced bytes')
    return _run_input(bytecode, 'bytecode', arguments, timeout_ms, memory_mb,
                      model_context, session_job_id)


def _run_input(encoded, input_kind, arguments, timeout_ms, memory_mb, model_context,
               session_job_id=None, world_snapshot=None):
    if len(encoded) > MAX_SOURCE_BYTES:
        raise ValueError('runtime input exceeds 100 MiB')
    if type(timeout_ms) is not int or not 1 <= timeout_ms <= 2000:
        raise ValueError('timeout_ms must be an integer from 1 to 2000')
    if type(memory_mb) is not int or not 8 <= memory_mb <= 512:
        raise ValueError('memory_mb must be an integer from 8 to 512')
    arguments = tuple(arguments)
    if len(arguments) > 64:
        raise ValueError('at most 64 numeric arguments are supported')
    for value in arguments:
        try:
            valid = type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError('arguments must be finite numbers')
    context_text = None
    world_data = None
    if world_snapshot is not None:
        if input_kind != 'source' or model_context is not None or session_job_id is not None:
            raise ValueError('world snapshots require source without a separate model context')
        world_data = json.dumps(world_snapshot, ensure_ascii=True, allow_nan=False).encode()
        if len(world_data) > 8 * 1024 * 1024:
            raise ValueError('world snapshot exceeds 8 MiB')
    if session_job_id is not None:
        if model_context is not None:
            raise ValueError('session metadata and model fixtures are mutually exclusive')
        if not isinstance(session_job_id, str) or len(session_job_id) != 36:
            raise ValueError('session_job_id must be an issued job UUID')
        uuid.UUID(session_job_id)
        context_text = ('mode=session\njob_id=' + session_job_id +
                        '\nservices=RunService\n')
    if model_context is not None:
        if not isinstance(model_context, dict) or model_context.get('mode') != 'offline':
            raise ValueError('model_context must explicitly select mode=offline')
        if set(model_context) != {'mode', 'job_id', 'is_studio', 'services'}:
            raise ValueError('model context requires mode, job_id, is_studio and services')
        lines = ['mode=offline']
        if 'job_id' in model_context:
            value = model_context['job_id']
            if (not isinstance(value, str) or len(value) > 128
                    or not value.startswith('offline-fixture-')
                    or any(ord(c) < 32 or ord(c) > 126 for c in value)):
                raise ValueError('job_id must be a printable offline-fixture-* value up to 128 characters')
            lines.append('job_id=' + value)
        if 'is_studio' in model_context:
            if type(model_context['is_studio']) is not bool:
                raise ValueError('is_studio fixture must be a boolean')
            lines.append('is_studio=' + str(model_context['is_studio']).lower())
        if 'services' in model_context:
            services = model_context['services']
            if (not isinstance(services, list) or len(services) > 32
                    or any(not isinstance(s, str) or not s.isascii()
                           or not s.isalpha() or len(s) > 64 for s in services)):
                raise ValueError('services fixture must be a list of ASCII class names')
            lines.append('services=' + ','.join(services))
        context_text = '\n'.join(lines) + '\n'
    # The Rust supervisor directly links the pinned VM and source bindings.
    # Keep fixture contexts and verified bytecode on their original host path.
    host = (RUST_HOST if input_kind == 'source' and context_text is None
            and RUST_HOST.is_file() else HOST)
    if not host.is_file():
        raise RuntimeError('build the offline host with: python3 runtime/build.py')

    # TemporaryDirectory defaults to owner-only; the source file is also 0600.
    with tempfile.TemporaryDirectory(prefix='local-luau-') as directory:
        path = Path(directory) / ('input.luau' if input_kind == 'source' else 'input.luac')
        path.write_bytes(encoded)
        path.chmod(0o600)
        command = [str(host), '--' + input_kind, str(path), '--timeout-ms', str(timeout_ms),
                   '--memory-mb', str(memory_mb)]
        if context_text is not None:
            context = Path(directory) / 'model-context.txt'
            context.write_text(context_text, encoding='ascii')
            context.chmod(0o600)
            command += ['--context-file', str(context)]
        if world_data is not None:
            snapshot_path = Path(directory) / 'world.json'
            snapshot_path.write_bytes(world_data)
            snapshot_path.chmod(0o600)
            command += ['--world-snapshot', str(snapshot_path)]
        command += ['--', *map(str, arguments)]
        try:
            completed = subprocess.run(command, capture_output=True, timeout=3 + timeout_ms / 1000)
        except subprocess.TimeoutExpired:
            observe_runtime_oneshot_report(None)
            return {'ok': False, 'values': [], 'error': 'host process wall timeout'}
        except OSError:
            observe_runtime_oneshot_report(None)
            raise
    if len(completed.stdout) > 1024 * 1024:
        observe_runtime_oneshot_report(None)
        raise RuntimeError('runtime output exceeded limit')
    try:
        result = json.loads(completed.stdout)
    except (UnicodeError, ValueError) as error:
        observe_runtime_oneshot_report(None)
        raise RuntimeError(f'runtime did not return JSON (exit {completed.returncode})') from error
    if (not isinstance(result, dict) or type(result.get('ok')) is not bool
            or not isinstance(result.get('values'), list)):
        observe_runtime_oneshot_report(None)
        raise RuntimeError('invalid runtime result')
    if completed.returncode and result['ok']:
        observe_runtime_oneshot_report(None)
        raise RuntimeError('runtime exited unsuccessfully but reported success')
    observe_runtime_oneshot_report(result)
    if world_snapshot is not None:
        result['runtime_backend'] = 'rust' if host == RUST_HOST else 'cpp'
    return result


def main():
    parser = argparse.ArgumentParser(description='Run local Luau in a fresh bounded VM')
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--source', type=Path)
    inputs.add_argument('--bytecode', type=Path, help='Trusted standard compiler output only')
    parser.add_argument('--timeout-ms', type=int, default=250)
    parser.add_argument('--memory-mb', type=int, default=512)
    parser.add_argument('--model-context', type=Path,
                        help='Optional JSON fixture selecting mode=offline (simulation only)')
    parser.add_argument('arguments', nargs='*', type=float)
    args = parser.parse_args()
    # Bounded read also rejects an oversized source before allocating it all.
    with (args.source or args.bytecode).open('rb') as stream:
        source = stream.read(MAX_SOURCE_BYTES + 1)
    if len(source) > MAX_SOURCE_BYTES:
        parser.error('source exceeds 100 MiB')
    context = None
    if args.model_context is not None:
        with args.model_context.open('rb') as stream:
            data = stream.read(4097)
        if len(data) > 4096:
            parser.error('model context exceeds 4 KiB')
        context = json.loads(data)
    runner = run_source if args.source else run_bytecode
    result = runner(source.decode('utf-8') if args.source else source, args.arguments,
                        timeout_ms=args.timeout_ms, memory_mb=args.memory_mb,
                        model_context=context)
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
