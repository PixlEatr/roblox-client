import math
import struct
import unittest

from physics_history import (
    CurrentRecord,
    HistoryEntry,
    PhysicsHistoryError,
    encode_history,
    encode_no_history_current,
    native_clock_milliseconds,
)
from physics_packet import TransformHeader
from physics_rotation import RotationField


def current(state=7):
    header = TransformHeader(1, True, True, True, 0xE1)
    # The rotation codecs retain generic type-57 selectors as opaque markers;
    # selector 1 is encoded here as its byte-level marker, without assuming
    # that generic property and native-physics selector semantics match.
    rotation = RotationField(selector=1, kind="selector", value=1)
    return CurrentRecord(header, (0.0, 0.0, 0.0), rotation, None, None,
                         "compact", state=state)


class PhysicsHistoryTests(unittest.TestCase):
    def test_no_history_prefix_and_current_body(self):
        self.assertEqual(encode_no_history_current(current()),
                         b"\x01\x00\x00\x00\x00\xe1\x07")

    def test_empty_regular_history_is_zero_count(self):
        self.assertEqual(encode_history(()), b"\0")

    def test_history_count_delta_and_record(self):
        encoded = encode_history((HistoryEntry(0.25, current(3)),))
        self.assertEqual(encoded, b"\x01" + struct.pack("<f", 0.25) + b"\xe1\x03")

    def test_reference_suffix_is_explicit(self):
        record = CurrentRecord(TransformHeader(1, True, True, True, 0xE1),
                               (0, 0, 0), RotationField(1, "selector", 1),
                               None, None, "compact", reference_bytes=b"\x92\x01")
        self.assertEqual(record.encode(), b"\xe1\x92\x01")

    def test_requires_one_suffix(self):
        with self.assertRaises(PhysicsHistoryError):
            current(state=None).encode()
        with self.assertRaises(PhysicsHistoryError):
            CurrentRecord(TransformHeader(1, True, True, True, 0xE1),
                          (0, 0, 0), RotationField(1, "identity", ()),
                          None, None, "compact", state=1,
                          reference_bytes=b"\x01").encode()

    def test_history_cap_selects_native_ring_prefix(self):
        entries = tuple(HistoryEntry(index * 0.25, current(index))
                        for index in (1, 2, 3))
        self.assertEqual(encode_history(entries, max_send_index=2),
                         b"\x02" + entries[0].encode() + entries[1].encode())
        self.assertEqual(encode_history(entries, max_send_index=2,
                                        force_single_sample=True),
                         b"\x01" + entries[0].encode())

    def test_default_history_cap_selects_first_255_entries(self):
        entries = tuple(HistoryEntry(0, current()) for _ in range(256))
        self.assertEqual(encode_history(entries),
                         b"\xff" + b"".join(entry.encode() for entry in entries[:255]))

    def test_invalid_history_delta_and_cap(self):
        for delta in (-0.01, math.inf, math.nan):
            with self.assertRaises(PhysicsHistoryError):
                HistoryEntry(delta, current()).encode()
        for cap in (0, 1, 256, True, 2.0):
            with self.assertRaises(PhysicsHistoryError):
                encode_history((), max_send_index=cap)
        with self.assertRaises(PhysicsHistoryError):
            encode_history((), force_single_sample=1)

    def test_clock_conversion_uses_milliseconds(self):
        self.assertEqual(native_clock_milliseconds(10.0, 0.125, -0.025), 10100)
        with self.assertRaises(PhysicsHistoryError):
            native_clock_milliseconds(1, math.inf, 0)


if __name__ == "__main__":
    unittest.main()
