import unittest

from physics_children import (
    MotorPoseRecord, encode_motor_pose_block,
    PhysicsChildrenError,
    encode_child_traversal,
    encode_motor_like_block,
    encode_motor_like_record,
    encode_native_varuint,
)
from physics_wire import position


class PhysicsChildrenTests(unittest.TestCase):
    def test_joint_pose_record_uses_quaternion32_and_preserves_explicit_order(self):
        import math
        from quaternion32 import decode_quaternion32
        identity=(1.,0.,0.,0.,1.,0.,0.,0.,1.)
        quarter=(0.,-1.,0.,1.,0.,0.,0.,0.,1.)
        neutral=MotorPoseRecord((0.,0.,0.),identity)
        rotated=MotorPoseRecord((0.,0.,0.),quarter)
        encoded=encode_motor_pose_block([neutral,rotated])
        self.assertEqual(encoded[:3],b'\x02\x00\x02')
        q=decode_quaternion32(encoded,3)
        self.assertAlmostEqual(abs(q[2]),math.sqrt(.5),delta=.003)
        self.assertAlmostEqual(abs(q[3]),math.sqrt(.5),delta=.003)
        self.assertEqual(len(encoded),7)
        with self.assertRaises(PhysicsChildrenError):
            encode_motor_pose_block([MotorPoseRecord((0.,0.,0.),(2.,0.,0.,0.,1.,0.,0.,0.,1.))])

    def test_native_varuint_boundaries(self):
        self.assertEqual(encode_native_varuint(0), b"\x00")
        self.assertEqual(encode_native_varuint(127), b"\x7f")
        self.assertEqual(encode_native_varuint(128), b"\x80\x01")
        self.assertEqual(encode_native_varuint(16384), b"\x80\x80\x01")
        with self.assertRaises(PhysicsChildrenError):
            encode_native_varuint(-1)

    def test_empty_block_is_zero_count_only(self):
        self.assertEqual(encode_motor_like_block([], encode_packed_value=lambda _: b"x"), b"\x00")

    def test_nonempty_block_prefixes_count_then_record(self):
        result = encode_motor_like_block(
            [(0.0, (0.0, 0.0, 0.0))], encode_packed_value=lambda _: self.fail("not used")
        )
        self.assertEqual(result, b"\x01\x00")

    def test_zero_record_omits_both_optional_payloads(self):
        result = encode_motor_like_record(0.0, (0.0, 0.0, 0.0), encode_packed_value=lambda _: self.fail("not used"))
        self.assertEqual(result, b"\x00")

    def test_position_flag_and_payload(self):
        result = encode_motor_like_record(0.0, (1.0, 2.0, 3.0), encode_packed_value=lambda _: self.fail("not used"))
        self.assertEqual(result, b"\x01" + position((1.0, 2.0, 3.0)))

    def test_scalar_is_delegated_to_unmapped_leaf_codec(self):
        calls = []

        def scalar_encoder(value):
            calls.append(value)
            return b"scalar"

        result = encode_motor_like_record(2.0, (0.0, 0.0, 0.0), encode_packed_value=scalar_encoder)
        self.assertEqual(result, b"\x02scalar")
        self.assertEqual(calls, [2.0])

    def test_child_traversal_preserves_preorder_and_received_sibling_order(self):
        hierarchy = {
            "a": ("b", "c"),
            "b": ("d",),
            "c": (),
            "d": (),
            "e": (),
        }
        result = encode_child_traversal(
            ("a", "e"),
            children_of=hierarchy.__getitem__,
            is_eligible=lambda _: True,
            encode_reference=lambda node: f"<{node}>".encode(),
            encode_body=lambda node: f"[{node}]".encode(),
        )
        self.assertEqual(result, b"<a>[a]<b>[b]<d>[d]<c>[c]<e>[e]")
        self.assertEqual(encode_child_traversal(
            (), children_of=hierarchy.__getitem__, is_eligible=lambda _: True,
            encode_reference=lambda node: node.encode(),
            encode_body=lambda node: node.encode(),
        ), b"")

    def test_ineligible_child_skips_its_entire_subtree(self):
        visited = []

        def children_of(node):
            visited.append(node)
            return ("hidden",) if node == "excluded" else ()

        result = encode_child_traversal(
            ("excluded", "included"),
            children_of=children_of,
            is_eligible=lambda node: node == "included",
            encode_reference=lambda node: node.encode(),
            encode_body=lambda _: b"body",
        )
        self.assertEqual(result, b"includedbody")
        self.assertEqual(visited, ["included"])

    def test_child_traversal_rejects_unknown_eligibility_and_cycles(self):
        common = {
            "children_of": lambda _: (),
            "encode_reference": lambda node: node.encode(),
            "encode_body": lambda _: b"body",
        }
        with self.assertRaisesRegex(PhysicsChildrenError, "explicit boolean"):
            encode_child_traversal(("child",), is_eligible=lambda _: None, **common)
        with self.assertRaisesRegex(PhysicsChildrenError, "cycle"):
            encode_child_traversal(
                ("a",), children_of=lambda node: ("b",) if node == "a" else ("a",),
                is_eligible=lambda _: True,
                encode_reference=lambda node: node.encode(), encode_body=lambda _: b"body",
            )

    def test_child_traversal_requires_nonempty_reference_and_body_bytes(self):
        common = {
            "children_of": lambda _: (),
            "is_eligible": lambda _: True,
        }
        for reference, body in ((b"", b"body"), (b"ref", b""), (b"ref", bytearray(b"body"))):
            with self.subTest(reference=reference, body=body):
                with self.assertRaises(PhysicsChildrenError):
                    encode_child_traversal(
                        ("child",), encode_reference=lambda _: reference,
                        encode_body=lambda _: body, **common,
                    )

    def test_nonfinite_inputs_fail_closed(self):
        with self.assertRaises(PhysicsChildrenError):
            encode_motor_like_record(float("nan"), (0, 0, 0), encode_packed_value=lambda _: b"x")
        with self.assertRaises(PhysicsChildrenError):
            encode_motor_like_record(1, (float("inf"), 0, 0), encode_packed_value=lambda _: b"x")


if __name__ == "__main__":
    unittest.main()
