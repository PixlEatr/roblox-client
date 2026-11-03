"""Bounded observation of server-received state for the local character.

Input is a world_snapshot from world_snapshot.make_world_snapshot. Values are
accepted only when their per-property origins say received/derived_received,
with Anchored also permitting the narrowly established class_default. The
monitor never merges a local prediction into received observations.
"""
from __future__ import annotations

from collections import deque
import math
import time
from typing import Any


_RECEIVED = frozenset(("received", "derived_received"))
_ANCHORED_ORIGINS = _RECEIVED | {"class_default"}
_MAX_NODES = 100_000
_MAX_SAMPLES = 120


def _vector(value: Any) -> tuple[float, float, float] | None:
    if isinstance(value, dict):
        value = value.get("$vector3")
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        return None
    return tuple(float(v) for v in value)


def _position(node: dict) -> tuple[tuple[float, float, float] | None, str | None]:
    props = node.get("properties", {})
    origins = node.get("origins", {})
    for name in ("Position", "CFrame"):
        if origins.get(name) not in _RECEIVED:
            continue
        value = props.get(name)
        if name == "CFrame" and isinstance(value, dict):
            value = value.get("$cframe", value)
            if isinstance(value, dict):
                value = value.get("position")
        vector = _vector(value)
        if vector is not None:
            return vector, origins[name]
    return None, None


def _received_vector(node: dict, names: tuple[str, ...]) -> tuple[float, float, float] | None:
    props, origins = node.get("properties", {}), node.get("origins", {})
    for name in names:
        if origins.get(name) in _RECEIVED:
            value = _vector(props.get(name))
            if value is not None:
                return value
    return None


def _resolve(snapshot: Any) -> tuple[dict | None, dict | None, dict | None, str | None]:
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("nodes"), list):
        return None, None, None, "invalid_snapshot"
    nodes_list = snapshot["nodes"]
    if len(nodes_list) > _MAX_NODES:
        return None, None, None, "snapshot_node_limit_exceeded"
    nodes = {node.get("id"): node for node in nodes_list if isinstance(node, dict) and node.get("id") is not None}
    player = nodes.get(snapshot.get("local_player"))
    if not player or player.get("class_name") != "Player":
        return None, None, None, "own_player_unresolved"
    char = player.get("properties", {}).get("Character")
    character = nodes.get(char.get("$ref")) if isinstance(char, dict) else None
    if not character or character.get("class_name") != "Model":
        return None, None, None, "character_unresolved"
    descendants = [node for node in nodes_list if isinstance(node, dict) and node.get("parent") == character.get("id")]
    root = next((node for node in descendants if node.get("name") == "HumanoidRootPart" and node.get("class_name") in ("Part", "MeshPart")), None)
    humanoid = next((node for node in descendants if node.get("class_name") == "Humanoid"), None)
    if root is None:
        return character, None, humanoid, "root_unresolved"
    return character, root, humanoid, None


