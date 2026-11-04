window.RoveScripts=(() => {
let runtimeSession;
const $ = (id) => document.getElementById("script-"+id);
const editor = $('editor');
const run = $('run');
const key = document.querySelector('meta[name="console-key"]').content;
let connected = false;
let busy = false;
let eventCursor = 0;
let pollingEvents = false;
let streamAvailable = true;
const seenEvents = new Set();
const completedJobs = new Set();
const runningJobs = new Set();

let draft;try{draft=localStorage.getItem("rove.script");}catch{}
editor.value = draft || `local count = 0

for _, player in game:GetService("Players"):GetChildren() do
    if player:IsA("Player") then
        count += 1
        print(player.Name, "UserId:", player.UserId)
    end
end

print("Total players:", count)`;

function updateEditor() {
    const lines = editor.value.split('\n').length;
    $('gutter').textContent = Array.from({length: lines}, (_, i) => i + 1).join('\n');
    $('line-count').textContent = `${lines} ${lines === 1 ? 'line' : 'lines'}`;
    $('gutter').scrollTop = editor.scrollTop;
    run.disabled = busy || !connected || !editor.value.trim();
}

function append(text, kind = 'log') {
    if (!text && text !== 0) return;
    $('empty')?.remove();
    const entry = document.createElement('div');
    entry.className = `entry ${kind}`;
    const time = document.createElement('time');
    time.textContent = new Date().toLocaleTimeString([], {hour12: false});
    const message = document.createElement('pre');
    message.textContent = String(text);
    entry.append(time, message);
    $('output').append(entry);
    while ($('output').children.length > 1200) $('output').firstElementChild.remove();
    $('output').scrollTop = $('output').scrollHeight;
}

async function status() {
    if (busy) return;
    try {
        const response = await fetch('/api/runtime/status', {cache: 'no-store'});
        if (!response.ok) throw new Error();
        const current=await response.json();
        if(current.session!==runtimeSession){runtimeSession=current.session;eventCursor=0;streamAvailable=true;seenEvents.clear();completedJobs.clear();runningJobs.clear();}
        connected = Boolean(current.connected);
        $('connection-label').textContent = connected ? 'Runtime connected' : 'Waiting for runtime';
    } catch {
        connected = false;
        $('connection-label').textContent = 'Console offline';
    }

    $('connection').className = `connection ${connected ? 'connected' : 'disconnected'}`;
    updateEditor();
}

async function execute() {
    if (busy || run.disabled) return;
    busy = true;
    updateEditor();
    run.firstChild.textContent = 'Running ';
    $('result-state').textContent = 'Running';
    $('result-state').className = '';
    const started = performance.now();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
        const response = await fetch('/api/runtime/execute', {
            method: 'POST', headers: {'Content-Type': 'application/json', 'X-Console-Key': key},
            body: JSON.stringify({source: editor.value}), signal: controller.signal
        });
        const result = await response.json();
        renderEvent({...result, sequence: result.runtime_sequence});
        if (!response.ok) throw new Error(result.error || result.error_type || 'Script request failed');
        if (result.state === 'running' && completedJobs.has(result.job_id)) return;
        if (result.state === 'running' && !completedJobs.has(result.job_id)) {
            runningJobs.add(result.job_id);
            append(`Script is running (job ${result.job_id}).`, 'info');
            $('result-state').textContent = 'Running';
        } else if (result.ok && !(result.pending_tasks > 0)) {
            $('result-state').textContent = `${Math.round(performance.now() - started)} ms`;
        } else if (result.ok) {
            $('result-state').textContent = 'Background tasks running';
        }
    } catch (error) {
        append(error.name === 'AbortError' ? 'Request timed out. Check the runtime before running again.' : error.message, 'error');
        $('result-state').textContent = 'Error';
        $('result-state').className = 'error';
    } finally {
        clearTimeout(timeout);
        busy = false;
        run.firstChild.textContent = 'Run script ';
        await status();
    }
}

function showCompletion(result) {
    if (result.job_id && completedJobs.has(result.job_id)) return;
    if (result.job_id) {
        completedJobs.add(result.job_id);
        runningJobs.delete(result.job_id);
    }
    if (result.values?.length) append(`↳ ${JSON.stringify(result.values)}`, 'return');
    if (!result.ok) {
        append(result.error || result.error_type || 'Script failed', 'error');
        $('result-state').textContent = 'Error';
        $('result-state').className = 'error';
    } else if (result.pending_tasks > 0) {
        append('Script returned; background tasks are still running.', 'info');
        $('result-state').textContent = 'Background tasks running';
    } else {
        $('result-state').className = '';
        if (!result.values?.length) append(result.job_id ? `Job ${result.job_id} completed.` : 'Script completed.', 'info');
        $('result-state').textContent = runningJobs.size ? 'Running' : 'Complete';
    }
}

function renderEvent(result) {
    if (Number.isInteger(result.sequence)) {
        if (seenEvents.has(result.sequence)) return;
        seenEvents.add(result.sequence);
        if (seenEvents.size > 512) seenEvents.delete(seenEvents.values().next().value);
    }
    for (const line of (result.output || '').split('\n')) {
        if (line) append(line, line.startsWith('WARN:') ? 'warn' : 'log');
    }
    for (const error of result.task_errors || []) append(typeof error === 'string' ? error : JSON.stringify(error), 'error');
    for (const outcome of result.chat_outcomes || []) {
        append(outcome.error ? `Chat failed: ${outcome.error}` : `Chat: ${outcome.status}`,
               outcome.error || outcome.status !== 'Success' ? 'error' : 'info');
    }
    const completed = result.completed_jobs || [];
    for (const job of completed) showCompletion({...job, pending_tasks: result.pending_tasks});
    if (result.state === 'running' && result.job_id && !completedJobs.has(result.job_id)) runningJobs.add(result.job_id);
    else if (!completed.some(job => job.job_id === result.job_id) &&
             (result.completed || result.ok === false ||
              (!Number.isInteger(result.sequence) && !result.state && !completed.length))) {
        if ('ok' in result) showCompletion(result);
    }
}

async function pollEvents() {
    if (!connected || pollingEvents || !streamAvailable) return;
    pollingEvents = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
        const response = await fetch('/api/runtime/events', {
            method: 'POST', headers: {'Content-Type': 'application/json', 'X-Console-Key': key},
            body: JSON.stringify({after: eventCursor}), signal: controller.signal
        });
        const result = await response.json();
        if (!response.ok || !result.ok) throw new Error(result.error || 'Runtime output stream unavailable');
        if (result.reset) {seenEvents.clear(); completedJobs.clear(); runningJobs.clear();}
        if (result.overrun) append('Earlier runtime output is no longer retained.', 'info');
        for (const event of result.events || []) renderEvent(event);
        eventCursor = result.cursor;
    } catch (error) {
        streamAvailable = false;
        append(`Cannot follow script results: ${error.message}. Restart the client to reconnect.`, 'error');
    } finally {
        clearTimeout(timeout);
        pollingEvents = false;
    }
}

