"""Evidence labels for received root poses; local prediction is never evidence."""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Iterable


@dataclass(frozen=True)
class PoseSample:
    timestamp: float
    position: tuple[float, float, float]


class MovementVerifier:
    """Measure server-received displacement of one caller-identified own root.

    ``add_pose`` requires explicit provenance and root ownership assertions from
    the caller. It deliberately says *movement*, not *walking*: poses alone do
    not distinguish walking from falling, vehicles, impulses, or other motion.
    """

    def __init__(self, *, max_gap: float = 1.0, max_speed: float = 80.0,
                 min_duration: float = 1.0, min_distance: float = 2.0,
                 min_samples: int = 4, history_limit: int = 4096):
        for name, value in (("max_gap", max_gap), ("max_speed", max_speed),
                            ("min_duration", min_duration), ("min_distance", min_distance)):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if type(min_samples) is not int or min_samples < 2:
            raise ValueError("min_samples must be an integer of at least two")
        if type(history_limit) is not int or history_limit < min_samples or history_limit > 100_000:
            raise ValueError("history_limit must fit min_samples and be at most 100000")
        self.max_gap = float(max_gap)
        self.max_speed = float(max_speed)
        self.min_duration = float(min_duration)
        self.min_distance = float(min_distance)
        self.min_samples = min_samples
        self.history_limit = history_limit
        self._samples: list[PoseSample] = []
        self._rejected = 0

    def add_pose(self, position: Iterable[float], *, timestamp: float | None = None,
                 source: str, own_root: bool) -> None:
        """Record a pose only when explicitly marked received and own-root.

        Raises ValueError for untrusted provenance, malformed coordinates, or
        non-monotonic timestamps; rejected local/predicted values are not stored.
        """
        if source != "received" or own_root is not True:
            self._rejected += 1
            raise ValueError("pose must be a received pose for the own character root")
        if timestamp is None:
            timestamp = time.monotonic()
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
            self._rejected += 1
            raise ValueError("timestamp must be finite monotonic seconds")
        try:
            coords = tuple(position)
        except TypeError:
            self._rejected += 1
            raise ValueError("position must contain three finite coordinates") from None
        if len(coords) != 3 or any(isinstance(v, bool) or not isinstance(v, (int, float))
                                   or not math.isfinite(v) or abs(v) > 1e7 for v in coords):
            self._rejected += 1
            raise ValueError("position must contain three bounded finite coordinates")
        point = tuple(float(v) for v in coords)
        t = float(timestamp)
        if self._samples and t <= self._samples[-1].timestamp:
            self._rejected += 1
            raise ValueError("received pose timestamps must increase monotonically")
        self._samples.append(PoseSample(t, point))
        if len(self._samples) > self.history_limit:
            del self._samples[:len(self._samples) - self.history_limit]

    def report(self) -> dict:
        """Return sanitized aggregate evidence with an explicit measurement label."""
        points = self._samples
        result = {
            "evidence_label": "server_received_own_root_poses",
            "classification": "insufficient_received_samples",
            "sustained_received_movement": False,
            "sample_count": len(points),
            "rejected_sample_count": self._rejected,
            "observed_distance_studs": 0.0,
            "observed_duration_seconds": 0.0,
            "largest_sample_gap_seconds": 0.0,
            "implausible_speed_segments": 0,
            "stale_gap_segments": 0,
            "local_prediction_used": False,
        }
        if len(points) < 2:
            return result
        result["observed_duration_seconds"] = round(points[-1].timestamp - points[0].timestamp, 6)
        distance = 0.0
        largest_gap = 0.0
        bad_speed = 0
        stale = 0
        for previous, current in zip(points, points[1:]):
            dt = current.timestamp - previous.timestamp
            largest_gap = max(largest_gap, dt)
            delta = math.dist(previous.position, current.position)
            distance += delta
            if dt > self.max_gap:
                stale += 1
            elif delta / dt > self.max_speed:
                bad_speed += 1
        result.update(observed_distance_studs=round(distance, 6),
                      largest_sample_gap_seconds=round(largest_gap, 6),
                      implausible_speed_segments=bad_speed, stale_gap_segments=stale)
        if stale:
            result["classification"] = "stale_or_gapped_samples"
        elif bad_speed:
            result["classification"] = "teleport_or_implausible_speed"
        elif (len(points) >= self.min_samples
              and result["observed_duration_seconds"] >= self.min_duration
              and distance >= self.min_distance):
            result["classification"] = "sustained_received_movement"
            result["sustained_received_movement"] = True
        else:
            result["classification"] = "observed_displacement"
        return result
