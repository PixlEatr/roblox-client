"""Offline codec for the verified subset of the native transform component.

This contains the transform subset and message-level header/terminator framing.
It does not derive runtime records, select transport routing, or send. The caller
must explicitly select the compact or short velocity branch to match the outer
record flags; full runtime record production remains outside this codec.
"""
from dataclasses import dataclass
import struct

from physics_rotation import (
    RotationField, RotationCodecError, decode_native_quaternion, encode_native_quaternion,
)
from physics_header import encode_header as encode_physics_header
from physics_velocity import (
    DecodedVelocity,
    decode as decode_velocity,
    decode_native_short_velocity,
    encode_native_short_velocity,
)
from physics_wire import position as encode_position, velocity as encode_velocity
from wire_reader import Reader, WireError
from world_replication import World


class PhysicsPacketError(ValueError):
    """Invalid or unsupported physics transform header."""


@dataclass(frozen=True)
class TransformHeader:
    """Lossless interpretation of the verified flags byte.

    `rotation_selector` is retained as a numeric selector only. Its mapping to
    rotation forms is not inferred here. Bit 7 marks zero position; bits 6 and
    5 suppress the linear and angular velocity fields, respectively.
    """

    rotation_selector: int
    position_is_zero: bool
    linear_velocity_omitted: bool
    angular_velocity_omitted: bool
    raw: int


@dataclass(frozen=True)
class TransformComponent:
    """Decoded transform subset; this does not represent a whole packet."""

    header: TransformHeader
    position: tuple[float, float, float]
    rotation: RotationField
    linear_velocity: DecodedVelocity | None
    angular_velocity: DecodedVelocity | None
    velocity_mode: str
    consumed: int


def encode_physics_message(timestamp_ms: int, sequence: int, root_records, *, layout: str) -> bytes:
    """Compose the statically mapped message framing around encoded root records.

    The output is the 18-byte native header, caller-supplied root record bytes,
    then the native null-reference terminator. ``timestamp_ms`` must already
    be in the adjusted monotonic clock domain expected by the native writer;
    this helper does not infer the Workspace adjustment or choose layout. This
    is message framing only and does not select a transport route or send.
    """
    try:
        header = encode_physics_header(timestamp_ms, sequence, layout=layout)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PhysicsPacketError("invalid native physics header inputs") from exc
    try:
        records = tuple(root_records)
    except TypeError as exc:
        raise PhysicsPacketError("root_records must be an iterable of encoded bytes") from exc
    if any(not isinstance(record, bytes) or not record for record in records):
        raise PhysicsPacketError("each root record must be non-empty encoded bytes")
    # C650 appends a null instance reference after all top-level records. The
    # native reference writer encodes a null reference as ULEB128 zero.
    return header + b"".join(records) + b"\x00"


def decode_transform_header(data: bytes, offset: int = 0) -> tuple[TransformHeader, int]:
    """Decode exactly the transform flags byte at `offset`; return bytes used."""
    if not isinstance(data, bytes):
        raise PhysicsPacketError("transform header input must be bytes")
    if type(offset) is not int or offset < 0 or offset >= len(data):
        raise PhysicsPacketError("truncated transform header")
    raw = data[offset]
    return TransformHeader(
        rotation_selector=raw & 0x1F,
        position_is_zero=bool(raw & 0x80),
        linear_velocity_omitted=bool(raw & 0x40),
        angular_velocity_omitted=bool(raw & 0x20),
        raw=raw,
    ), 1


def encode_transform_header(
    *,
    rotation_selector: int,
    position_is_zero: bool,
    linear_velocity_omitted: bool,
    angular_velocity_omitted: bool,
) -> bytes:
    """Encode a verified transform flags byte; payloads are not included."""
    if type(rotation_selector) is not int or not 0 <= rotation_selector <= 31:
        raise PhysicsPacketError("rotation selector must fit in five bits")
    for name, value in (
        ("position_is_zero", position_is_zero),
        ("linear_velocity_omitted", linear_velocity_omitted),
        ("angular_velocity_omitted", angular_velocity_omitted),
    ):
        if type(value) is not bool:
            raise PhysicsPacketError(f"{name} must be an explicit boolean")
    raw = rotation_selector
    if position_is_zero:
        raw |= 0x80
    if linear_velocity_omitted:
        raw |= 0x40
    if angular_velocity_omitted:
        raw |= 0x20
    return bytes((raw,))


def _check_velocity_mode(velocity_mode: str) -> None:
    if velocity_mode not in ("compact", "short"):
        raise PhysicsPacketError("velocity mode must be explicitly 'compact' or 'short'")


def _supported_rotation(field: RotationField, expected_selector: int) -> None:
    if not isinstance(field, RotationField) or field.selector != expected_selector:
        raise PhysicsPacketError("rotation field must match the transform header selector")
    # Native selectors 1..24 identify the table-coded axis-pair path. Selector
    # 0 and 25..31 use the compact six-byte quaternion helper.
    if 1 <= expected_selector <= 24:
        if field.kind != "selector" or field.value != expected_selector:
            raise PhysicsPacketError("selectors 1..24 require their native selector marker")
        return
    if field.kind != "native-quaternion":
        raise PhysicsPacketError("fallback selectors require native-order quaternion components")
    try:
        encode_native_quaternion(field.value)
    except (TypeError, ValueError, OverflowError, RotationCodecError) as exc:
        raise PhysicsPacketError("invalid native quaternion") from exc


