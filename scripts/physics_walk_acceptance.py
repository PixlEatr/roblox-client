"""Bounded acceptance evidence for an explicitly started, server-observed walk.

This module only consumes poses labeled as received for the caller's own root.
It does not drive a client, authenticate, or claim that pose movement proves a
particular physics implementation. Reports contain aggregate evidence only.
"""
from __future__ import annotations

import math
import time
from typing import Iterable

from movement_verification import MovementVerifier


class PhysicsWalkAcceptance:
    """Track a one-minute movement trial with explicit start and stop events."""

    MIN_TRIAL_SECONDS = 60.0
    MAX_TRIAL_SECONDS = 60.0

    def __init__(self, *, trial_seconds: float = 60.0, min_samples: int = 20,
                 max_gap: float = 1.0, max_speed: float = 80.0,
                 min_distance: float = 2.0, stop_settle_seconds: float = 1.0,
                 stop_tolerance: float = 0.75):
        if (isinstance(trial_seconds, bool) or not isinstance(trial_seconds, (int, float))
                or not math.isfinite(trial_seconds)
                or not self.MIN_TRIAL_SECONDS <= trial_seconds <= self.MAX_TRIAL_SECONDS):
            raise ValueError("trial_seconds must be exactly 60 seconds")
        if type(min_samples) is not int or not 20 <= min_samples <= 10000:
            raise ValueError("min_samples must be between 20 and 10000")
        for name, value in (("max_gap", max_gap), ("max_speed", max_speed),
                            ("min_distance", min_distance),
                            ("stop_settle_seconds", stop_settle_seconds),
                            ("stop_tolerance", stop_tolerance)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        self.trial_seconds = float(trial_seconds)
        self.min_samples = min_samples
        self.max_gap = float(max_gap)
        self.max_speed = float(max_speed)
        self.min_distance = float(min_distance)
        self.stop_settle_seconds = float(stop_settle_seconds)
        self.stop_tolerance = float(stop_tolerance)
        self.reset()

    def reset(self) -> None:
        self._started_at: float | None = None
        self._stopped_at: float | None = None
        self._last_event_at: float | None = None
        self._disconnected = False
        self._clean_leave_at: float | None = None
        self._corrections = 0
        self._verifier: MovementVerifier | None = None
        self._post_stop: list[tuple[float, tuple[float, float, float]]] = []
        self._fall_observed = False
        self._landing_observed = False

    @staticmethod
    def _time(timestamp: float | None) -> float:
        if timestamp is None:
            timestamp = time.monotonic()
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
            raise ValueError("timestamp must be finite monotonic seconds")
        return float(timestamp)

    def start_test(self, *, timestamp: float | None = None) -> None:
        """Explicitly begin a fresh trial; a repeated start must be reset first."""
        if self._started_at is not None:
            raise RuntimeError("trial already started; reset before starting another")
        t = self._time(timestamp)
        self._started_at = self._last_event_at = t
        self._verifier = MovementVerifier(
            max_gap=self.max_gap, max_speed=self.max_speed,
            min_duration=self.trial_seconds, min_distance=self.min_distance,
            min_samples=self.min_samples, history_limit=max(self.min_samples, 10000))

    def add_received_pose(self, position: Iterable[float], *, timestamp: float | None = None,
                          source: str, own_root: bool,
                          received_vertical_velocity_y: float | None = None,
                          received_vertical_velocity_origin: str | None = None,
                          anchored: bool | None = None,
                          anchored_origin: str | None = None,
                          supported_by_received_geometry: bool | None = None,
                          support_evidence: str | None = None) -> None:
        """Record a server-received own-root pose. Local/predicted poses are rejected."""
        if self._started_at is None or self._verifier is None:
            raise RuntimeError("start_test must be called before recording poses")
        if self._disconnected:
            raise RuntimeError("cannot record poses after disconnect")
        if self._clean_leave_at is not None:
            raise RuntimeError("cannot record poses after clean leave")
        t = self._time(timestamp)
        if self._last_event_at is not None and t <= self._last_event_at:
            raise ValueError("event timestamps must increase monotonically")
        if self._stopped_at is not None and t > self._stopped_at + max(10.0, self.stop_settle_seconds + 2 * self.max_gap):
            raise RuntimeError("post-stop evidence window has expired")
        if received_vertical_velocity_y is not None and (
                isinstance(received_vertical_velocity_y, bool)
                or not isinstance(received_vertical_velocity_y, (int, float))
                or not math.isfinite(received_vertical_velocity_y)):
            raise ValueError("received_vertical_velocity_y must be an explicit received property")
        if (received_vertical_velocity_y is not None
                and received_vertical_velocity_origin not in ("received", "derived_received")):
            raise ValueError("vertical velocity requires received property provenance")
        if anchored is not None and type(anchored) is not bool:
            raise ValueError("anchored must be an explicitly received boolean")
        if anchored is not None and anchored_origin not in (
                "received", "derived_received", "class_default"):
            raise ValueError("anchored state requires received or verified class-default provenance")
        if supported_by_received_geometry is not None and type(supported_by_received_geometry) is not bool:
            raise ValueError("supported_by_received_geometry must be an explicit boolean")
        if (supported_by_received_geometry is True
                and (not isinstance(support_evidence, str)
                     or not support_evidence.startswith("received_geometry:"))):
            raise ValueError("landing support requires received_geometry evidence")
        if support_evidence is not None and (
                not isinstance(support_evidence, str)
                or not support_evidence.startswith("received_geometry:")):
            raise ValueError("unsupported geometry evidence label")
        self._verifier.add_pose(position, timestamp=t, source=source, own_root=own_root)
        # Positions alone never establish falling or landing. Require a
        # received downward velocity while unanchored and unsupported, followed
        # by a later collision against geometry built from received world data.
        if (self._stopped_at is None and anchored is False
                and received_vertical_velocity_y is not None and received_vertical_velocity_y < -1.0
                and supported_by_received_geometry is False):
            self._fall_observed = True
        if (self._fall_observed and anchored is False
                and supported_by_received_geometry is True
                and received_vertical_velocity_y is not None
                and abs(received_vertical_velocity_y) <= 2.0):
            self._landing_observed = True
        self._last_event_at = t

    def note_server_correction(self, *, timestamp: float | None = None) -> None:
        """Count an explicitly observed server correction; never treat it as a pose."""
        if self._started_at is None:
            raise RuntimeError("start_test must be called first")
        if self._disconnected:
            raise RuntimeError("trial is disconnected")
        if self._stopped_at is not None:
            raise RuntimeError("trial already stopped")
        if self._clean_leave_at is not None:
            raise RuntimeError("clean leave already recorded")
        t = self._time(timestamp)
        self._check_event_time(t)
        self._corrections += 1
        self._last_event_at = t

    def disconnect(self, *, timestamp: float | None = None) -> None:
        """Mark the trial interrupted by a connection loss."""
        self._require_active_event()
        t = self._time(timestamp)
        self._check_event_time(t)
        self._disconnected = True
        self._last_event_at = t

    def command_stop(self, *, timestamp: float | None = None) -> None:
        """Record the caller's explicit stop command, closing the trial input."""
        self._require_active_event()
        t = self._time(timestamp)
        self._check_event_time(t)
        self._stopped_at = t
        self._last_event_at = t

    def mark_clean_leave(self, *, timestamp: float | None = None,
                         source: str) -> None:
        """Record a normal transport close after stop and received settling."""
        if source != "transport_clean_leave":
            raise ValueError("leave must be confirmed by the transport")
        if self._stopped_at is None:
            raise RuntimeError("command_stop must precede clean leave")
        if self._started_at is None:
            raise RuntimeError("start_test must be called first")
        if self._disconnected:
            raise RuntimeError("trial is disconnected")
        if self._clean_leave_at is not None:
            raise RuntimeError("clean leave already recorded")
        t = self._time(timestamp)
        self._check_event_time(t)
        if not self._stop_is_stable():
            raise RuntimeError("clean leave requires received post-stop settling samples")
        self._clean_leave_at = t
        self._last_event_at = t

    def _require_active_event(self) -> None:
        if self._started_at is None:
            raise RuntimeError("start_test must be called first")
        if self._disconnected:
            raise RuntimeError("trial is disconnected")
        if self._stopped_at is not None:
            raise RuntimeError("trial already stopped")

    def _check_event_time(self, timestamp: float) -> None:
        if self._last_event_at is not None and timestamp <= self._last_event_at:
            raise ValueError("event timestamps must increase monotonically")

    def report(self) -> dict:
        """Return privacy-safe aggregates and conservative acceptance status."""
        pose_report = self._verifier.report() if self._verifier else None
        elapsed = (self._stopped_at - self._started_at
                   if self._started_at is not None and self._stopped_at is not None else 0.0)
        stop_stable = self._stop_is_stable()
        complete = bool(
            self._started_at is not None and self._stopped_at is not None
            and not self._disconnected and elapsed >= self.trial_seconds
            and self._clean_leave_at is not None
            and self._fall_observed and self._landing_observed
            and pose_report and pose_report["sustained_received_movement"]
            and self._horizontal_distance() >= self.min_distance
            and pose_report["sample_count"] >= self.min_samples
            and self._active_movement_window_count() >= 6
            and stop_stable
        )
        if complete:
            status = "accepted_server_received_movement_trial"
        elif self._started_at is None:
            status = "not_started"
        elif self._disconnected:
            status = "interrupted_disconnect"
        elif self._stopped_at is None:
            status = "incomplete_not_stopped"
        elif elapsed < self.trial_seconds:
            status = "incomplete_duration"
        elif not self._fall_observed:
            status = "received_fall_not_confirmed"
        elif not self._landing_observed:
            status = "received_supported_landing_not_confirmed"
        elif not pose_report or not pose_report["sustained_received_movement"]:
            status = "insufficient_received_movement_evidence"
        elif self._horizontal_distance() < self.min_distance:
            status = "insufficient_horizontal_movement_evidence"
        elif self._active_movement_window_count() < 6:
            status = "movement_not_sustained_across_trial"
        elif not stop_stable:
            status = "stop_not_confirmed_by_received_poses"
        elif self._clean_leave_at is None:
            status = "clean_transport_leave_not_confirmed"
        else:
            status = "insufficient_received_samples"
        return {
            "evidence_label": "server_received_own_root_state_and_explicit_test_events",
            "status": status,
            "accepted": complete,
            "trial_started": self._started_at is not None,
            "commanded_stop": self._stopped_at is not None,
            "clean_transport_leave": self._clean_leave_at is not None,
            "disconnected": self._disconnected,
            "trial_duration_seconds": round(max(0.0, elapsed), 3),
            "required_duration_seconds": self.trial_seconds,
            "received_sample_count": pose_report["sample_count"] if pose_report else 0,
            "required_sample_count": self.min_samples,
            "observed_duration_seconds": pose_report["observed_duration_seconds"] if pose_report else 0.0,
            "observed_distance_studs": pose_report["observed_distance_studs"] if pose_report else 0.0,
            "observed_horizontal_distance_studs": round(self._horizontal_distance(), 3),
            "server_correction_count": self._corrections,
            "active_movement_windows_of_12": self._active_movement_window_count(),
            "required_active_movement_windows": 6,
            "stop_stable_from_received_poses": stop_stable,
            "received_downward_velocity_while_unanchored": self._fall_observed,
            "received_geometry_landing_after_fall": self._landing_observed,
            "gravity_inferred_from_positions": False,
            "gait_inferred_from_root_motion": False,
            "largest_sample_gap_seconds": pose_report["largest_sample_gap_seconds"] if pose_report else 0.0,
            "implausible_speed_segments": pose_report["implausible_speed_segments"] if pose_report else 0,
            "stale_gap_segments": pose_report["stale_gap_segments"] if pose_report else 0,
            "local_prediction_used": False,
            "raw_positions_included": False,
        }

    def _stop_is_stable(self) -> bool:
        if self._stopped_at is None or self._verifier is None:
            return False
        samples = self._verifier._samples
        if len(samples) < 2:
            return False
        # A stop is evidenced only if received poses continue after the command
        # and remain within tolerance for the settle window.
        after = [s for s in samples if s.timestamp > self._stopped_at]
        if len(after) < 2 or after[-1].timestamp - after[0].timestamp < self.stop_settle_seconds:
            return False
        if any(b.timestamp - a.timestamp > self.max_gap for a, b in zip(after, after[1:])):
            return False
        return math.dist(after[0].position, after[-1].position) <= self.stop_tolerance

    def _active_movement_window_count(self) -> int:
        if self._started_at is None or self._stopped_at is None or self._verifier is None:
            return 0
        distances: dict[int, float] = {}
        samples = [s for s in self._verifier._samples if self._started_at <= s.timestamp <= self._stopped_at]
        for a, b in zip(samples, samples[1:]):
            dt = b.timestamp - a.timestamp
            # Vertical falls do not count as horizontal displacement. Even
            # this metric is movement only, never gait.
            delta = math.dist((a.position[0], a.position[2]),
                              (b.position[0], b.position[2]))
            if dt <= self.max_gap and delta / dt <= self.max_speed:
                window = int((a.timestamp - self._started_at) // 10.0)
                if 0 <= window < 12:
                    distances[window] = distances.get(window, 0.0) + delta
        return sum(distance >= 0.5 for distance in distances.values())

    def _horizontal_distance(self) -> float:
        if self._started_at is None or self._stopped_at is None or self._verifier is None:
            return 0.0
        samples = [s for s in self._verifier._samples
                   if self._started_at <= s.timestamp <= self._stopped_at]
        distance = 0.0
        for a, b in zip(samples, samples[1:]):
            dt = b.timestamp - a.timestamp
            if dt <= self.max_gap:
                delta = math.dist((a.position[0], a.position[2]),
                                  (b.position[0], b.position[2]))
                if delta / dt <= self.max_speed:
                    distance += delta
        return distance


class PhysicsFallDiagnostic:
    """Short server-received root observation; never an acceptance test.

    This is for the first bounded fall experiment (ten frames across 0.5s).
    Local send-return counters are recorded separately and can never substitute
    for a received pose or be interpreted as server acknowledgement.
    """

    REQUIRED_FRAMES = 10
    REQUIRED_SECONDS = 0.5
    MAX_WINDOW_SECONDS = 1.0

    def __init__(self, *, max_gap: float = 0.15, max_speed: float = 300.0):
        for name, value in (("max_gap", max_gap), ("max_speed", max_speed)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        self.max_gap = float(max_gap)
        self.max_speed = float(max_speed)
        self.reset()

    def reset(self) -> None:
        self._started_at: float | None = None
        self._last_timestamp: float | None = None
        self._samples: list[tuple[float, tuple[float, float, float]]] = []
        self._downward_velocity_observed = False
        self._send_return_count = 0
        self._send_attempt_count = 0
        self._rejected_count = 0

    def start(self, *, timestamp: float | None = None) -> None:
        if self._started_at is not None:
            raise RuntimeError("diagnostic already started; reset first")
        self._started_at = self._last_timestamp = PhysicsWalkAcceptance._time(timestamp)

    def note_client_send_result(self, *, returned: bool) -> None:
        """Count local send API outcomes only; this says nothing about server receipt."""
        if self._started_at is None:
            raise RuntimeError("start must be called before recording sends")
        if type(returned) is not bool:
            raise ValueError("returned must be a boolean")
        self._send_attempt_count += 1
        if returned:
            self._send_return_count += 1

    def add_received_pose(self, position: Iterable[float], *, timestamp: float | None = None,
                          source: str, own_root: bool,
                          received_vertical_velocity_y: float | None = None,
                          received_vertical_velocity_origin: str | None = None,
                          anchored: bool | None = None,
                          anchored_origin: str | None = None) -> None:
        if self._started_at is None:
            raise RuntimeError("start must be called before recording poses")
        if source != "received" or own_root is not True:
            self._rejected_count += 1
            raise ValueError("pose must be received for the caller's own root")
        t = PhysicsWalkAcceptance._time(timestamp)
        if self._last_timestamp is not None and t <= self._last_timestamp:
            self._rejected_count += 1
            raise ValueError("event timestamps must increase monotonically")
        if self._started_at is not None and t - self._started_at > self.MAX_WINDOW_SECONDS:
            raise RuntimeError("0.5-second diagnostic window expired")
        if received_vertical_velocity_y is not None and (
                isinstance(received_vertical_velocity_y, bool)
                or not isinstance(received_vertical_velocity_y, (int, float))
                or not math.isfinite(received_vertical_velocity_y)
                or received_vertical_velocity_origin not in ("received", "derived_received")):
            raise ValueError("vertical velocity must have received property provenance")
        if anchored is not None and type(anchored) is not bool:
            raise ValueError("anchored must be a boolean")
        if anchored is not None and anchored_origin not in (
                "received", "derived_received", "class_default"):
            raise ValueError("anchored state must have received provenance")
        coords = tuple(position)
        if len(coords) != 3 or any(isinstance(x, bool) or not isinstance(x, (int, float))
                                   or not math.isfinite(x) or abs(x) > 1e7 for x in coords):
            raise ValueError("position must contain three bounded finite coordinates")
        point = tuple(float(x) for x in coords)
        if self._samples:
            previous_t, previous_position = self._samples[-1]
            dt = t - previous_t
            speed = math.dist(point, previous_position) / dt
            if dt > self.max_gap:
                self._rejected_count += 1
                raise ValueError("received pose gap exceeds diagnostic limit")
            if speed > self.max_speed:
                self._rejected_count += 1
                raise ValueError("received pose speed exceeds diagnostic limit")
        if anchored is False and received_vertical_velocity_y is not None and received_vertical_velocity_y < -0.5:
            self._downward_velocity_observed = True
        self._samples.append((t, point))
        self._last_timestamp = t

    def report(self) -> dict:
        sample_count = len(self._samples)
        pose_span = (self._samples[-1][0] - self._samples[0][0]
                     if sample_count >= 2 else 0.0)
        elapsed = (self._samples[-1][0] - self._started_at
                   if sample_count and self._started_at is not None else 0.0)
        moved = (sample_count >= 2 and
                 math.dist(self._samples[0][1], self._samples[-1][1]) > 0.02)
        descended = (sample_count >= 2 and
                     self._samples[-1][1][1] < self._samples[0][1][1] - 0.02)
        complete = (sample_count >= self.REQUIRED_FRAMES
                    and elapsed >= self.REQUIRED_SECONDS
                    and elapsed <= self.MAX_WINDOW_SECONDS)
        if not complete:
            status = "diagnostic_window_incomplete"
        elif self._downward_velocity_observed:
            status = "received_downward_velocity_while_unanchored"
        elif descended:
            status = "received_downward_root_pose_change_cause_unconfirmed"
        elif moved:
            status = "received_root_pose_changed_direction_unclassified"
        else:
            status = "no_received_root_pose_change_observed"
        return {
            "evidence_label": "bounded_server_received_own_root_pose_diagnostic",
            "status": status,
            "diagnostic_window_complete": complete,
            "accepted": False,
            "server_acceptance_confirmed": False,
            "required_frames": self.REQUIRED_FRAMES,
            "received_pose_count": sample_count,
            "required_duration_seconds": self.REQUIRED_SECONDS,
            "observed_duration_seconds": round(elapsed, 4),
            "observed_pose_span_seconds": round(pose_span, 4),
            "received_root_pose_changed": moved,
            "received_downward_pose_change_observed": descended,
            "received_downward_velocity_while_unanchored": self._downward_velocity_observed,
            "gravity_inferred_from_positions": False,
            "client_send_attempt_count": self._send_attempt_count,
            "client_send_return_count": self._send_return_count,
            "send_count_used_as_server_evidence": False,
            "rejected_pose_count": self._rejected_count,
            "raw_positions_included": False,
        }


__all__ = ["PhysicsFallDiagnostic", "PhysicsWalkAcceptance"]
