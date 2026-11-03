"""Native local joint-transform records and eligible child traversal.

104CBEF68 is the four-byte quaternion leaf, after Rodrigues conversion of the
source axis/angle. This module does not choose native skeleton source order.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Iterable

from physics_wire import position as encode_position


class PhysicsChildrenError(ValueError):
    """Invalid input to the verified child-block subset."""


@dataclass(frozen=True)
class MotorPoseRecord:
    """One explicit joint-local CFrame, in caller-provided native source order."""
    position: tuple
    rotation: tuple

    def encode(self):
        from physics_rotation import native_matrix3x3_to_quaternion
        from quaternion32 import encode_quaternion32
        if (not isinstance(self.rotation,(list,tuple)) or len(self.rotation)!=9
                or any(type(v) not in (int,float) or not math.isfinite(v) for v in self.rotation)):
            raise PhysicsChildrenError('Motor rotation must be a finite matrix')
        r=self.rotation
        for a in range(3):
            for b in range(3):
                if abs(sum(r[a*3+k]*r[b*3+k] for k in range(3))-(1. if a==b else 0.))>2e-3:
                    raise PhysicsChildrenError('Motor rotation must be rigid')
        det=r[0]*(r[4]*r[8]-r[5]*r[7])-r[1]*(r[3]*r[8]-r[5]*r[6])+r[2]*(r[3]*r[7]-r[4]*r[6])
        if abs(det-1.)>2e-3:
            raise PhysicsChildrenError('Motor rotation must preserve handedness')
        q=native_matrix3x3_to_quaternion(r)
        norm=math.sqrt(sum(v*v for v in q))
        angle=2*math.acos(min(1.,abs(q[3])/norm))
        return encode_motor_like_record(angle,self.position,
                    encode_packed_value=lambda _:encode_quaternion32(q))


def encode_motor_pose_block(records):
    """Encode explicit ordered local poses; caller establishes joint identity."""
    if not isinstance(records,(tuple,list)) or len(records)>256:
        raise PhysicsChildrenError('Motor source must be a bounded ordered sequence')
    if any(not isinstance(record,MotorPoseRecord) for record in records):
        raise PhysicsChildrenError('Motor source contains an unsupported record')
    return encode_native_varuint(len(records))+b''.join(record.encode() for record in records)


def encode_native_varuint(value: int) -> bytes:
    """Encode the unsigned 7-bit continuation format used by 1015F9530."""
    if type(value) is not int or value < 0 or value >= 1 << 64:
        raise PhysicsChildrenError("count must be an unsigned 64-bit integer")
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _near_zero(value: float) -> bool:
    """Match the native float test: abs(x) <= (abs(x)+1)*1e-5."""
    value = float(value)
    if not math.isfinite(value):
        raise PhysicsChildrenError("record values must be finite")
    return abs(value) <= (abs(value) + 1.0) * 0.00001


def encode_motor_like_record(
    packed_value: float,
    position: Iterable[float],
    *,
    encode_packed_value: Callable[[float], bytes],
) -> bytes:
    """Encode the known fields of one 28-byte source record.

    Native record offset +12 is the scalar; +16..+24 is a three-float vector.
    Source fields +0..+8 are the axis used to construct the rotation. The
    callback supplies the resulting 104CBEF68 four-byte quaternion.
    """
    try:
        scalar = float(packed_value)
        vector = tuple(float(component) for component in position)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PhysicsChildrenError("record needs one scalar and a 3D position") from exc
    if len(vector) != 3 or not math.isfinite(scalar) or any(not math.isfinite(v) for v in vector):
        raise PhysicsChildrenError("record needs finite values and a 3D position")

    magnitude_squared = sum(component * component for component in vector)
    position_present = not _near_zero(magnitude_squared)
    scalar_present = not _near_zero(scalar)
    flags = (1 if position_present else 0) | (2 if scalar_present else 0)
    out = bytearray((flags,))
    if position_present:
        try:
            out.extend(encode_position(vector))
        except (TypeError, ValueError, OverflowError) as exc:
            raise PhysicsChildrenError("invalid compact position") from exc
    if scalar_present:
        try:
            encoded = encode_packed_value(scalar)
        except Exception as exc:
            raise PhysicsChildrenError("packed scalar encoder failed") from exc
        if not isinstance(encoded, bytes) or not encoded:
            raise PhysicsChildrenError("packed scalar encoder must return non-empty bytes")
        out.extend(encoded)
    return bytes(out)


def encode_motor_like_block(
    records: Iterable[tuple[float, Iterable[float]]],
    *,
    encode_packed_value: Callable[[float], bytes],
) -> bytes:
    """Encode count then records, matching DE2C's proven sequence."""
    records = tuple(records)
    try:
        prefix = encode_native_varuint(len(records))
    except PhysicsChildrenError as exc:
        raise PhysicsChildrenError("record count is not encodable") from exc
    return prefix + b"".join(
        encode_motor_like_record(value, vector, encode_packed_value=encode_packed_value)
        for value, vector in records
    )


def encode_child_traversal(
    children: Iterable[object],
    *,
    children_of: Callable[[object], Iterable[object]],
    is_eligible: Callable[[object], bool],
    encode_reference: Callable[[object], bytes],
    encode_body: Callable[[object], bytes],
) -> bytes:
    """Encode DCEC's eligible-child preorder without inventing hierarchy state.

    Each eligible child contributes its reference, body, and then descendants,
    retaining the supplied sibling order. Ineligible subtrees are skipped. The
    root itself, child counts, and D76C's final null reference are not emitted.
    """
    out = bytearray()
    active = set()

    def append_children(source: Iterable[object]) -> None:
        try:
            ordered_children = tuple(source)
        except Exception as exc:
            raise PhysicsChildrenError("child hierarchy must be iterable") from exc
        for child in ordered_children:
            try:
                eligible = is_eligible(child)
            except Exception as exc:
                raise PhysicsChildrenError("child eligibility check failed") from exc
            if type(eligible) is not bool:
                raise PhysicsChildrenError("child eligibility must be an explicit boolean")
            if not eligible:
                continue
            identity = id(child)
            if identity in active:
                raise PhysicsChildrenError("child hierarchy contains a cycle")
            active.add(identity)
            try:
                for label, encoder in (
                    ("reference", encode_reference),
                    ("body", encode_body),
                ):
                    try:
                        encoded = encoder(child)
                    except Exception as exc:
                        raise PhysicsChildrenError(f"child {label} encoder failed") from exc
                    if not isinstance(encoded, bytes) or not encoded:
                        raise PhysicsChildrenError(
                            f"child {label} encoder must return non-empty bytes"
                        )
                    out.extend(encoded)
                try:
                    descendants = children_of(child)
                except Exception as exc:
                    raise PhysicsChildrenError("could not read child descendants") from exc
                append_children(descendants)
            finally:
                active.remove(identity)

    append_children(children)
    return bytes(out)