editor.addEventListener('input',()=>{try{localStorage.setItem('rove.script',editor.value);}catch{}updateEditor();});
editor.addEventListener('scroll', () => {$('gutter').scrollTop = editor.scrollTop;});
editor.addEventListener('keydown', (event) => {
    if (event.key === 'Tab') {
        event.preventDefault();
        editor.setRangeText('    ', editor.selectionStart, editor.selectionEnd, 'end');
        updateEditor();
    }
});
document.addEventListener('keydown', (event) => {
    if (!document.getElementById('workspace-page').hidden && (event.metaKey || event.ctrlKey) && event.key === 'Enter') {
        event.preventDefault();
        execute();
    }
});
$('shortcut').textContent = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘ Enter' : 'Ctrl Enter';
run.addEventListener('click', execute);
$('clear').addEventListener('click', () => {$('output').replaceChildren(); $('result-state').textContent = '';});
$('load').addEventListener('click', () => $('file').click());
$('file').addEventListener('change', async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    try {
        if (file.size > 100 * 1024 * 1024) throw new Error('Script exceeds 100 MiB');
        editor.value = await file.text();
        $('filename').textContent = file.name;
        updateEditor();
    } catch (error) {append(error.message, 'error');}
    event.target.value = '';
});
updateEditor();
status();
setInterval(status, 2500);
setInterval(pollEvents, 250);

return {execute,renderEvent,pollEvents,status,updateEditor};
})();
