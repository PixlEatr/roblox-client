(() => {
  'use strict';

  const connection = document.getElementById('game-connection');
  const waitingText = document.getElementById('game-waiting-text');
  let wasConnected = false;

  async function refreshLocalStatus() {
    try {
      const response = await fetch('/api/runtime/status', { cache: 'no-store', signal: AbortSignal.timeout(4000) });
      if (!response.ok) throw new Error('status unavailable');
      const status = await response.json();
      if (status.connected === true) {
        wasConnected = true;
        connection.textContent = 'Local VM connected';
        connection.classList.add('live');
        waitingText.textContent = 'Rendering the current local Rove session. No online game was joined by this page.';
      } else if (wasConnected) {
        connection.textContent = 'Local session ended';
        connection.classList.remove('live');
        waitingText.textContent = 'The local session ended. Restart the Rove runtime to connect again.';
      } else {
        connection.textContent = 'Waiting for local VM';
        connection.classList.remove('live');
        waitingText.textContent = 'Start the local Rove runtime to attach. This page never starts an online game.';
      }
    } catch {
      connection.textContent = wasConnected ? 'Local VM reconnecting' : 'Local runtime unavailable';
      connection.classList.remove('live');
      waitingText.textContent = wasConnected
        ? 'The local runtime is temporarily unavailable.'
        : 'Start the local Rove runtime to attach. This page never starts an online game.';
    }
  }

  window.addEventListener('DOMContentLoaded', () => {
    window.dispatchEvent(new Event('rove:workspace'));
    void refreshLocalStatus();
    window.setInterval(refreshLocalStatus, 1500);
  }, { once: true });
})();
