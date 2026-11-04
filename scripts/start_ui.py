#!/usr/bin/env python3
"""Set up missing dependencies, then open Rove."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def python_path(root):
    return Path(root) / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')


def runtime_files(root):
    root = Path(root)
    suffix = '.exe' if os.name == 'nt' else ''
    return {
        'Luau runtime': root / ('runtime/build/luau-host' + suffix),
        'Script runtime': root / ('rust-client/target/release/headless-luau' + suffix),
        'Animation decoder': root / ('animation-codec/target/release/animation-codec' + suffix),
        'Avatar preview': root / 'ui/avatar-viewer.js',
        'World preview': root / 'ui/world-viewer.js',
    }


def environment(root):
    import json
    env = os.environ.copy()
    env['PYTHONUTF8'] = '1'
    extra = [str(Path.home() / '.cargo/bin')]
    if sys.platform == 'darwin':
        extra += ['/opt/homebrew/bin', '/usr/local/bin']
    settings = Path(root) / '.rove-platform.json'
    if settings.is_file():
        prefix = json.loads(settings.read_text()).get('native_prefix')
        if prefix:
            extra.insert(0, str(Path(prefix) / 'bin'))
    env['PATH'] = os.pathsep.join(extra + [env.get('PATH', '')])
    return env


def missing_requirements(root, env):
    missing = [name for name, path in runtime_files(root).items() if not path.is_file()]
    python = python_path(root)
    if not python.is_file():
        return ['Python environment', *missing]
    probe = ('import sys; assert sys.version_info >= (3,12); '
             'import aioquic, cryptography, OpenSSL, zstandard, blake3')
    if os.name == 'nt':
        probe += '; import win32api, win32security'
    try:
        result = subprocess.run([str(python), '-c', probe], cwd=root, env=env,
                                capture_output=True, timeout=20)
        if result.returncode:
            missing.insert(0, 'Python dependencies')
    except (OSError, subprocess.TimeoutExpired):
        missing.insert(0, 'Python environment')
    return missing


def ensure_setup(root=ROOT):
    root = Path(root)
    env = environment(root)
    missing = missing_requirements(root, env)
    if not missing:
        return env
    print('Setting up Rove: ' + ', '.join(missing) + '.', flush=True)
    if sys.platform == 'darwin':
        command = ['/bin/zsh', str(root / 'Setup Rove.command'), '--no-pause']
    elif os.name == 'nt':
        command = [sys.executable, str(root / 'scripts/setup_windows.py'), '--ui']
    else:
        command = [sys.executable, str(root / 'scripts/setup_ui.py'), '--no-app']
    result = subprocess.run(command, cwd=root, env=env)
    if result.returncode:
        raise SystemExit(result.returncode)
    env = environment(root)
    missing = missing_requirements(root, env)
    if missing:
        raise RuntimeError('Setup did not finish: ' + ', '.join(missing) + '.')
    return env


def prepare_launcher(root=ROOT):
    if any(arg in ('-h', '--help') for arg in sys.argv[1:]):
        return
    try:
        env = ensure_setup(root)
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(str(error)) from None
    if Path(sys.prefix).resolve() != (Path(root) / '.venv').resolve():
        python = str(python_path(root))
        os.execve(python, [python, str(Path(root) / 'scripts/launcher.py'), *sys.argv[1:]], env)


def main(argv=None, root=ROOT):
    args = sys.argv[1:] if argv is None else argv
    root = Path(root)
    if any(arg in ('-h', '--help') for arg in args):
        return subprocess.call([sys.executable, str(root / 'scripts/open_ui.py'), *args], cwd=root)
    try:
        env = ensure_setup(root)
        return subprocess.call([str(python_path(root)), str(root / 'scripts/open_ui.py'), *args],
                               cwd=root, env=env)
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
