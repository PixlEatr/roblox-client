"""Summarize received own-root physics evidence from an existing private capture.

This tool reads an existing report and capture only. It never opens a connection,
executes received scripts, or writes packet bytes into its public output.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
from pathlib import Path
from typing import Any

from world_replication import World
from world_schema import parse_schema


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "outputs/gamejoin/private-server-bytecode-auth-visible.json"


def summarize_world(world: Any, packet_count: int, own_user_id: int) -> dict[str, Any]:
    """Resolve the account's root by UserId, then report owner equality independently."""
    classes = {
        key: world.schema["classes"][instance["class"]]["name"]
        for key, instance in world.instances.items()
    }
    players = [
        (key, instance) for key, instance in world.instances.items()
        if classes[key] == "Player"
    ]
    roots = []
    own_players = []
    for player_key, player in players:
        character_key = player["properties"].get("Character")
        character = world.instances.get(character_key)
        root_key = character["properties"].get("PrimaryPart") if character else None
        root = world.instances.get(root_key) if root_key else None
        if root:
            roots.append((player_key, root_key, root))
            if player["properties"].get("UserId") == own_user_id:
                own_players.append((root_key, root))

    if len(own_players) != 1:
        return {
            "evidence_source": "received_capture",
            "status": "account_player_not_uniquely_resolved_by_user_id",
            "received_world_packet_count": packet_count,
            "player_count": len(players),
            "account_player_count": len(own_players),
            "local_prediction_used": False,
        }

    root_key, root = own_players[0]
    own_owner_matches_scope = root["properties"].get("NetworkOwnerV3") == world.client_scope
    other_roots = [item for item in roots if item[1] != root_key]
    other_owner_scope_match_count = sum(
        item[2]["properties"].get("NetworkOwnerV3") == world.client_scope
        for item in other_roots
    )
    class_name = classes.get(root_key)
    props = root["properties"]
    anchored = props.get("Anchored")
    anchored_origin = "server_replication" if type(anchored) is bool else None
    if anchored_origin is None:
        creation = world.creation.get(root_key, {})
        if (class_name in ("Part", "MeshPart")
                and all(creation.get(flag) for flag in ("fresh", "complete", "ordinary"))):
            anchored = False
            anchored_origin = "exact_build_class_default"
        else:
            anchored = None

    received_pose_updates = 0
    position = _position_from_props(props)
    initial_position = position
    final_position = position
    for action in world.actions:
        if action.get("kind") != "set" or action.get("key") != root_key:
            continue
        fields = action.get("fields", {})
        if "CFrame" in fields or "ReplicationPV" in fields:
            received_pose_updates += 1
            update = fields.get("ReplicationPV", fields.get("CFrame"))
            point = _position(update)
            if point is not None:
                if initial_position is None:
                    initial_position = point
                final_position = point
    finite_position = position is not None
    moved = bool(initial_position and final_position and math.dist(initial_position, final_position) > 1e-3)
    return {
        "evidence_source": "received_capture",
        "status": "own_root_resolved",
        "received_world_packet_count": packet_count,
        "player_count": len(players),
        "own_root_class": class_name,
        "own_root_fresh_complete_creation": all(
            world.creation.get(root_key, {}).get(flag)
            for flag in ("fresh", "complete", "ordinary")
        ),
        "anchored": anchored,
        "anchored_origin": anchored_origin,
        "network_owner_matches_client_scope": own_owner_matches_scope,
        "other_player_root_count": len(other_roots),
        "other_player_roots_matching_client_scope": other_owner_scope_match_count,
        "received_position_finite": finite_position,
        "received_root_pose_update_count": received_pose_updates,
        "received_root_pose_sample_count": 1 + received_pose_updates if finite_position else 0,
        "server_received_motion_observed": moved,
        "local_prediction_used": False,
    }


def _position(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, dict):
        return None
    point = value.get("position")
    if (not isinstance(point, (list, tuple)) or len(point) != 3
            or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in point)):
        return None
    return tuple(float(v) for v in point)


def _position_from_props(props: dict[str, Any]) -> tuple[float, float, float] | None:
    return _position(props.get("ReplicationPV")) or _position(props.get("CFrame"))


def analyze_report(report_path: Path, own_user_id: int) -> dict[str, Any]:
    report_path = report_path.resolve()
    report = json.loads(report_path.read_text())
    capture = (ROOT / report["private_capture"]).resolve()
    if not capture.is_relative_to((ROOT / "secrets").resolve()):
        raise ValueError("Capture must remain inside the private secrets directory")

    world = None
    packet_count = 0
    world_packet_count = 0
    for row in json.loads(capture.read_text()):
        if (row.get("direction") != "received" or row.get("application") != 4
                or row.get("channel") != 1):
            continue
        payload = base64.b64decode(row["data_base64"], validate=True)
        packet_count += 1
        if payload[:1] == b"\x97":
            world = World(parse_schema(payload))
        elif payload[:1] == b"\x81":
            if world is None:
                raise ValueError("Globals arrived before the replicated schema")
            world.globals(payload)
        elif payload[:1] == b"\x83":
            if world is None:
                raise ValueError("World data arrived before the replicated schema")
            world.packet(payload)
            world_packet_count += 1
    if world is None:
        raise ValueError("Capture has no received world schema")

    summary = summarize_world(world, world_packet_count, own_user_id)
    summary["received_app_channel_message_count"] = packet_count
    summary["source_report"] = str(report_path.relative_to(ROOT)) if report_path.is_relative_to(ROOT) else "external_report"
    summary["captured_player_spawned"] = report.get("player_spawned") is True
    summary["capture_walk_seconds_verified"] = report.get("walk_seconds_verified", 0)
    return summary


def aggregate_reports(results: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = [row for row in results if row.get("status") == "own_root_resolved"]
    return {
        "evidence_source": "multiple_received_captures",
        "capture_count": len(results),
        "own_account_root_resolved_count": len(resolved),
        "own_owner_matches_client_scope_count": sum(
            row.get("network_owner_matches_client_scope") is True for row in resolved
        ),
        "other_player_root_count": sum(row.get("other_player_root_count", 0) for row in resolved),
        "other_player_scope_match_count": sum(
            row.get("other_player_roots_matching_client_scope", 0) for row in resolved
        ),
        "all_resolved_own_roots_match_scope": bool(resolved) and all(
            row.get("network_owner_matches_client_scope") is True for row in resolved
        ),
        "independent_identity_key": "Player.UserId supplied from private account join metadata",
        "interpretation": "Observed owner-scope equality in these captures; this aggregate does not prove the protocol's universal ownership semantics.",
        "captures": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, action="append",
                        help="Existing authorized session report; repeat to compare captures")
    parser.add_argument("--user-id", type=int, required=True,
                        help="Account UserId used only to resolve Player; it is never written to output")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/physics-final/received.json")
    args = parser.parse_args()
    reports = args.report or [DEFAULT_REPORT]
    results = [analyze_report(report, args.user_id) for report in reports]
    result = results[0] if len(results) == 1 else aggregate_reports(results)
    output = args.output.resolve()
    if not output.is_relative_to(ROOT):
        raise ValueError("Output must remain inside the project")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
