"""Offline reconciliation of local movement predictions with received poses.

This module labels evidence for a caller-owned character root. Predictions and
outbound intents remain local state; only explicitly received own-root samples
can update the confirmed pose. It does not implement Roblox networking.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Iterable


def _point(value: Iterable[float]) -> tuple[float, float, float]:
    try:
        coords = tuple(value)
    except TypeError:
        raise ValueError("position must contain three finite coordinates") from None
    if len(coords) != 3 or any(type(v) not in (int, float) or not math.isfinite(v)
                               or abs(v) > 1e7 for v in coords):
        raise ValueError("position must contain three bounded finite coordinates")
    return tuple(float(v) for v in coords)


@dataclass(frozen=True)
class Prediction:
    timestamp: float
    position: tuple[float, float, float]
    generation: int


@dataclass(frozen=True)
class ReceivedPose:
    sequence: int
    timestamp: float
    position: tuple[float, float, float]
    generation: int


class PhysicsReconciler:
    """Compare local predictions to later, explicitly received root poses.

    `confirmed_position` is changed only by `record_received_pose`. A send or
    prediction can never act as an acknowledgement. A mismatching received pose
    is evidence of a correction; rejection requires an explicit server rejection
    event and is never inferred from position mismatch alone.
    """

    def __init__(self, *, correction_distance: float = 1.0,
                 prediction_max_age: float = 2.0, history_limit: int = 256):
        for name, value in (("correction_distance", correction_distance),
                            ("prediction_max_age", prediction_max_age)):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if type(history_limit) is not int or not 1 <= history_limit <= 100_000:
            raise ValueError("history_limit must be between 1 and 100000")
        self.correction_distance = float(correction_distance)
        self.prediction_max_age = float(prediction_max_age)
        self.history_limit = history_limit
        self.generation = 0
        self.predictions: list[Prediction] = []
        self.received: list[ReceivedPose] = []
        self.confirmed_position: tuple[float, float, float] | None = None
        self.confirmed_timestamp: float | None = None
        self.confirmed_sequence: int | None = None
        self._rejected_input_count = 0
        self._last_event = "awaiting_received_pose"

    @property
    def predicted_position(self) -> tuple[float, float, float] | None:
        return self.predictions[-1].position if self.predictions else None

    def reset_character(self) -> int:
        """Start a new character generation and discard old root evidence."""
        self.generation += 1
        self.predictions.clear()
        self.received.clear()
        self.confirmed_position = None
        self.confirmed_timestamp = None
        self.confirmed_sequence = None
        self._last_event = "character_generation_reset"
        return self.generation

    @staticmethod
    def _timestamp(timestamp: float | None) -> float:
        if timestamp is None:
            timestamp = time.monotonic()
        if type(timestamp) not in (int, float) or not math.isfinite(timestamp):
            raise ValueError("timestamp must be finite monotonic seconds")
        return float(timestamp)

    def record_prediction(self, position: Iterable[float], *, timestamp: float | None = None) -> dict:
        """Store local predicted state without changing received confirmation."""
        point = _point(position)
        stamp = self._timestamp(timestamp)
        if self.predictions and stamp <= self.predictions[-1].timestamp:
            self._rejected_input_count += 1
            return {"status": "stale_or_out_of_order_prediction", "confirmed": False}
        self.predictions.append(Prediction(stamp, point, self.generation))
        del self.predictions[:-self.history_limit]
        return {"status": "local_prediction_recorded", "confirmed": False,
                "generation": self.generation, "position": point}

    def record_outbound_intent(self, position: Iterable[float], *, timestamp: float | None = None) -> dict:
        """Record an outgoing desired pose as a prediction, never confirmation."""
        result = self.record_prediction(position, timestamp=timestamp)
        return {**result, "source": "outbound_intent", "server_acknowledged": False}

    def record_received_pose(self, position: Iterable[float], *, sequence: int,
                             timestamp: float | None = None, source: str,
                             own_root: bool, generation: int | None = None) -> dict:
        """Accept a received own-root pose; classify freshness and correction.

        Stale sequence/timestamp samples are reported and cannot roll back the
        confirmed state. `generation` protects against delayed samples from a
        replaced character. Inputs without explicit received provenance fail
        closed and leave confirmation untouched. Accepted poses discard local
        predictions at or before the sample while retaining later predictions.
        """
        if source != "received" or own_root is not True:
            self._rejected_input_count += 1
            return {"status": "untrusted_provenance", "confirmed": False}
        try:
            point = _point(position)
            stamp = self._timestamp(timestamp)
        except ValueError:
            self._rejected_input_count += 1
            return {"status": "invalid_received_sample", "confirmed": False}
        gen = self.generation if generation is None else generation
        if type(sequence) is not int or sequence < 0:
            self._rejected_input_count += 1
            return {"status": "invalid_sequence", "confirmed": False}
        if type(gen) is not int or gen != self.generation:
            self._rejected_input_count += 1
            return {"status": "stale_character_generation", "confirmed": False}
        if self.confirmed_sequence is not None and sequence <= self.confirmed_sequence:
            self._rejected_input_count += 1
            return {"status": "stale_or_out_of_order_sequence", "confirmed": False}
        if self.confirmed_timestamp is not None and stamp <= self.confirmed_timestamp:
            self._rejected_input_count += 1
            return {"status": "stale_or_out_of_order_timestamp", "confirmed": False}

        matched = None
        for prediction in reversed(self.predictions):
            age = stamp - prediction.timestamp
            if prediction.generation == self.generation and 0 <= age <= self.prediction_max_age:
                matched = prediction
                break
        error = math.dist(point, matched.position) if matched else None
        status = ("server_correction_observed" if error is not None and error > self.correction_distance
                  else "prediction_confirmed_by_received_pose" if matched
                  else "received_pose_confirmed")
        pruned_predictions = sum(
            prediction.generation == self.generation and prediction.timestamp <= stamp
            for prediction in self.predictions
        )
        self.predictions[:] = [
            prediction for prediction in self.predictions
            if prediction.generation != self.generation or prediction.timestamp > stamp
        ]
        self.confirmed_position = point
        self.confirmed_timestamp = stamp
        self.confirmed_sequence = sequence
        self.received.append(ReceivedPose(sequence, stamp, point, self.generation))
        del self.received[:-self.history_limit]
        self._last_event = status
        return {"status": status, "confirmed": True, "evidence": "received_own_root_pose",
                "generation": self.generation, "sequence": sequence, "timestamp": stamp,
                "position": point, "prediction_error": error,
                "pruned_prediction_count": pruned_predictions}

    def record_server_rejection(self, *, sequence: int, timestamp: float | None = None,
                                source: str, own_root: bool,
                                generation: int | None = None) -> dict:
        """Record an explicit received rejection; never infer one from a pose."""
        if source != "received" or own_root is not True:
            self._rejected_input_count += 1
            return {"status": "untrusted_provenance", "server_rejected": False}
        try:
            stamp = self._timestamp(timestamp)
        except ValueError:
            self._rejected_input_count += 1
            return {"status": "invalid_received_sample", "server_rejected": False}
        gen = self.generation if generation is None else generation
        if (type(sequence) is not int or sequence < 0 or type(gen) is not int
                or gen != self.generation):
            self._rejected_input_count += 1
            return {"status": "stale_character_generation_or_invalid_sequence",
                    "server_rejected": False}
        self._last_event = "server_rejection_received"
        return {"status": self._last_event, "server_rejected": True,
                "generation": gen, "sequence": sequence, "timestamp": stamp,
                "confirmed_position_unchanged": True}

    def report(self) -> dict:
        return {
            "evidence_label": "received_own_root_physics_reconciliation",
            "generation": self.generation,
            "confirmed_position": self.confirmed_position,
            "confirmed_sequence": self.confirmed_sequence,
            "confirmed_timestamp": self.confirmed_timestamp,
            "predicted_position": self.predicted_position,
            "prediction_is_confirmation": False,
            "last_event": self._last_event,
            "received_sample_count": len(self.received),
            "local_prediction_count": len(self.predictions),
            "rejected_input_count": self._rejected_input_count,
        }
