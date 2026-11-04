#!/usr/bin/env python3
"""Capture the share link from an existing sUNC result panel.

This observes the rendered UI; it does not run tests, join a server, change the
report gate, or infer a URL from printed outcomes. --copy clicks the observed
Frame's normal input handler and verifies its platform clipboard output.
"""
import argparse
import json
import math
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time
from urllib.parse import urlsplit

from script_console import send_request


PANEL_PROVENANCE = '''
local screen=game:GetService("CoreGui"):FindFirstChild("sUNC_melon_result")
if not screen or not screen:IsA("ScreenGui") then return false end
local panel=screen:FindFirstChild("result_frame",true)
if not panel or not panel:IsA("Frame") then return false end
local text=panel:FindFirstChild("link_textbox",true)
if not text or not text:IsA("TextBox") then return false end
return screen,panel,text
'''


def share_url(value):
    if not isinstance(value, str) or len(value) > 256:
        return None
    value = value.strip()
    if not value.startswith('https://'):
        value = 'https://' + value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if (parsed.scheme != 'https' or parsed.netloc not in ('r.sunc.su', 'r.sunc.io')
            or parsed.query or parsed.fragment
            or not re.fullmatch(r'/[A-Za-z0-9_-]{1,128}', parsed.path)):
        return None
    return value


def _visible_box(node, viewport):
    box = node.get('box')
    if (not isinstance(box, list) or len(box) != 5
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
            or box[2] <= 0 or box[3] <= 0 or box[4] != 0):
        return False
    x, y, width, height, _ = box
    # Wait for the panel's entrance tween to put the whole control on screen.
    return 0 <= x and 0 <= y and x+width <= viewport[0] and y+height <= viewport[1]


def result_control(response):
    """Require the visible readonly URL box and its actual Frame listener."""
    frame = response.get('frame', {})
    if not isinstance(frame, dict):
        return {'state': 'waiting_for_panel', 'reason': 'display_unavailable'}
    viewport = frame.get('viewport')
    if (not response.get('ok') or not isinstance(viewport, list) or len(viewport) != 2
            or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
                   for v in viewport)):
        return {'state': 'waiting_for_panel', 'reason': 'display_unavailable'}
    gui = frame.get('gui', [])
    controls = {node.get('id'): node for node in gui if isinstance(node, dict)}
    matches = []
    for node in controls.values():
        if (node.get('class') != 'TextBox' or node.get('name') != 'link_textbox'
                or node.get('textEditable') is not False or not _visible_box(node, viewport)):
            continue
        url = share_url(node.get('text'))
        target = controls.get(node.get('pointerTarget'), {})
        if (url is None or target.get('class') != 'Frame'
                or target.get('name') != 'result_frame' or not target.get('interactive')
                or not isinstance(target.get('id'), str) or not _visible_box(target, viewport)):
            continue
        matches.append({'state': 'panel_observed', 'share_url': url,
                        'text': node['text'], 'textbox_id': node['id'],
                        'pointer_target': target['id'], 'panel_box': target['box'],
                        'viewport': viewport, 'clipboard_verified': False})
    if len(matches) != 1:
        return {'state': 'waiting_for_panel',
                'reason': 'ambiguous_panels' if matches else 'panel_absent',
                'viewport': viewport, 'candidate_count': len(matches)}
    return matches[0]


def capture(request, *, read_clipboard=None, clipboard_timeout=2.):
    observation = result_control(request({'op': 'render'}))
    if observation['state'] != 'panel_observed':
        return observation
    # Rendered names alone could match an unrelated game GUI. Confirm these
    # exact instance identities belong to the suite's CoreGui result screen.
    provenance = request({'source': PANEL_PROVENANCE, 'timeout_ms': 250})
    values = provenance.get('values', [])
    if (not provenance.get('ok') or len(values) != 3
            or any(not isinstance(value, dict) or not isinstance(value.get('$ref'), str)
                   for value in values)
            or values[1]['$ref'] != observation['pointer_target']
            or values[2]['$ref'] != observation['textbox_id']):
        return {'state': 'waiting_for_panel', 'reason': 'panel_provenance_unavailable'}
    observation['screen_id'] = values[0]['$ref']
    if read_clipboard is None:
        return observation
    target = observation['pointer_target']
    pressed = request({'op': 'gui_action', 'action': {'id': target, 'kind': 'pointer_down'}})
    if not pressed.get('ok'):
        return {**observation, 'state': 'copy_failed', 'reason': 'pointer_down_rejected'}
    # Release even if the handler hides/destroys its panel on press. This also
    # leaves the session's single pointer available after a successful copy.
    released = request({'op': 'gui_action', 'action': {'id': target, 'kind': 'pointer_up'}})
    if not released.get('ok'):
        return {**observation, 'state': 'copy_failed', 'reason': 'pointer_up_rejected'}
    deadline = time.monotonic() + clipboard_timeout
    while True:
        try:
            copied = read_clipboard()
        except OSError as error:
            return {**observation, 'state': 'copy_failed', 'reason': 'clipboard_unavailable',
                    'error_type': type(error).__name__}
        if share_url(copied) == observation['share_url']:
            return {**observation, 'state': 'copied', 'clipboard_verified': True}
        if time.monotonic() >= deadline:
            # Never save or print unrelated clipboard contents.
            return {**observation, 'state': 'copy_failed', 'reason': 'clipboard_did_not_match'}
        time.sleep(min(.05, max(0., deadline-time.monotonic())))


def read_macos_clipboard():
    with subprocess.Popen(['/usr/bin/pbpaste'], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as child:
        deadline = time.monotonic() + 2.
        data = bytearray()
        try:
            os.set_blocking(child.stdout.fileno(), False)
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline-time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        return None
                    # Bound unrelated contents without waiting indefinitely
                    # for pbpaste or saving the clipboard in diagnostics.
                    chunk = os.read(child.stdout.fileno(), 1025-len(data))
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > 1024:
                        return None
            child.wait(timeout=max(.001, deadline-time.monotonic()))
            return data.decode('utf-8') if child.returncode == 0 else None
        except (UnicodeDecodeError, subprocess.TimeoutExpired):
            return None
        finally:
            if child.poll() is None:
                child.kill()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', type=Path, required=True,
                        help='Console socket of the already running session')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--wait-seconds', type=float, default=0)
    parser.add_argument('--copy', action='store_true', help='Click the observed copy panel')
    args = parser.parse_args()
    if not math.isfinite(args.wait_seconds) or not 0 <= args.wait_seconds <= 1200:
        parser.error('--wait-seconds must be 0..1200')
    if args.copy and sys.platform != 'darwin':
        parser.error('--copy currently verifies the macOS clipboard')
    started = time.monotonic()
    request = lambda command: send_request(args.socket, command)
    while True:
        try:
            result = capture(request, read_clipboard=read_macos_clipboard if args.copy else None)
        except (OSError, ValueError) as error:
            result = {'state': 'session_unavailable', 'error_type': type(error).__name__}
        result['elapsed_seconds'] = round(time.monotonic()-started, 3)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2)+'\n')
        if result['state'] != 'waiting_for_panel' or time.monotonic()-started >= args.wait_seconds:
            print(json.dumps(result))
            return 0 if result['state'] in ('panel_observed', 'copied') else 1
        time.sleep(min(2., max(0., args.wait_seconds-(time.monotonic()-started))))


if __name__ == '__main__':
    raise SystemExit(main())
