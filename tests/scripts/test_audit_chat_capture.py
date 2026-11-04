"""Focused tests for the sanitized offline chat-capture audit."""
import json
from collections import Counter
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import audit_chat_capture as audit


class FakeWorld:
    def __init__(self, schema):
        self.schema = schema
        self.event_sequence = 0
        self.event_journal = []
        self.items = Counter()
        self.instances = {
            (7, 1): {"class": 0, "properties": {"CanSend": False}},
            (7, 2): {"class": 1, "properties": {}},
        }

    def globals(self, payload):
        raise AssertionError("fixture does not require globals")

    def packet(self, payload):
        if payload == b"\x83ready":
            self.event_sequence += 1
            self.event_journal.append((self.event_sequence, {
                "key": None, "event": 3, "arguments": [],
            }))
        elif payload in (b"\x83good", b"\x83after-error", b"\x83overflow"):
            if payload == b"\x83overflow":
                self.event_sequence += 2
                self.event_journal.append((self.event_sequence, {
                    "key": None, "event": 0,
                    "arguments": [b"private message", b"", b"private-id", (7, 1), (7, 2), False],
                }))
                return
            self.event_sequence += 1
            self.event_journal.append((self.event_sequence, {
                "key": None, "event": 0,
                "arguments": [b"private message", b"", b"private-id", (7, 1), (7, 2), False],
            }))
            if payload == b"\x83after-error":
                raise ValueError("private parser detail must not escape")
        elif payload in (b"\x83universeout", b"\x83universein"):
            self.event_sequence += 1
            event_id = 1 if payload == b"\x83universeout" else 2
            args = ([b"private universe text", b"private metadata", b"private-id", 987654, True]
                    if event_id == 1 else [{"text": "private inbound universe text",
                                            "messageId": "private inbound id"}])
            self.event_journal.append((self.event_sequence, {
                "key": None, "event": event_id, "arguments": args,
            }))
        elif payload == b"\x83item4":
            self.items[4] += 1
            raise ValueError("unsupported replication item")
        else:
            raise ValueError("private parser detail must not escape")


