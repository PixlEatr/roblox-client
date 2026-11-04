/* Focused keyboard/mouse input for the local editor. No text-entry listeners. */
(function (global) {
    'use strict';
    global.createKeyboardControls = function ({surface, label, key, onResult = () => {}, codes = null}) {
        const client = [...crypto.getRandomValues(new Uint8Array(16))].map(v => v.toString(16).padStart(2, '0')).join('');
        let sequence = 0, lease = null, connected = false, desired = false;
        let sending = false, attach = false, release = false, detach = false, heartbeat = false;
        let mapping = codes, disposed = false;
        const held = new Map(), buttons = new Map(), queue = [], listeners = [];
        let mousePosition = null;
        const owns = target => target === surface || surface.contains(target);
        const focused = () => document.activeElement === surface && document.visibilityState !== 'hidden';
        function show(text) {
            label.textContent = text;
            surface.classList.toggle('active', desired && Boolean(lease));
            surface.setAttribute('aria-disabled', String(!connected));
        }
        function idle() { show(connected ? 'Click here to control · Esc to release' : 'Waiting for runtime'); }
        async function post(action, extra = {}, token = lease) {
            const body = {action, client_id: client, sequence: ++sequence, ...extra};
            if (action !== 'attach') body.lease = token;
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), 6000);
            try {
                const response = await fetch('/api/runtime/input', {
                    method: 'POST', headers: {'Content-Type': 'application/json', 'X-Console-Key': key},
                    body: JSON.stringify(body), signal: controller.signal, keepalive: true
                });
                const result = await response.json();
                if (!response.ok || !result.ok) throw new Error(result.error || 'Keyboard input unavailable');
                onResult(result);
                return result;
            } finally { clearTimeout(timer); }
        }
        function bestEffortDetach(token) {
            if (token) void post('detach', {}, token).catch(() => {});
        }
        function failed(error) {
            const previous = lease;
            desired = false; lease = null; attach = release = detach = heartbeat = false;
            held.clear(); buttons.clear(); mousePosition = null; queue.length = 0;
            if (document.activeElement === surface) surface.blur();
            bestEffortDetach(previous);
            show(error.name === 'AbortError' ? 'Keyboard timed out · click to reconnect' : error.message);
        }
        function nextBatch() {
            // Keep a press and its release in separate native deliveries. Both
            // events retain the same InputObject without collapsing its state.
            const batch = [], keys = new Set();
            const identity = item => item.input_type || 'Keyboard:' + item.key_code;
            while (queue.length && batch.length < 64 && !keys.has(identity(queue[0]))) {
                const item = queue.shift(); keys.add(identity(item)); batch.push(item);
            }
            return batch;
        }
        async function pump() {
            if (sending || !connected || disposed) return;
            sending = true;
            try {
                while (connected && !disposed) {
                    if (detach) {
                        detach = false;
                        if (lease) { const token = lease; lease = null; await post('detach', {}, token); }
                        idle();
                    } else if (attach) {
                        attach = false;
                        const result = await post('attach');
                        if (!connected || disposed) { bestEffortDetach(result.lease); break; }
                        lease = result.lease;
                        if (!lease) throw new Error('Keyboard backend did not attach');
                        if (!desired || !focused()) release = true;
                        else show('Controls active · Esc to release');
                    } else if (release && lease) {
                        release = false; queue.length = 0;
                        await post('release'); idle();
                    } else if (lease && desired && queue.length) {
                        await post('events', {events: nextBatch()});
                    } else if (lease && heartbeat) {
                        heartbeat = false; await post('heartbeat');
                    } else break;
                }
            } catch (error) { failed(error); }
            finally { sending = false; }
        }
        function relinquish({disconnect = false} = {}) {
            desired = false; held.clear(); buttons.clear(); mousePosition = null; queue.length = 0; attach = false;
            const disconnectRequested = disconnect || detach;
            release = !disconnectRequested; detach = disconnectRequested; heartbeat = false;
            idle(); void pump();
        }
        function begin() {
            if (!connected || !mapping || !focused() || disposed || desired) return;
            desired = true; attach = true; release = detach = false;
            show('Connecting controls…'); void pump();
        }
        function listen(target, name, handler, options) {
            target.addEventListener(name, handler, options); listeners.push([target, name, handler, options]);
        }
        const mouseButtons = {0: 'MouseButton1', 1: 'MouseButton3', 2: 'MouseButton2'};
        const mouseEvent = event => event.isTrusted && (!event.pointerType || event.pointerType === 'mouse');
        function coordinates(event) {
            const x = event.clientX, y = event.clientY;
            return Number.isFinite(x) && Number.isFinite(y) ? [x, y] : null;
        }
        function enqueue(item) {
            const previous = queue.at(-1);
            if (item.input_type === 'MouseMovement' && previous?.input_type === 'MouseMovement') {
                previous.position = item.position; previous.mouse_location = item.mouse_location;
                previous.delta = previous.delta.map((n, i) => n + item.delta[i]);
            } else queue.push(item);
            if (queue.length > 64) { failed(new Error('Input queue is full · click to reconnect')); return; }
            void pump();
        }
        function pointerItem(type, phase, point, delta = [0, 0, 0], z = 0) {
            return {input_type: type, phase, position: [...point, z], delta, mouse_location: [...point]};
        }
        listen(surface, 'pointerdown', event => {
            if (!mouseEvent(event) || !connected || !mouseButtons[event.button]) return;
            const wasFocused = desired && focused();
            if (!wasFocused) {
                if (event.button === 0) { surface.focus(); begin(); }
                return; // Activating the control area does not click through into a game action.
            }
            const point = coordinates(event); if (!point || buttons.has(event.button)) return;
            event.preventDefault(); mousePosition = point; buttons.set(event.button, mouseButtons[event.button]);
            enqueue(pointerItem(mouseButtons[event.button], 'begin', point));
        });
        listen(document, 'pointerup', event => {
            if (!mouseEvent(event) || !buttons.has(event.button)) return;
            const type = buttons.get(event.button); buttons.delete(event.button);
            const point = coordinates(event) || mousePosition || [0, 0]; mousePosition = point;
            event.preventDefault(); enqueue(pointerItem(type, 'end', point));
        });
        function cancelButton(button, point) {
            const type = buttons.get(button); if (!type) return;
            buttons.delete(button); enqueue(pointerItem(type, 'cancel', point));
        }
        listen(document, 'pointercancel', event => {
            if (!mouseEvent(event)) return;
            const point = coordinates(event) || mousePosition || [0, 0]; mousePosition = point;
            if (buttons.has(event.button)) cancelButton(event.button, point);
            else for (const button of [...buttons.keys()]) cancelButton(button, point);
        });
        listen(document, 'pointermove', event => {
            if (!mouseEvent(event) || !desired || !focused() || (!owns(event.target) && buttons.size === 0)) return;
            const point = coordinates(event); if (!point) return;
            if (Number.isInteger(event.buttons) && event.buttons >= 0) {
                const masks = {0: 1, 1: 4, 2: 2};
                for (const button of [...buttons.keys()]) if (!(event.buttons & masks[button])) cancelButton(button, point);
            }
            const delta = mousePosition ? [point[0] - mousePosition[0], point[1] - mousePosition[1], 0] : [0, 0, 0];
            mousePosition = point;
            enqueue(pointerItem('MouseMovement', 'change', point, delta));
        });
        listen(surface, 'wheel', event => {
            if (!event.isTrusted || !desired || !focused() || event.ctrlKey || !Number.isFinite(event.deltaY) || !event.deltaY) return;
            const point = coordinates(event); if (!point) return;
            // Discrete wheel adapter: one vertical DOM wheel event is one step;
            // upward is positive, matching Roblox MouseWheel.Position.Z.
            const step = -Math.sign(event.deltaY); mousePosition = point;
            event.preventDefault(); enqueue(pointerItem('MouseWheel', 'change', point, [0, 0, step], step));
        }, {passive: false});
        listen(surface, 'contextmenu', event => {
            if (event.isTrusted && desired && focused()) event.preventDefault();
        });
        listen(surface, 'auxclick', event => {
            if (event.isTrusted && desired && focused()) event.preventDefault();
        });
        listen(surface, 'focus', () => { begin(); });
        listen(surface, 'blur', () => { relinquish(); });
        listen(document, 'keydown', event => {
            if (!event.isTrusted || !owns(event.target) || !desired || !focused()) return;
            if (event.code === 'Escape' || event.metaKey || event.code === 'MetaLeft' || event.code === 'MetaRight') {
                if (event.code === 'Escape') event.preventDefault();
                relinquish(); surface.blur(); return;
            }
            if (event.isComposing || event.repeat || held.has(event.code)) return;
            const code = mapping[event.code]; if (!code) return;
            event.preventDefault(); held.set(event.code, code);
            enqueue({phase: 'begin', key_code: code});
        });
        listen(document, 'keyup', event => {
            if (!event.isTrusted || !held.has(event.code)) return;
            const code = held.get(event.code); held.delete(event.code); event.preventDefault();
            enqueue({phase: 'end', key_code: code});
        });
        listen(window, 'blur', () => { relinquish(); if (document.activeElement === surface) surface.blur(); });
        listen(document, 'visibilitychange', () => {
            if (document.visibilityState === 'hidden') {
                relinquish({disconnect: true}); if (document.activeElement === surface) surface.blur();
            }
        });
        listen(window, 'pagehide', () => {
            const token = lease; desired = false; lease = null; held.clear(); buttons.clear(); mousePosition = null; queue.length = 0;
            attach = release = detach = heartbeat = false;
            bestEffortDetach(token); // The owner-thread expiry covers lost unload requests.
        });
        const interval = setInterval(() => {
            if (connected && lease && document.visibilityState !== 'hidden') { heartbeat = true; void pump(); }
        }, 500);
        const ready = mapping ? Promise.resolve() : fetch('/keyboard-codes.json', {cache: 'no-store'})
            .then(response => { if (!response.ok) throw new Error('Keyboard mapping unavailable'); return response.json(); })
            .then(value => { mapping = value; begin(); })
            .catch(error => { show(error.message); });
        idle();
        return {
            ready, owns,
            get active() { return desired; },
            setConnected(value) {
                value = Boolean(value);
                if (connected === value) return;
                connected = value;
                if (!value) {
                    const token = lease; lease = null; desired = false; held.clear(); buttons.clear(); mousePosition = null; queue.length = 0;
                    attach = release = detach = heartbeat = false; bestEffortDetach(token);
                    if (document.activeElement === surface) surface.blur();
                }
                idle();
            },
            destroy() {
                const token = lease; disposed = true; desired = false; lease = null; held.clear(); buttons.clear(); mousePosition = null; queue.length = 0;
                clearInterval(interval);
                for (const [target, name, handler, options] of listeners) target.removeEventListener(name, handler, options);
                bestEffortDetach(token);
            }
        };
    };
})(globalThis);
