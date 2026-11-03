"""Offline-only replay glue for local intent and explicitly received poses.

Input is a normalized, already-decoded event stream. This module does not
parse Roblox transport packets, authenticate, load a Roblox client, or perform
network I/O. Compact position/velocity codecs are independent primitives, not
a complete outbound physics record.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable, Mapping

from movement_verification import MovementVerifier
from physics_reconciliation import PhysicsReconciler
from physics_controller import OwnCharacterPhysicsController
from physics_velocity import decode as decode_velocity


class OfflinePhysicsReplay:
    """Replay normalized events through local prediction and received evidence."""

    def __init__(self, *, position=(0.0, 0.0, 0.0), walk_speed=16.0):
        # A tiny synthetic, explicitly unanchored snapshot exercises the same
        # local-intention controller as the runtime. No live world is loaded.
        snapshot = _synthetic_snapshot(position, walk_speed)
        self.controller = OwnCharacterPhysicsController(snapshot)
        self.reconciler = PhysicsReconciler()
        self.verifier = MovementVerifier()
        self._local_steps = 0
        self._received_count = 0
        self._stopped = True
        self._intent_sequence = 0

    def apply(self, event: Mapping) -> dict:
        """Apply one event and return a sanitized summary without source IDs."""
        if not isinstance(event, Mapping):
            raise TypeError("event must be a mapping")
        kind = event.get("kind")
        if kind == "intent":
            self._intent_sequence += 1
            self.controller.consume([{
                "sequence": self._intent_sequence, "kind": "Move",
                "instance_id": "synthetic-humanoid", "vector": event.get("direction"),
                "relative_to_camera": event.get("relative_to_camera", False),
            }])
            self._stopped = False
            return {"event": "local_intent_recorded", "confirmed": False,
                    "accepted": self.controller.accepted,
                    "rejected": self.controller.rejected}
        if kind == "stop":
            self._intent_sequence += 1
            self.controller.consume([{"sequence": self._intent_sequence, "kind": "Stop",
                                      "instance_id": "synthetic-humanoid"}])
            self._stopped = True
            return {"event": "local_controller_stopped", "confirmed": False}
        if kind == "step":
            dt = event.get("dt")
            stamp = event.get("timestamp")
            sample = self.controller.step(dt, timestamp=stamp)
            state = sample["predicted_state"]
            point = state["position"] if state else None
            if point is not None:
                self.reconciler.record_outbound_intent(point, timestamp=stamp)
            self._local_steps += 1
            return {"event": "local_prediction", "position": point,
                    "moving": bool(state and any(state["linear_velocity"])),
                    "confirmed": sample["server_confirmed"],
                    "network_packets_sent": sample["network_packets_sent"]}
        if kind == "received_pose":
            result = self.reconciler.record_received_pose(
                event.get("position"), sequence=event.get("sequence"),
                timestamp=event.get("timestamp"), source=event.get("source"),
                own_root=event.get("own_root"), generation=event.get("generation"))
            if result.get("confirmed"):
                self.verifier.add_pose(event["position"], timestamp=event["timestamp"],
                                       source="received", own_root=True)
                self._received_count += 1
            return {"event": result["status"], "confirmed": result.get("confirmed", False),
                    "position": result.get("position")}
        if kind == "compact_velocity":
            decoded = decode_velocity(event.get("bytes", b""))
            return {"event": "compact_velocity_decoded", "velocity": decoded.vector,
                    "packed_length": len(decoded.packed), "zero_sentinel": decoded.zero_sentinel}
        raise ValueError("unsupported normalized event kind")

    def replay(self, events: Iterable[Mapping]) -> list[dict]:
        return [self.apply(event) for event in events]

    def report(self) -> dict:
        return {
            "mode": "offline_normalized_event_replay",
            "network_io": False,
            "complete_physics_packet_codec": False,
            "local_steps": self._local_steps,
            "received_pose_count": self._received_count,
            "controller_stopped": self._stopped,
            "local_position": (self.controller.controller.position
                               if self.controller.controller is not None else None),
            "reconciliation": self.reconciler.report(),
            "received_movement": self.verifier.report(),
        }


def _synthetic_snapshot(position, walk_speed):
    if len(position) != 3:
        raise ValueError("position must have three coordinates")
    return {"local_player": "synthetic-player", "nodes": [
        {"id": "synthetic-player", "class_name": "Player", "properties": {
            "Character": {"$ref": "synthetic-character"}}},
        {"id": "synthetic-character", "class_name": "Model", "properties": {}},
        {"id": "synthetic-humanoid", "class_name": "Humanoid", "parent": "synthetic-character",
         "properties": {"WalkSpeed": walk_speed}},
        {"id": "synthetic-root", "class_name": "Part", "name": "HumanoidRootPart",
         "parent": "synthetic-character", "properties": {
             "Anchored": False, "Position": {"$vector3": list(position)}}},
    ]}


def synthetic_fixture() -> list[dict]:
    """Small deterministic fixture; all coordinates are invented locally."""
    events = [{"kind": "intent", "direction": (1.0, 0.0, 0.0)}]
    events.extend({"kind": "step", "dt": 0.25, "timestamp": n * 0.25}
                  for n in range(1, 6))
    events.append({"kind": "stop"})
    events.extend({"kind": "step", "dt": 0.25, "timestamp": n * 0.25}
                  for n in range(6, 9))
    # Synthetic received poses deliberately form a separate evidence stream.
    events.extend({"kind": "received_pose", "position": (float(n), 0.0, 0.0),
                   "sequence": n, "timestamp": 2.0 + n * 0.25,
                   "source": "received", "own_root": True}
                  for n in range(1, 6))
    return events


def _load_jsonl(path: Path) -> list[dict]:
    """Read normalized JSONL events; never echo input lines or paths."""
    events = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on input line {line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"input line {line_number} must be a JSON object")
            events.append(row)
    return events


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path,
                        help="optional JSONL of normalized, already-decoded events")
    parser.add_argument("--fixture", action="store_true",
                        help="run a deterministic synthetic event stream")
    args = parser.parse_args(argv)
    if args.fixture and args.input:
        parser.error("choose either an input file or --fixture")
    events = synthetic_fixture() if args.fixture or not args.input else _load_jsonl(args.input)
    replay = OfflinePhysicsReplay()
    replay.replay(events)
    print(json.dumps(replay.report(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
