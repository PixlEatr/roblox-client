"""Fail-closed orchestration for bounded physics staging or submission.

Network use requires an explicit ``PhysicsSendAdapter`` with evidenced payload
and route configuration. This module does not choose an application/channel,
encode a game packet, or infer ownership. A successful send count means the
underlying client send method returned without error; it does not mean the
server accepted or replicated the update.
"""
from collections.abc import Mapping
from itertools import islice
import math

from physics_authority import assess_physics_authority


MAX_SESSION_SECONDS = 60.0
MAX_SESSION_STEPS = 600
MAX_STEP_SECONDS = 0.25
MAX_DATAGRAM_PAYLOAD = 1100


def _valid_protocol_evidence(evidence):
    """Require explicit evidence for both payload schema and channel binding."""
    return (isinstance(evidence, Mapping)
            and evidence.get('outbound_payload_confirmed') is True
            and evidence.get('application_channel_confirmed') is True
            and isinstance(evidence.get('source'), str)
            and bool(evidence['source'].strip()))


class PhysicsSendAdapter:
    """Explicit mapping from encoded physics bytes to NativeSession's sender.

    ``route_evidence`` must identify the verified payload schema and exact
    application/channel pair. No defaults are provided. ``close`` disables
    this adapter locally without closing the shared game session.
    """

    def __init__(self, send_unreliable, *, application, channel,
                 route_evidence):
        if not callable(send_unreliable):
            raise TypeError('send_unreliable must be callable')
        if type(application) is not int or not 0 <= application <= 8:
            raise ValueError('application must be an explicitly verified id from 0 through 8')
        if type(channel) is not int or not 0 <= channel < 32:
            raise ValueError('channel must be an explicitly verified id from 0 through 31')
        if not _valid_protocol_evidence(route_evidence):
            raise ValueError('route_evidence must confirm payload and application/channel')
        if (route_evidence.get('application') != application
                or route_evidence.get('channel') != channel):
            raise ValueError('route_evidence application/channel do not match configuration')
        self._send_unreliable = send_unreliable
        self.application = application
        self.channel = channel
        self.evidence = dict(route_evidence)
        self.closed = False

    def send(self, payload):
        """Send one already-encoded payload once; propagate ambiguous failures."""
        if self.closed:
            raise RuntimeError('physics send adapter is closed')
        if not isinstance(payload, bytes) or len(payload) > MAX_DATAGRAM_PAYLOAD:
            raise ValueError('payload must be bytes no larger than 1100 bytes')
        # NativeSession.send_unreliable returns None on normal completion.
        # Raising means transmission may still be ambiguous, so callers must
        # latch the failure and never retry this frame.
        return self._send_unreliable(self.channel, payload,
                                     application=self.application) is not False

    def close(self):
        """Disable this adapter while leaving its shared session untouched."""
        self.closed = True


