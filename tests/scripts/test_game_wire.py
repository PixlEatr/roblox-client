import base64
import json
import unittest

from game_wire import (FrameDecoder, encode_varint, frame, issued_early_auth,
                       feature_configuration, encode_unsigned, encode_string, protocol_sync,
                       player_join_info)


class FramingTests(unittest.TestCase):
    def test_rfc9000_vectors(self):
        # RFC 9000 section 16 examples, independently of the implementation.
        for raw, value in (("25", 37), ("7bbd", 15293), ("9d7f3e7d", 494878333),
                           ("c2197c5eff14e88c", 151288809941952652)):
            self.assertEqual(encode_varint(value).hex(), raw)

    def test_observed_control_frame_all_delivery_splits(self):
        wire = bytes.fromhex("800000020000")
        self.assertEqual(frame(b"\0\0"), wire)
        for split in range(len(wire) + 1):
            decoder = FrameDecoder()
            self.assertEqual(decoder.feed(wire[:split]) + decoder.feed(wire[split:]), [b"\0\0"])
            decoder.finish()

    def test_coalesced_frames_and_nonminimal_lengths(self):
        decoder = FrameDecoder()
        self.assertEqual(decoder.feed(bytes.fromhex("000178400261628000000163")), [b"", b"x", b"ab", b"c"])
        decoder.finish()

    def test_oversize_and_truncated_peer_frames(self):
        with self.assertRaises(ValueError):
            FrameDecoder(max_frame=16).feed(bytes.fromhex("80000011"))
        for data in (bytes.fromhex("80"), bytes.fromhex("8000000261")):
            decoder = FrameDecoder()
            decoder.feed(data)
            with self.assertRaises(ValueError):
                decoder.finish()

    def test_ticket_fields_are_passed_through(self):
        first, second = bytes(range(33)), bytes(range(66))
        ticket = ";".join(("synthetic", "unused-signature", base64.b64encode(first).decode(),
                           base64.b64encode(second).decode(), "17"))
        self.assertEqual(issued_early_auth(ticket), b"\xa8\x11\x21" + first + b"\x42" + second)
        with self.assertRaises(ValueError):
            issued_early_auth("not-a-ticket")

    def test_configuration_pair_and_truncation(self):
        data = b"\x93\0\x01\0\x07\0Example\x04\0true"
        self.assertEqual(feature_configuration(data), {"Example": "true"})
        for length in range(len(data)):
            with self.assertRaises(ValueError):
                feature_configuration(data[:length])
        with self.assertRaises(ValueError):
            feature_configuration(data + b"\0")

    def test_network_integer_and_string_vectors(self):
        self.assertEqual(encode_unsigned(127), b"\x7f")
        self.assertEqual(encode_unsigned(128), b"\x80\x01")
        self.assertEqual(encode_unsigned(624485), bytes.fromhex("e58e26"))
        self.assertEqual(encode_string("é"), b"\x02\xc3\xa9")

    def test_protocol_sync_layout_and_issued_identity(self):
        raw = protocol_sync({"UserId": 123, "UserName": "synthetic", "SuperSafeChat": True})
        self.assertEqual(raw[:8], bytes.fromhex("9001240000000000"))
        self.assertEqual(raw[-24:].hex(), "5e1fcb85987b90bf9ea7b18a5e1fcb851660491178feefcb")
        self.assertIn(b'"UserId":123', raw)
        self.assertIn(b'"SuperSafeChat":true', raw)
        ranged = protocol_sync({"UserId": 123}, luau_range=(13, 14))
        self.assertEqual(ranged[:10], bytes.fromhex("9001240000000d0e0000"))
        with self.assertRaises(ValueError):
            protocol_sync({}, luau_range=(13, 100))


    def test_native_platform_pair_in_protocol_sync(self):
        # Independent literals in copied native 102c23b80 / 102c23b90 assembly.
        info = player_join_info({"UserId": 123, "UserName": "synthetic"})
        self.assertEqual(info["OsPlatform"], "OSX")
        self.assertEqual(info["OsPlatformName"], "MacOS")
        raw = protocol_sync({"UserId": 123, "UserName": "synthetic"}, luau_range=(13, 14))
        offset, count, shift = 10, 0, 0
        while True:
            byte = raw[offset]
            offset += 1
            count |= (byte & 127) << shift
            if byte < 128: break
            shift += 7
        encoded = json.loads(raw[offset:offset + count])
        self.assertEqual((encoded["OsPlatform"], encoded["OsPlatformName"]), ("OSX", "MacOS"))


    def test_direct_join_context_defaults_in_protocol_json(self):
        issued = {"UserId": 123, "UserName": "synthetic", "AgeChecked": 7,
                  "APIsecurityToken": "opaque-synthetic"}
        ticket = {"synthetic": "unchanged"}
        for supplied in ({}, {"RawJoinData": "supplied-raw",
                              "ClientJoinData": "supplied-client",
                              "GameJoinContext": "supplied-game"}):
            allocation = dict(issued, **supplied)
            before = json.dumps(allocation, sort_keys=True)
            raw = protocol_sync(allocation, join_ticket=ticket, luau_range=(13, 14))
            self.assertEqual(raw[:10], bytes.fromhex("9001240000000d0e0000"))
            offset, count, shift = 10, 0, 0
            while True:
                byte = raw[offset]
                offset += 1
                count |= (byte & 127) << shift
                if byte < 128: break
                shift += 7
            self.assertEqual(offset + count + 24, len(raw))
            encoded = json.loads(raw[offset:offset + count])
            self.assertEqual({field: encoded[field] for field in
                              ("RawJoinData", "ClientJoinData", "GameJoinContext")},
                             supplied or {"RawJoinData": "", "ClientJoinData": "", "GameJoinContext": ""})
            self.assertEqual(encoded["AgeChecked"], "7")
            self.assertEqual(encoded["APIsecurityToken"], issued["APIsecurityToken"])
            self.assertEqual(json.loads(encoded["__joinTicket"]), ticket)
            self.assertEqual(json.dumps(allocation, sort_keys=True), before)


if __name__ == "__main__":
    unittest.main()
