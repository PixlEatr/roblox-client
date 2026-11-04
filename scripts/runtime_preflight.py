"""Bounded, offline dependency checks before opening a live client session.

Cloud-backed reads can block even after imports succeed. Keep the probe in a
separate process so a missing parser, trust asset, or executable cannot freeze
the live network loop while it is handling a server packet.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from local_runtime import HOST, RUST_HOST


ROOT = Path(__file__).resolve().parents[1]
DATALESS = 0x40000000  # Darwin SF_DATALESS, absent on other platforms.


def _probe():
    required = {
        'bytecode_inspector': ROOT / 'scripts/inspect_bytecode.py',
        'opcode_metadata': ROOT / 'scripts/opcode_metadata.py',
        'opcode_header': ROOT / 'vendor/luau/Common/include/Luau/Bytecode.h',
        'opcode_lengths': ROOT / 'vendor/luau/Common/include/Luau/BytecodeUtils.h',
        'signature_table': ROOT / 'inputs/bytecode-signature-v1-public.bin',
        'signature_provenance': ROOT / 'inputs/bytecode-signature-v1-public.json',
        'persistent_runtime': RUST_HOST,
        'bytecode_runtime': HOST,
    }
    for label, path in required.items():
        try:
            metadata = path.stat()
        except OSError:
            return {'ok': False, 'reason': 'dependency_unavailable', 'dependency': label}
        if getattr(metadata, 'st_flags', 0) & DATALESS:
            return {'ok': False, 'reason': 'dependency_cloud_only', 'dependency': label}

    from bytecode_container import _inspector
    from bytecode_signature import _public_coefficients
    from local_runtime import run_source

    if not _inspector().opcode_metadata()['opcodes'] or not _public_coefficients():
        return {'ok': False, 'reason': 'dependency_validation_failed'}
    # Authored source only: no captured program, credentials, or network access.
    contexts = (None, {'mode': 'offline', 'job_id': 'offline-fixture-preflight',
                       'is_studio': False, 'services': ['RunService']})
    for context in contexts:
        result = run_source('return 17', timeout_ms=1000, model_context=context)
        if result.get('ok') is not True or result.get('values') != [17]:
            return {'ok': False, 'reason': 'runtime_probe_failed'}
    return {'ok': True, 'network_used': False, 'runtime_probes': 2}


def check_runtime_dependencies(timeout=15):
    command = [sys.executable]
    if sys.pycache_prefix:
        command += ['-X', 'pycache_prefix=' + sys.pycache_prefix]
    command += [str(Path(__file__).resolve()), '--worker']
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout,
                                check=False, text=True)
    except subprocess.TimeoutExpired:
        return {'ok': False, 'reason': 'dependency_probe_timeout'}
    except OSError:
        return {'ok': False, 'reason': 'dependency_probe_unavailable'}
    try:
        report = json.loads(result.stdout)
    except (ValueError, TypeError):
        return {'ok': False, 'reason': 'dependency_probe_invalid_result'}
    if not isinstance(report, dict) or type(report.get('ok')) is not bool:
        return {'ok': False, 'reason': 'dependency_probe_invalid_result'}
    if result.returncode != 0:
        return {'ok': False, 'reason': 'dependency_probe_failed'}
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        report = _probe() if args.worker else check_runtime_dependencies()
    except Exception as error:
        report = {'ok': False, 'reason': 'dependency_probe_failed',
                  'error_type': type(error).__name__}
    print(json.dumps(report), flush=True)
    if not args.worker and not report.get('ok'):
        raise SystemExit(1)