def decode_transform_component(data: bytes, *, velocity_mode: str) -> TransformComponent:
    """Decode a bounded transform component in the explicitly chosen mode.

    The header and body order are statically mapped in 0x104D4EF84/0x104D4F2CC/
    0x104D4F45C. Select ``velocity_mode="compact"`` for the five-byte vector
    codec or ``velocity_mode="short"`` for the three-byte native vector codec.
    D76C chooses this mode at runtime; this utility requires an explicit choice.
    This remains a transform component, not proof of root-record framing or
    transport acceptance.
    """
    _check_velocity_mode(velocity_mode)
    header, _ = decode_transform_header(data)
    reader = Reader(data[1:])
    if header.position_is_zero:
        position_value = (0.0, 0.0, 0.0)
    else:
        position_value = tuple(World.compact_position(reader))

    # The physics writer's 1..24 selector path carries no quaternion payload.
    # Selector 0 and 25..31 use the native six-byte compact quaternion helper;
    # do not substitute type-57's unrelated seven-float receive variant.
    selector = header.rotation_selector
    if 1 <= selector <= 24:
        rotation = RotationField(selector, "selector", selector)
    else:
        try:
            quaternion, used = decode_native_quaternion(reader.data, reader.pos)
        except (ValueError, struct.error, RotationCodecError) as exc:
            raise PhysicsPacketError("invalid or truncated native quaternion") from exc
        reader.take(used)
        rotation = RotationField(selector, "native-quaternion", quaternion)

    def read_velocity(omitted: bool) -> DecodedVelocity | None:
        if omitted:
            return None
        try:
            tag = reader.u8()
            if tag == 0:
                raw = b"\0"
            elif velocity_mode == "short":
                raw = bytes((tag,)) + reader.take(2)
            else:
                raw = bytes((tag,)) + reader.take(4)
            if velocity_mode == "short":
                return decode_native_short_velocity(raw)
            return decode_velocity(raw)
        except (ValueError, WireError, struct.error) as exc:
            raise PhysicsPacketError("invalid compact velocity payload") from exc

    linear_velocity = read_velocity(header.linear_velocity_omitted)
    angular_velocity = read_velocity(header.angular_velocity_omitted)
    reader.finish()
    return TransformComponent(
        header, position_value, rotation, linear_velocity, angular_velocity,
        velocity_mode, 1 + reader.pos,
    )


def encode_transform_component(
    *,
    header: TransformHeader,
    position_value,
    rotation: RotationField,
    linear_velocity,
    angular_velocity,
    velocity_mode: str,
) -> bytes:
    """Encode only the mapped transform subset; not an outer physics packet."""
    _check_velocity_mode(velocity_mode)
    if not isinstance(header, TransformHeader) or header.raw != (
        (header.rotation_selector & 0x1F)
        | (0x80 if header.position_is_zero else 0)
        | (0x40 if header.linear_velocity_omitted else 0)
        | (0x20 if header.angular_velocity_omitted else 0)
    ):
        raise PhysicsPacketError("transform header fields are inconsistent")
    _supported_rotation(rotation, header.rotation_selector)
    out = bytearray((header.raw,))
    if header.position_is_zero:
        if position_value not in (None, (0, 0, 0), [0, 0, 0]):
            raise PhysicsPacketError("zero-position flag requires an omitted or zero position")
    else:
        if position_value is None:
            raise PhysicsPacketError("nonzero-position header requires a position vector")
        try:
            out.extend(encode_position(position_value))
        except (TypeError, ValueError, OverflowError) as exc:
            raise PhysicsPacketError("invalid position vector") from exc

    if 1 <= header.rotation_selector <= 24:
        # The one-byte flags field already carries this native table selector.
        pass
    else:
        try:
            out.extend(encode_native_quaternion(rotation.value))
        except (TypeError, ValueError, OverflowError, RotationCodecError) as exc:
            raise PhysicsPacketError("invalid native quaternion") from exc

    def write_velocity(omitted: bool, vector, label: str) -> None:
        if omitted:
            if vector is not None:
                raise PhysicsPacketError(f"{label} must be omitted when its header bit is set")
            return
        if vector is None:
            raise PhysicsPacketError(f"{label} vector is required when its header bit is clear")
        try:
            if velocity_mode == "short":
                out.extend(encode_native_short_velocity(vector))
            else:
                out.extend(encode_velocity(vector))
        except (TypeError, ValueError, OverflowError) as exc:
            raise PhysicsPacketError(f"invalid {label} vector") from exc

    write_velocity(header.linear_velocity_omitted, linear_velocity, "linear velocity")
    write_velocity(header.angular_velocity_omitted, angular_velocity, "angular velocity")
    return bytes(out)
