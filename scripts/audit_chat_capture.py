"""Summarize chat replication events in an existing private capture.

Reads a small private reference object containing ``run_path``, then streams
one capture JSON array from that run. It never opens a network connection and
prints only aggregate protocol counts, class matches, and CanSend presence.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
from pathlib import Path
from typing import Any, Iterator

from world_replication import World
from world_schema import parse_schema


CHAT_EVENT_PREFIXES = (
    "ClientToServerMessageReplicateSignal",
    "ServerToClientMessageReplicateSignal",
)
UNIVERSE_CHAT_EVENTS = {
    "ClientToServerUniverseChatMessageSignalV1",
    "ServerToClientUniverseChatMessageSignalV1",
}
READY_EVENT = "ClientToServerChatIsLoadedSignal"
CAPTURE_ROW_KEYS = {"direction", "application", "channel", "data_base64"}
BASE_ROW_KEYS = {"direction", "data_base64"}


class JsonArrayStream:
    """Decode a top-level JSON array one item at a time with bounded memory."""

    def __init__(self, path: Path, *, chunk_size: int = 65536, max_item_chars: int = 16 * 1024 * 1024):
        self.path = path
        self.chunk_size = chunk_size
        self.max_item_chars = max_item_chars

    def __iter__(self) -> Iterator[Any]:
        decoder = json.JSONDecoder()
        with self.path.open("r", encoding="utf-8") as stream:
            buffer = ""
            position = 0
            eof = False

            def fill() -> None:
                nonlocal buffer, eof
                if not eof:
                    part = stream.read(self.chunk_size)
                    if part:
                        buffer += part
                    else:
                        eof = True

            def whitespace() -> None:
                nonlocal buffer, position
                while True:
                    while position < len(buffer) and buffer[position].isspace():
                        position += 1
                    if position >= self.chunk_size:
                        buffer = buffer[position:]
                        position = 0
                    if position < len(buffer) or eof:
                        return
                    fill()

            fill()
            whitespace()
            if position >= len(buffer) or buffer[position] != "[":
                raise ValueError("Capture is not a JSON array")
            position += 1
            whitespace()
            if position < len(buffer) and buffer[position] == "]":
                position += 1
                whitespace()
                while not eof:
                    fill()
                    whitespace()
                if buffer[position:].strip():
                    raise ValueError("Unexpected data after capture array")
                return
            while True:
                whitespace()
                if position >= len(buffer):
                    raise ValueError("Capture array ended unexpectedly")
                if buffer[position] == "]":
                    raise ValueError("Invalid capture array separator")
                if buffer[position] != "{":
                    raise ValueError("Capture rows must be JSON objects")
                while True:
                    try:
                        item, end = decoder.raw_decode(buffer, position)
                        break
                    except json.JSONDecodeError:
                        if eof or len(buffer) - position >= self.max_item_chars:
                            raise ValueError("Invalid or oversized capture row") from None
                        fill()
                if not isinstance(item, dict) or end - position > self.max_item_chars:
                    raise ValueError("Capture rows must be bounded JSON objects")
                yield item
                position = end
                whitespace()
                if position >= len(buffer):
                    raise ValueError("Capture array ended unexpectedly")
                if buffer[position] == "]":
                    position += 1
                    whitespace()
                    while not eof:
                        fill()
                        whitespace()
                    if buffer[position:].strip():
                        raise ValueError("Unexpected data after capture array")
                    return
                if buffer[position] != ",":
                    raise ValueError("Invalid capture array separator")
                position += 1
                whitespace()
                if position < len(buffer) and buffer[position] == "]":
                    raise ValueError("Trailing comma in capture array")
                if position > self.chunk_size:
                    buffer = buffer[position:]
                    position = 0


def _capture_file(run_path: Path, capture_name: str | None = None) -> Path:
    secrets = run_path / "secrets"
    if not secrets.is_dir():
        raise ValueError("Run capture directory is unavailable")
    if capture_name is not None:
        if (not capture_name or capture_name in (".", "..")
                or Path(capture_name).name != capture_name
                or "/" in capture_name or "\\" in capture_name):
            raise ValueError("Capture selector must be a simple filename")
        path = secrets / capture_name
        try:
            first = next(iter(JsonArrayStream(path)))
        except (OSError, StopIteration, ValueError, json.JSONDecodeError, UnicodeError):
            raise ValueError("Selected file is not a capture array") from None
        if not isinstance(first, dict) or not CAPTURE_ROW_KEYS <= first.keys():
            raise ValueError("Selected file is not a capture array")
        return path
    matches = []
    for path in sorted(secrets.glob("*.json")):
        try:
            first = next(iter(JsonArrayStream(path)))
        except (OSError, StopIteration, ValueError, json.JSONDecodeError, UnicodeError):
            continue
        if isinstance(first, dict) and CAPTURE_ROW_KEYS <= first.keys():
            matches.append(path)
    if len(matches) > 1:
        raise ValueError("Multiple capture arrays exist in the referenced run")
    if not matches:
        raise ValueError("No capture array was found in the referenced run")
    return matches[0]


def _class_name(world: World, key: Any) -> str | None:
    if not isinstance(key, tuple) or len(key) != 2:
        return None
    instance = world.instances.get(key)
    if instance is None:
        return None
    class_id = instance.get("class")
    if type(class_id) is not int or not 0 <= class_id < len(world.schema["classes"]):
        return None
    return world.schema["classes"][class_id].get("name")


def _count_chat_event(report: dict[str, Any], world: World, direction: str,
                      event: dict[str, Any], elapsed_seconds: float | None = None) -> None:
    name = world.schema["events"][event["event"]]["name"]
    if name == READY_EVENT:
        if direction == "sent":
            timing = report["readiness_timing"]
            timing["ready_signal_count"] += 1
            if elapsed_seconds is not None:
                if timing["ready_signal_first_at_seconds"] is None:
                    timing["ready_signal_first_at_seconds"] = elapsed_seconds
                timing["ready_signal_last_at_seconds"] = elapsed_seconds
        return
    is_universe = name in UNIVERSE_CHAT_EVENTS
    if not is_universe and not name.startswith(CHAT_EVENT_PREFIXES):
        return
    side = "outbound" if direction == "sent" else "inbound"
    report[side + "_chat_events"] += 1

    if is_universe:
        # Universe V1 has no TextSource/TextChannel reference arguments. Count
        # the route without inspecting its text, message ID, user ID, or payload.
        report[side + "_universe_chat_events"] += 1
        return

    args = event.get("arguments", [])
    if name.startswith("ClientToServerMessageReplicateSignal"):
        source, channel = (args[3], args[4]) if len(args) >= 5 else (None, None)
    elif name == "ServerToClientMessageReplicateSignal":
        source, channel = (args[4], args[5]) if len(args) >= 6 else (None, None)
    else:
        # The dictionary V2 event has no typed source/channel slots. The
        # dictionary is deliberately not inspected or copied to public output.
        source, channel = None, None

    source_class = _class_name(world, source)
    channel_class = _class_name(world, channel)
    if source_class is None or channel_class is None:
        report[side + "_source_channel_class_unknown"] += 1
    elif source_class == "TextSource" and channel_class == "TextChannel":
        report[side + "_source_channel_class_matches"] += 1
    else:
        report[side + "_source_channel_class_mismatches"] += 1

    source_node = world.instances.get(source) if isinstance(source, tuple) else None
    properties = source_node.get("properties", {}) if source_node else {}
    can_send = properties.get("CanSend")
    state = "true" if can_send is True else "false" if can_send is False else "unknown"
    report[side + "_explicit_can_send_" + state] += 1


def audit_capture(reference: Path, capture_name: str | None = None) -> dict[str, Any]:
    """Return a sanitized aggregate audit for the capture referenced by JSON."""
    ref = json.loads(reference.read_text(encoding="utf-8"))
    run_value = ref.get("run_path") if isinstance(ref, dict) else None
    if not isinstance(run_value, str):
        raise ValueError("Reference does not contain a run path")
    run_path = Path(run_value)
    capture = _capture_file(run_path, capture_name)

    report: dict[str, Any] = {
        "network_used": False,
        "capture_rows": 0,
        "schema_packets": 0,
        "world_packets_decoded": 0,
        "packet_decode_errors": 0,
        "incoming_packet_decode_errors": 0,
        "outgoing_packet_decode_errors": 0,
        "unsupported_outbound_item4": 0,
        "audit_incomplete": False,
        "incoming_audit_incomplete": False,
        "journal_overflow_detected": False,
        "incoming_journal_overflow_detected": False,
        "outgoing_journal_overflow_detected": False,
        "capture_row_errors": 0,
        "outbound_chat_events": 0,
        "inbound_chat_events": 0,
        "outbound_universe_chat_events": 0,
        "inbound_universe_chat_events": 0,
        "outbound_source_channel_class_matches": 0,
        "outbound_source_channel_class_mismatches": 0,
        "outbound_source_channel_class_unknown": 0,
        "inbound_source_channel_class_matches": 0,
        "inbound_source_channel_class_mismatches": 0,
        "inbound_source_channel_class_unknown": 0,
        "outbound_explicit_can_send_true": 0,
        "outbound_explicit_can_send_false": 0,
        "outbound_explicit_can_send_unknown": 0,
        "inbound_explicit_can_send_true": 0,
        "inbound_explicit_can_send_false": 0,
        "inbound_explicit_can_send_unknown": 0,
        "non_capture_rows": 0,
        "readiness_timing": {
            "ready_signal_count": 0,
            "ready_signal_first_at_seconds": None,
            "ready_signal_last_at_seconds": None,
            "spawn_verified_at_seconds": None,
            "framework_reply_at_seconds": None,
            "ready_to_spawn_seconds": None,
            "ready_to_framework_reply_seconds": None,
        },
    }
    world: World | None = None
    for row in JsonArrayStream(capture):
        report["capture_rows"] += 1
        if not isinstance(row, dict) or not BASE_ROW_KEYS <= row.keys():
            report["capture_row_errors"] += 1
            report["audit_incomplete"] = True
            report["incoming_audit_incomplete"] = True
            continue
        if not CAPTURE_ROW_KEYS <= row.keys():
            report["non_capture_rows"] += 1
            continue
        direction = row.get("direction")
        if direction not in ("sent", "received") or row.get("application") != 4 or row.get("channel") not in (1, 3):
            report["non_capture_rows"] += 1
            if row.get("application") == 4 and row.get("channel") in (1, 3) and direction not in ("sent", "received"):
                report["audit_incomplete"] = True
                report["incoming_audit_incomplete"] = True
            continue
        before = None
        item4_before = 0
        try:
            payload = base64.b64decode(row["data_base64"], validate=True)
            if direction == "received" and row["channel"] == 1 and payload[:1] == b"\x97":
                world = World(parse_schema(payload))
                report["schema_packets"] += 1
                continue
            if world is None:
                continue
            if direction == "received" and row["channel"] == 1 and payload[:1] == b"\x81":
                world.globals(payload)
                continue
            if payload[:1] != b"\x83":
                continue
            before = world.event_sequence
            item4_before = world.items.get(4, 0)
            elapsed = row.get("seconds")
            if (type(elapsed) not in (int, float) or not math.isfinite(elapsed)
                    or elapsed < 0):
                elapsed = None
            world.packet(payload)
            report["world_packets_decoded"] += 1
            journal = [entry for entry in world.event_journal if entry[0] > before]
            expected_count = world.event_sequence - before
            if expected_count > len(journal):
                report["audit_incomplete"] = True
                report["journal_overflow_detected"] = True
                direction_key = "incoming_journal_overflow_detected" if direction == "received" else "outgoing_journal_overflow_detected"
                report[direction_key] = True
                if direction == "received":
                    report["incoming_audit_incomplete"] = True
            for _sequence, event in journal:
                _count_chat_event(report, world, direction, event, elapsed)
        except (ValueError, UnicodeError, IndexError, KeyError, TypeError):
            if world is not None and type(before) is int:
                journal = [entry for entry in world.event_journal if entry[0] > before]
                expected_count = world.event_sequence - before
                if expected_count > len(journal):
                    report["journal_overflow_detected"] = True
                    direction_key = "incoming_journal_overflow_detected" if direction == "received" else "outgoing_journal_overflow_detected"
                    report[direction_key] = True
                    if direction == "received":
                        report["incoming_audit_incomplete"] = True
                for _sequence, event in journal:
                    elapsed = row.get("seconds")
                    if (type(elapsed) not in (int, float) or not math.isfinite(elapsed)
                            or elapsed < 0):
                        elapsed = None
                    _count_chat_event(report, world, direction, event, elapsed)
                item4_after = world.items.get(4, 0)
            else:
                item4_after = 0
                item4_before = 0
            report["audit_incomplete"] = True
            if direction == "received":
                report["incoming_audit_incomplete"] = True
            if direction == "sent" and item4_after > item4_before:
                report["unsupported_outbound_item4"] += item4_after - item4_before
            else:
                report["packet_decode_errors"] += 1
                direction_key = "incoming_packet_decode_errors" if direction == "received" else "outgoing_packet_decode_errors"
                report[direction_key] += 1
    if report["schema_packets"] == 0:
        report["audit_incomplete"] = True
        report["incoming_audit_incomplete"] = True
    session_report_path = run_path / "outputs" / "gamejoin" / "chat-delivery-check.json"
    if session_report_path.is_file():
        try:
            session_report = json.loads(session_report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            session_report = {}
        timing = report["readiness_timing"]
        for key in ("spawn_verified_at_seconds", "framework_reply_at_seconds"):
            value = session_report.get(key) if isinstance(session_report, dict) else None
            if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                timing[key] = value
        ready_at = timing["ready_signal_first_at_seconds"]
        spawn_at = timing["spawn_verified_at_seconds"]
        framework_at = timing["framework_reply_at_seconds"]
        if ready_at is not None and spawn_at is not None:
            timing["ready_to_spawn_seconds"] = round(spawn_at - ready_at, 6)
        if ready_at is not None and framework_at is not None:
            timing["ready_to_framework_reply_seconds"] = round(framework_at - ready_at, 6)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True,
                        help="Private JSON pointer containing a run_path")
    parser.add_argument("--capture", type=str,
                        help="Exact private capture filename within the referenced run's secrets directory")
    parser.add_argument("--output", type=Path,
                        help="Optional destination for the sanitized aggregate JSON")
    args = parser.parse_args()
    try:
        report = audit_capture(args.reference, args.capture)
    except (OSError, ValueError, json.JSONDecodeError, UnicodeError):
        # Avoid exposing private paths, raw capture values, or exception text.
        report = {"network_used": False, "audit_error": True}
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if "audit_error" not in report else 2


if __name__ == "__main__":
    raise SystemExit(main())
