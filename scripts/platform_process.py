"""Process groups and executable names for native desktop platforms."""
import os
import signal
import subprocess


def executable(name):
    return name + '.exe' if os.name == 'nt' else name


def group_options():
    return {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}


def stop_group(process, *, force=False):
    if process.poll() is not None: return
    try:
        if os.name == 'nt':
            # Include any helper processes still owned by this worker.
            taskkill = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'taskkill.exe')
            try:
                result = subprocess.run([taskkill, '/PID', str(process.pid), '/T', '/F'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                if result.returncode and process.poll() is None: process.kill()
            except (OSError, subprocess.TimeoutExpired):
                if process.poll() is None: process.kill()
        else: os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError: pass