class CaptureAuditTests(unittest.TestCase):
    def test_streams_list_and_emits_only_sanitized_counts(self):
        schema = {"events": [{"name": "ClientToServerMessageReplicateSignalV4"},
                              {"name": "ClientToServerUniverseChatMessageSignalV1"},
                              {"name": "ServerToClientUniverseChatMessageSignalV1"},
                              {"name": "ClientToServerChatIsLoadedSignal"}],
                  "classes": [{"name": "TextSource"}, {"name": "TextChannel"}]}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / "run"
            secrets = run / "secrets"
            secrets.mkdir(parents=True)
            capture = secrets / "capture.json"
            rows = [
                {"direction": "received", "application": 4, "channel": 1,
                 "data_base64": "lw=="},
                {"direction": "sent", "application": 4, "channel": 1,
                 "data_base64": "g2dvb2Q="},
                {"direction": "sent", "application": 4, "channel": 1,
                 "seconds": 2.5, "data_base64": "g3JlYWR5"},
                {"direction": "received_datagram", "bytes": 18,
                 "data_base64": "cHJpdmF0ZSBkYXRh"},
            ]
            capture.write_text(json.dumps(rows), encoding="utf-8")
            report_path = run / "outputs" / "gamejoin"
            report_path.mkdir(parents=True)
            (report_path / "chat-delivery-check.json").write_text(json.dumps({
                "spawn_verified_at_seconds": 3.25,
                "framework_reply_at_seconds": 4.5,
            }), encoding="utf-8")
            reference = root / "reference.json"
            reference.write_text(json.dumps({"run_path": str(run)}), encoding="utf-8")
            with mock.patch.object(audit, "parse_schema", return_value=schema), \
                    mock.patch.object(audit, "World", FakeWorld):
                report = audit.audit_capture(reference)

        encoded = json.dumps(report)
        self.assertEqual(report["capture_rows"], 4)
        self.assertEqual(report["outbound_chat_events"], 1)
        self.assertEqual(report["readiness_timing"], {
            "ready_signal_count": 1,
            "ready_signal_first_at_seconds": 2.5,
            "ready_signal_last_at_seconds": 2.5,
            "spawn_verified_at_seconds": 3.25,
            "framework_reply_at_seconds": 4.5,
            "ready_to_spawn_seconds": 0.75,
            "ready_to_framework_reply_seconds": 2.0,
        })
        self.assertEqual(report["outbound_source_channel_class_matches"], 1)
        self.assertEqual(report["outbound_explicit_can_send_false"], 1)
        self.assertEqual(report["packet_decode_errors"], 0)
        self.assertEqual(report["capture_row_errors"], 0)
        self.assertEqual(report["non_capture_rows"], 1)
        self.assertFalse(report["network_used"])
        self.assertNotIn("private message", encoded)
        self.assertNotIn("private-id", encoded)
        self.assertNotIn("private parser detail", encoded)

    def test_stream_reader_handles_rows_larger_than_one_chunk(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rows.json"
            path.write_text(json.dumps([{"value": "x" * 300}, {"value": 2}]), encoding="utf-8")
            self.assertEqual(list(audit.JsonArrayStream(path, chunk_size=17)),
                             [{"value": "x" * 300}, {"value": 2}])

    def test_stream_reader_compacts_long_whitespace(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rows.json"
            path.write_text("[" + " " * 10000 + "{}" + " " * 10000 + "]", encoding="utf-8")
            self.assertEqual(list(audit.JsonArrayStream(path, chunk_size=13)), [{}])

    def test_stream_reader_rejects_trailing_comma_primitives_and_oversized_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rows.json"
            for content, limit in (("[{} ,]", 100), ("[42]", 100), ("[{'x':1}]", 100),
                                   ('[{"value":"' + "x" * 80 + '"}]', 40)):
                path.write_text(content, encoding="utf-8")
                with self.subTest(content=content[:20]), self.assertRaises(ValueError):
                    list(audit.JsonArrayStream(path, chunk_size=7, max_item_chars=limit))

    def test_multiple_capture_arrays_are_rejected_as_ambiguous(self):
        with tempfile.TemporaryDirectory() as temp:
            secrets = Path(temp) / "run" / "secrets"
            secrets.mkdir(parents=True)
            row = [{"direction": "received", "application": 4, "channel": 1,
                    "data_base64": "lw=="}]
            (secrets / "one.json").write_text(json.dumps(row), encoding="utf-8")
            (secrets / "two.json").write_text(json.dumps(row), encoding="utf-8")
            with self.assertRaises(ValueError):
                audit._capture_file(secrets.parent)

    def test_explicit_capture_filename_selects_one_of_multiple_arrays(self):
        schema = {"events": [{"name": "ClientToServerMessageReplicateSignalV4"},
                              {"name": "ClientToServerUniverseChatMessageSignalV1"},
                              {"name": "ServerToClientUniverseChatMessageSignalV1"}],
                  "classes": [{"name": "TextSource"}, {"name": "TextChannel"}]}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            secrets = root / "run" / "secrets"
            secrets.mkdir(parents=True)
            schema_row = {"direction": "received", "application": 4, "channel": 1,
                          "data_base64": "lw=="}
            (secrets / "chosen.json").write_text(json.dumps([
                schema_row,
                {"direction": "sent", "application": 4, "channel": 1,
                 "data_base64": "g2dvb2Q="},
            ]), encoding="utf-8")
            (secrets / "other.json").write_text(json.dumps([schema_row]), encoding="utf-8")
            reference = root / "reference.json"
            reference.write_text(json.dumps({"run_path": str(root / "run")}), encoding="utf-8")
            with mock.patch.object(audit, "parse_schema", return_value=schema), \
                    mock.patch.object(audit, "World", FakeWorld):
                report = audit.audit_capture(reference, "chosen.json")
            self.assertEqual(report["capture_rows"], 2)
            self.assertEqual(report["outbound_chat_events"], 1)
            with self.assertRaises(ValueError):
                audit.audit_capture(reference, "../chosen.json")

    def test_decode_failure_preserves_prior_event_and_marks_audit_incomplete(self):
        report = self._audit_packets([b"\x83after-error"])
        self.assertEqual(report["outbound_chat_events"], 1)
        self.assertEqual(report["outgoing_packet_decode_errors"], 1)
        self.assertTrue(report["audit_incomplete"])

    def test_counts_universe_v1_routes_without_exposing_payload_or_identity(self):
        report = self._audit_packets([
            ("sent", b"\x83universeout"),
            ("received", b"\x83universein"),
        ])
        self.assertEqual(report["outbound_chat_events"], 1)
        self.assertEqual(report["inbound_chat_events"], 1)
        self.assertEqual(report["outbound_universe_chat_events"], 1)
        self.assertEqual(report["inbound_universe_chat_events"], 1)
        self.assertEqual(report["outbound_source_channel_class_unknown"], 0)
        encoded = json.dumps(report)
        for private in ("private universe text", "private inbound universe text",
                        "private metadata", "private-id", "private inbound id", "987654"):
            self.assertNotIn(private, encoded)

    def test_outbound_item_four_is_unsupported_and_journal_gap_is_incomplete(self):
        marker = self._audit_packets([b"\x83item4"])
        self.assertEqual(marker["unsupported_outbound_item4"], 1)
        self.assertEqual(marker["outgoing_packet_decode_errors"], 0)
        self.assertTrue(marker["audit_incomplete"])
        self.assertFalse(marker["incoming_audit_incomplete"])
        overflow = self._audit_packets([b"\x83overflow"])
        self.assertEqual(overflow["outbound_chat_events"], 1)
        self.assertTrue(overflow["journal_overflow_detected"])
        self.assertTrue(overflow["audit_incomplete"])
        self.assertFalse(overflow["incoming_audit_incomplete"])

    @staticmethod
    def _audit_packets(packets):
        schema = {"events": [{"name": "ClientToServerMessageReplicateSignalV4"},
                              {"name": "ClientToServerUniverseChatMessageSignalV1"},
                              {"name": "ServerToClientUniverseChatMessageSignalV1"}],
                  "classes": [{"name": "TextSource"}, {"name": "TextChannel"}]}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            secrets = root / "run" / "secrets"
            secrets.mkdir(parents=True)
            rows = [{"direction": "received", "application": 4, "channel": 1,
                     "data_base64": "lw=="}]
            import base64
            for item in packets:
                direction, packet = item if isinstance(item, tuple) else ("sent", item)
                rows.append({"direction": direction, "application": 4, "channel": 1,
                             "data_base64": base64.b64encode(packet).decode("ascii")})
            (secrets / "capture.json").write_text(json.dumps(rows), encoding="utf-8")
            reference = root / "reference.json"
            reference.write_text(json.dumps({"run_path": str(root / "run")}), encoding="utf-8")
            with mock.patch.object(audit, "parse_schema", return_value=schema), \
                    mock.patch.object(audit, "World", FakeWorld):
                return audit.audit_capture(reference)


if __name__ == "__main__":
    unittest.main()
