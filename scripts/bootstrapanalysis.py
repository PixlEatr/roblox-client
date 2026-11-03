#!/usr/bin/env python3
"""Summarize persistent-Luau bootstrap failures without printing private data.

The persistent stream can contain replicated identifiers and local source
output. This utility reports only aggregate counts and a fixed classification.
"""
import argparse
import json
from pathlib import Path


KNOWN_CLONE_LIMIT_WARNING = (
    'Client template: script-runtime:22: Clone failed: '
    'local mutation output limit exceeded'
)


def analyze(path):
    records = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(records, list):
        raise ValueError('persistent stream must contain a list')

    warnings = 0
    runtime_error_records = 0
    characters_seen_in_warnings = False
    running_local_scripts_seen = False
    for record in records:
        if not isinstance(record, dict):
            continue
        output = record.get('output')
        if isinstance(output, str) and KNOWN_CLONE_LIMIT_WARNING in output:
            warnings += output.count(KNOWN_CLONE_LIMIT_WARNING)
            characters_seen_in_warnings |= 'Character' in output
        runtime = record.get('script_runtime')
        if isinstance(runtime, dict):
            if runtime.get('bootstrap_errors', 0):
                runtime_error_records += 1
            running_local_scripts_seen |= bool(runtime.get('running', 0))

    if warnings:
        cause = 'template_clone_failed_due_to_local_mutation_output_budget'
        location = 'ScriptExecution.prepareTemplates mirror pass; specific source container is not logged'
    elif runtime_error_records:
        cause = 'bootstrap_error_present_but_unclassified'
        location = None
    else:
        cause = 'no_persistent_bootstrap_error_found'
        location = None

    return {
        'records': len(records),
        'clone_limit_warnings': warnings,
        'runtime_error_records': runtime_error_records,
        'running_local_scripts_seen': running_local_scripts_seen,
        'character_named_in_warning': characters_seen_in_warnings,
        'cause': cause,
        'location': location,
        'source_limits': {
            'max_local_mutations': 10000,
            'max_mutation_bytes': 131072,
            'source': 'runtime/world_bindings.cpp check_mutation_budget',
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        'capture', nargs='?',
        default=str(Path(__file__).resolve().parents[1]
                    / 'secrets/luau-persistent-stream.json'),
        help='private persistent stream capture (only aggregate output is printed)',
    )
    args = parser.parse_args(argv)
    print(json.dumps(analyze(args.capture), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