class PhysicsSession:
    """Bounded coordinator; stages locally or submits through an explicit adapter.

    ``controller.step(dt)`` must return a mapping representing its current
    output. ``packet_encoder(output)`` returns already encoded bytes. With no
    sender, transport must expose ``stage(bytes)`` and output remains local. A
    sender must be a ``PhysicsSendAdapter``. Authority is rechecked each tick.
    """

    def __init__(self, controller=None, packet_encoder=None, transport=None,
                 *, protocol_evidence=None, sender=None, enabled=False,
                 max_duration=MAX_SESSION_SECONDS,
                 max_steps=MAX_SESSION_STEPS):
        if type(enabled) is not bool:
            raise ValueError('enabled must be a boolean')
        if (isinstance(max_duration, bool) or not isinstance(max_duration, (int, float))
                or not math.isfinite(max_duration) or not 0 < max_duration <= MAX_SESSION_SECONDS):
            raise ValueError('max_duration must be finite and from 0 through 60 seconds')
        if type(max_steps) is not int or not 1 <= max_steps <= MAX_SESSION_STEPS:
            raise ValueError('max_steps must be an integer from 1 through 600')
        self.controller = controller
        self.packet_encoder = packet_encoder
        self.transport = transport
        self.sender = sender
        self.protocol_evidence = protocol_evidence
        self.enabled = enabled
        self.max_duration = float(max_duration)
        self.max_steps = max_steps
        self._generation = 0
        self._running = False
        self._elapsed = 0.0
        self._steps = 0
        self._intents = 0
        self._staged = 0
        self._network_send_attempts = 0
        self._network_send_count = 0
        self._send_failed = False
        self._rejected = 0
        self._last_reason = 'disabled'

    def start(self):
        """Start one bounded generation, or report why configuration is unsafe."""
        self._generation += 1
        self._running = False
        self._elapsed = 0.0
        self._steps = 0
        if not self.enabled:
            self._last_reason = 'disabled_by_default'
            return self._result(False, self._last_reason)
        if not _valid_protocol_evidence(self.protocol_evidence):
            self._last_reason = 'outbound_protocol_not_evidenced'
            return self._result(False, self._last_reason)
        if self.controller is None or not callable(getattr(self.controller, 'step', None)):
            self._last_reason = 'controller_unavailable'
            return self._result(False, self._last_reason)
        if not callable(self.packet_encoder):
            self._last_reason = 'packet_encoder_unavailable'
            return self._result(False, self._last_reason)
        if self._send_failed:
            self._last_reason = 'sender_failed_no_retry'
            return self._result(False, self._last_reason)
        if self.sender is not None:
            if not isinstance(self.sender, PhysicsSendAdapter):
                self._last_reason = 'sender_must_be_evidenced_adapter'
                return self._result(False, self._last_reason)
            if self.sender.closed:
                self._last_reason = 'sender_closed'
                return self._result(False, self._last_reason)
            if not _valid_protocol_evidence(self.sender.evidence):
                self._last_reason = 'outbound_protocol_not_evidenced'
                return self._result(False, self._last_reason)
        elif not callable(getattr(self.transport, 'stage', None)):
            self._last_reason = 'staging_adapter_unavailable'
            return self._result(False, self._last_reason)
        self._running = True
        self._last_reason = ('ready_for_evidenced_submission' if self.sender is not None
                             else 'ready_for_local_staging')
        return self._result(True, self._last_reason)

    def consume_intents(self, intents):
        """Pass bounded intent records into controllers that support consume()."""
        if not self._running:
            self._rejected += len(intents) if hasattr(intents, '__len__') else 1
            return self._result(False, 'session_not_running')
        consume = getattr(self.controller, 'consume', None)
        if not callable(consume):
            self._rejected += 1
            return self._result(False, 'controller_does_not_accept_intents')
        try:
            known_length = len(intents)
        except (TypeError, AttributeError):
            known_length = None
        if known_length is not None and known_length > 256:
            self._rejected += known_length
            return self._result(False, 'intent_batch_exceeds_limit')
        batch = list(islice(iter(intents), 257))
        if len(batch) > 256:
            self._rejected += len(batch)
            return self._result(False, 'intent_batch_exceeds_limit')
        try:
            consume(batch)
        except Exception:
            self._rejected += len(batch)
            self._last_reason = 'controller_rejected_intents'
            return self._result(False, self._last_reason)
        self._intents += len(batch)
        self._last_reason = 'intents_consumed_locally'
        return self._result(True, self._last_reason)

    def tick(self, dt, *, authority_evidence=None):
        """Advance and locally stage one encoded frame when every gate passes."""
        if not self._running:
            self._rejected += 1
            return self._result(False, 'session_not_running')
        if (isinstance(dt, bool) or not isinstance(dt, (int, float))
                or not math.isfinite(dt) or not 0 < dt <= MAX_STEP_SECONDS):
            self._rejected += 1
            return self._result(False, 'invalid_or_oversized_step')
        if self._steps >= self.max_steps or self._elapsed + dt > self.max_duration:
            self.stop(reason='session_bound_reached')
            self._rejected += 1
            return self._result(False, 'session_bound_reached')
        try:
            authority = assess_physics_authority(authority_evidence or {})
        except (TypeError, ValueError):
            authority = {'eligible_for_physics_submission': False,
                         'reasons': ['invalid_authority_evidence']}
        if not authority.get('eligible_for_physics_submission'):
            self._rejected += 1
            self._last_reason = 'authority_unconfirmed'
            return self._result(False, self._last_reason,
                                authority_reasons=tuple(authority.get('reasons', ())))
        try:
            output = self.controller.step(float(dt))
            if not isinstance(output, Mapping):
                raise TypeError('controller output must be a mapping')
            payload = self.packet_encoder(output)
            if not isinstance(payload, bytes):
                raise TypeError('packet encoder must return bytes')
            if self.sender is None:
                self.transport.stage(payload)
        except Exception:
            self._rejected += 1
            self._last_reason = 'controller_encoder_or_staging_failed'
            return self._result(False, self._last_reason)
        self._steps += 1
        self._elapsed += float(dt)
        if self.sender is None:
            self._staged += 1
            self._last_reason = 'locally_staged_only'
            return self._result(True, self._last_reason)

        # Count the attempt before entering native transport code. If it raises
        # or reports False, delivery is ambiguous and the frame is never retried.
        self._network_send_attempts += 1
        try:
            sent = self.sender.send(payload)
        except Exception:
            sent = False
        if sent is not True:
            self._send_failed = True
            self._running = False
            self.sender.close()
            self._last_reason = 'send_result_ambiguous_no_retry'
            return self._result(False, self._last_reason)
        self._network_send_count += 1
        self._last_reason = 'client_send_returned_server_acceptance_unconfirmed'
        return self._result(True, self._last_reason)

    def stop(self, *, reason='stopped'):
        """Stop and invalidate the generation; discard any local staged frames."""
        self._running = False
        self._generation += 1
        drain = getattr(self.transport, 'drain', None)
        if callable(drain):
            try:
                drain()
            except Exception:
                pass
        if self.sender is not None:
            self.sender.close()
        self._last_reason = str(reason)
        return self._result(True, self._last_reason)

    def summary(self):
        """Return counters/status only; never includes payloads or identifiers."""
        return {
            'enabled': self.enabled,
            'running': self._running,
            'generation': self._generation,
            'elapsed_seconds': round(self._elapsed, 6),
            'step_count': self._steps,
            'intent_count': self._intents,
            'staged_count': self._staged,
            'network_send_attempt_count': self._network_send_attempts,
            'network_send_count': self._network_send_count,
            'network_submission_enabled': (self._running and self.sender is not None
                                           and not self.sender.closed),
            'rejected_count': self._rejected,
            'last_status': self._last_reason,
        }

    def _result(self, ok, reason, **details):
        return {'ok': bool(ok), 'reason': reason, 'generation': self._generation,
                'summary': self.summary(), **details}


__all__ = ['PhysicsSession', 'PhysicsSendAdapter', 'MAX_SESSION_SECONDS',
           'MAX_SESSION_STEPS', 'MAX_STEP_SECONDS']