class PhysicsLiveMonitor:
    """Track received physics observations without mistaking prediction for replication."""

    def __init__(self, *, max_samples: int = _MAX_SAMPLES):
        if type(max_samples) is not int or not 2 <= max_samples <= _MAX_SAMPLES:
            raise ValueError("max_samples must be an integer from 2 through 120")
        self.samples = deque(maxlen=max_samples)
        self._root_key = None
        self._fall_seen = False
        self._support_source = None

    def observe(self, snapshot: dict, *, timestamp: float | None = None,
                local_prediction: dict | None = None,
                support_evidence: dict | None = None) -> dict:
        """Read one snapshot. `support_evidence` must be an external, explicit geometry result.

        local_prediction is intentionally returned in its own field and never
        used to calculate observed motion/readiness.
        """
        if timestamp is None:
            timestamp = time.monotonic()
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
            raise ValueError("timestamp must be finite monotonic seconds")
        timestamp = float(timestamp)
        if self.samples and timestamp <= self.samples[-1]["timestamp"]:
            raise ValueError("timestamps must increase monotonically")

        character, root, humanoid, problem = _resolve(snapshot)
        if problem:
            if problem in ("character_unresolved", "root_unresolved", "own_player_unresolved"):
                self.samples.clear()
                self._root_key = None
                self._fall_seen = False
            return self._report(problem, local_prediction=local_prediction)

        key = (character.get("id"), root.get("id"))
        if key != self._root_key:
            self.samples.clear()
            self._root_key = key
            self._fall_seen = False

        position, position_origin = _position(root)
        origins = root.get("origins", {})
        anchored = root.get("properties", {}).get("Anchored")
        anchored_origin = origins.get("Anchored")
        if type(anchored) is not bool or anchored_origin not in _ANCHORED_ORIGINS:
            anchored = None
            anchored_origin = None
        velocity = _received_vector(root, ("AssemblyLinearVelocity", "Velocity"))
        angular = _received_vector(root, ("AssemblyAngularVelocity", "RotVelocity"))
        humanoid_props = humanoid.get("properties", {}) if humanoid else {}
        humanoid_origins = humanoid.get("origins", {}) if humanoid else {}
        walk_speed = humanoid_props.get("WalkSpeed") if humanoid_origins.get("WalkSpeed") in _RECEIVED else None
        if isinstance(walk_speed, bool) or not isinstance(walk_speed, (int, float)) or not math.isfinite(walk_speed):
            walk_speed = None

        support = self._support(support_evidence)
        previous = self.samples[-1] if self.samples else None
        delta = None
        if previous and position is not None and previous["position"] is not None:
            dt = timestamp - previous["timestamp"]
            delta = tuple((position[i] - previous["position"][i]) / dt for i in range(3))
        vertical_velocity_y = velocity[1] if velocity is not None else (delta[1] if delta else None)
        downward = ((velocity is not None and velocity[1] < -0.5)
                    or (delta is not None and delta[1] < -0.5))
        if downward and anchored is False:
            self._fall_seen = True
        row = {"timestamp": timestamp, "position": position, "position_origin": position_origin,
               "anchored": anchored, "anchored_origin": anchored_origin,
               "velocity": velocity, "angular_velocity": angular,
               "walk_speed": float(walk_speed) if walk_speed is not None else None,
               "vertical_velocity_y": vertical_velocity_y,
               "downward_motion_observed": bool(downward and anchored is False),
               "supported_by_received_geometry": support is not None,
               "support_source": support,
               "root_id_private": key[1]}
        self.samples.append(row)
        self._support_source = support
        return self._report("ok", local_prediction=local_prediction)

    @staticmethod
    def _support(evidence: dict | None) -> str | None:
        if not isinstance(evidence, dict) or evidence.get("supported") is not True:
            return None
        source = evidence.get("source")
        if not isinstance(source, str) or not source.startswith("received_geometry:"):
            return None
        return source[:120]

    def _report(self, status: str, *, local_prediction: dict | None) -> dict:
        latest = self.samples[-1] if self.samples else None
        prev = self.samples[-2] if len(self.samples) > 1 else None
        pose_changed = bool(latest and prev and latest["position"] is not None and prev["position"] is not None
                            and math.dist(latest["position"], prev["position"]) > 0.02)
        moving_xy = bool(latest and prev and latest["position"] is not None and prev["position"] is not None
                         and math.dist((latest["position"][0], latest["position"][2]),
                                       (prev["position"][0], prev["position"][2])) > 0.02)
        anchored = latest["anchored"] if latest else None
        pose_ok = bool(latest and latest["position"] is not None)
        ready = bool(latest and latest["root_id_private"] is not None and pose_ok and anchored is False)
        if latest is None:
            reason = status
        elif anchored is None:
            reason = "received_anchored_state_unknown"
        elif anchored:
            reason = "server_reports_anchored"
        elif not pose_ok:
            reason = "received_pose_unavailable"
        elif not latest["velocity"] and not latest["angular_velocity"]:
            reason = "ready_to_test_no_received_velocity"
        else:
            reason = "ready_to_test"
        return {
            "evidence_label": "server_received_own_character_state",
            "status": status,
            "movement_ready_to_test": ready,
            "readiness_reason": reason,
            "sample_count": len(self.samples),
            "received_pose_observed": pose_ok,
            "received_pose_changed": pose_changed,
            "received_horizontal_motion_observed": moving_xy,
            "downward_motion_observed": bool(latest and latest["downward_motion_observed"]),
            "received_downward_motion_seen": self._fall_seen,
            "received_position": latest["position"] if latest else None,
            "position_source": latest["position_origin"] if latest else None,
            "received_vertical_velocity_y": (latest["velocity"][1]
                                               if latest and latest["velocity"] is not None else None),
            "vertical_velocity_y": latest["vertical_velocity_y"] if latest else None,
            "received_velocity": latest["velocity"] if latest else None,
            "received_angular_velocity": latest["angular_velocity"] if latest else None,
            "anchored": anchored,
            "anchored_origin": latest["anchored_origin"] if latest else None,
            "supported_by_received_geometry": bool(latest and latest["supported_by_received_geometry"]),
            "support_source": latest["support_source"] if latest else None,
            "grounded": None,
            "grounded_reason": "no received grounded property or geometry result supplied",
            "local_prediction": local_prediction if isinstance(local_prediction, dict) else None,
            "local_prediction_used_for_received_evidence": False,
        }


__all__ = ["PhysicsLiveMonitor"]
