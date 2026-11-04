"""Hold a temporary macOS sleep assertion only while a game session runs."""
from contextlib import contextmanager
import os
import subprocess
import sys


@contextmanager
def prevent_idle_sleep(report):
    policy = report['host_awake_policy'] = {
        'platform': sys.platform,
        'helper_started': False,
        'scope': 'active_game_session',
        'prevents_lid_or_thermal_sleep': False,
    }
    process = None
    if sys.platform == 'darwin':
        policy['requested_assertions'] = ['idle_system_sleep', 'system_sleep_on_ac']
        try:
            process = subprocess.Popen(
                ['/usr/bin/caffeinate', '-i', '-s', '-w', str(os.getpid())],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True)
            policy['helper_started'] = process.poll() is None
        except OSError as error:
            # Power-management availability must not prevent a connection.
            policy['helper_error_type'] = type(error).__name__
    try:
        yield
    finally:
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            except OSError:
                pass
