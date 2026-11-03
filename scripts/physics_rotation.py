"""Structural codecs and verified native conversion for rotation fields.

The type-27/51/57 parsers preserve six-byte payloads as opaque data. Separate
helpers model the verified native quaternion codec and matrix conversion.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass


class RotationCodecError(ValueError):
    pass


@dataclass(frozen=True)
class RotationField:
    selector: int
    kind: str
    value: object = None


# Receive-side compact orientation selectors, read from the copied 0.745
# Mach-O at 0x105EAA3E3. Index zero is reserved; selectors 1..24 map to the
# packed axis-pair codes consumed by 0x102E8F40C.
_COMPACT_SELECTOR_TO_AXIS_PAIR = (
    0, 1, 2, 4, 5, 6, 8, 9, 11, 12, 13, 15, 16,
    19, 20, 22, 23, 24, 26, 27, 29, 30, 31, 33, 34,
)

# Native outbound helper 0x104CBEC4C keeps the three components other than
# the largest-magnitude quaternion component in this order. The row is
# indexed by the omitted component (0..3); these are literal indices from
# 0x105EA7CA0 in the pinned ARM64 image.
_NATIVE_QUATERNION_COMPONENTS = (
    (1, 2, 3),
    (0, 2, 3),
    (0, 1, 3),
    (0, 1, 2),
)
_NATIVE_QUATERNION_SCALE = 16383.0
_NATIVE_QUATERNION_RANGE = 0.7071067690849304  # float at 0x1059B7000


def _f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def native_matrix3x3_to_quaternion(matrix: tuple[float, ...]) -> tuple[float, float, float, float]:
    """Convert a row-major 3x3 rotation matrix using native helper 0x102E865A4.

    The native helper reads nine consecutive float32 values and writes
    ``(x, y, z, w)``. This reproduces its trace/dominant-diagonal branches,
    including its signed dominant component. Inputs are required to be finite
    and float32-representable; callers are responsible for supplying a rotation
    matrix (this routine deliberately does not orthogonalize it).
    """
    if not isinstance(matrix, (tuple, list)) or len(matrix) != 9:
        raise RotationCodecError("native matrix conversion requires nine row-major values")
    try:
        m = tuple(_f32(float(v)) for v in matrix)
    except (TypeError, ValueError, OverflowError, struct.error) as exc:
        raise RotationCodecError("matrix values must be finite float32 numbers") from exc
    if any(not math.isfinite(v) for v in m):
        raise RotationCodecError("matrix values must be finite float32 numbers")

    m00, m01, m02, m10, m11, m12, m20, m21, m22 = m
    trace = _f32(_f32(m00 + m11) + m22)
    q = [0.0, 0.0, 0.0, 0.0]
    if trace > 0.0:
        root = _f32(math.sqrt(_f32(trace + 1.0)))
        half = _f32(0.5)
        factor = _f32(half / root)
        q[3] = _f32(root * half)
        q[0] = _f32(factor * _f32(m21 - m12))
        q[1] = _f32(factor * _f32(m02 - m20))
        q[2] = _f32(factor * _f32(m10 - m01))
        return tuple(q)

    # Native compares m00, m11, m22 with strict greater-than branches. In
    # each diagonal branch it stores the dominant component as negative.
    if m00 > m11 and m00 > m22:
        root = _f32(math.sqrt(_f32(_f32(_f32(1.0 + m00) - m11) - m22)))
        factor = _f32(_f32(-0.5) / root)
        q[0] = _f32(root * _f32(-0.5))
        q[1] = _f32(factor * _f32(m01 + m10))
        q[2] = _f32(factor * _f32(m02 + m20))
        q[3] = _f32(factor * _f32(m21 - m12))
    elif m11 > m22:
        root = _f32(math.sqrt(_f32(_f32(_f32(1.0 + m11) - m00) - m22)))
        factor = _f32(_f32(-0.5) / root)
        q[1] = _f32(root * _f32(-0.5))
        q[0] = _f32(factor * _f32(m01 + m10))
        q[2] = _f32(factor * _f32(m12 + m21))
        q[3] = _f32(factor * _f32(m02 - m20))
    else:
        root = _f32(math.sqrt(_f32(_f32(_f32(1.0 + m22) - m00) - m11)))
        factor = _f32(_f32(-0.5) / root)
        q[2] = _f32(root * _f32(-0.5))
        q[0] = _f32(factor * _f32(m02 + m20))
        q[1] = _f32(factor * _f32(m12 + m21))
        q[3] = _f32(factor * _f32(m10 - m01))
    return tuple(q)


_NATIVE_AXES = (
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
    (-1.0, 0.0, 0.0),
    (0.0, -1.0, 0.0),
    (0.0, 0.0, -1.0),
)


def native_compact_selector_to_matrix(selector: int) -> tuple[float, ...]:
    """Expand a native physics selector 1..24 to its row-major basis.

    The selector table at 0x105EAA3E3 feeds axis-pair expander 0x102E8F40C.
    Its two axes come from the initialized table at 0x106DF42F0, and its third
    axis is their cross product. This mapping is specific to native physics;
    it does not assign meanings to generic property selectors.
    """
    if type(selector) is not int or not 1 <= selector <= 24:
        raise RotationCodecError("native compact rotation selector must be in 1..24")
    code = _COMPACT_SELECTOR_TO_AXIS_PAIR[selector]
    first = _NATIVE_AXES[code // 6]
    second = _NATIVE_AXES[code % 6]
    cross = (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )
    columns = (first, second, cross)
    return tuple(columns[column][row] for row in range(3) for column in range(3))


def native_property_selector_to_matrix(selector: int) -> tuple[float, ...]:
    """Expand a property CFrame selector (type 27) to its row-major basis.

    The native property reader at 0x104F233B0 passes ``selector - 1`` to the
    six-axis pair expander (0x102E8F40C). This numbering is distinct from the
    compact physics selector table used by ``native_compact_selector_to_matrix``.
    Only orthogonal axis pairs produce a valid rotation basis.
    """
    if type(selector) is not int or not 1 <= selector <= 36:
        raise RotationCodecError("native property rotation selector must be in 1..36")
    code = selector - 1
    first = _NATIVE_AXES[code // 6]
    second = _NATIVE_AXES[code % 6]
    if sum(first[i] * second[i] for i in range(3)) != 0:
        raise RotationCodecError("native property selector does not identify orthogonal axes")
    cross = (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )
    columns = (first, second, cross)
    return tuple(columns[column][row] for row in range(3) for column in range(3))


def native_compact_selector_to_quaternion(selector: int) -> tuple[float, float, float, float]:
    """Convert a compact native physics selector through the verified basis path."""
    return native_matrix3x3_to_quaternion(native_compact_selector_to_matrix(selector))


def _sign_extend_15(value: int) -> int:
    value &= 0x7fff
    return value - 0x8000 if value & 0x4000 else value


def native_yaw_rotation(yaw: float) -> RotationField:
    """Represent a modeled Roblox yaw with the native quaternion wire branch.

    Construct a row-major Y rotation directly from the controller's radians;
    this does not read or infer the layout of a native runtime CFrame object.
    Selector zero carries the six-byte quaternion even for cardinal directions.
    """
    if (isinstance(yaw, bool) or not isinstance(yaw, (int, float))
            or not math.isfinite(yaw)):
        raise RotationCodecError('modeled yaw must be a finite number')
    cosine, sine = math.cos(yaw), math.sin(yaw)
    matrix = (cosine, 0.0, sine, 0.0, 1.0, 0.0, -sine, 0.0, cosine)
    return RotationField(0, 'native-quaternion', native_matrix3x3_to_quaternion(matrix))


def encode_native_quaternion(components: tuple[float, float, float, float]) -> bytes:
    """Encode the native physics helper's quaternion as its six-byte form.

    `components` are in the native four-vector order accepted by the helper.
    The disassembly establishes the six-byte layout and largest-component
    scheme; the upstream CFrame conversion's user-facing component convention
    is outside this function.
    """
    if not isinstance(components, (tuple, list)) or len(components) != 4:
        raise RotationCodecError("native quaternion requires four components")
    if any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in components):
        raise RotationCodecError("native quaternion components must be finite numbers")
    values = tuple(_f32(float(x)) for x in components)
    norm2 = _f32(_f32(_f32(values[0]*values[0] + values[1]*values[1]) +
                       _f32(values[2]*values[2])) + _f32(values[3]*values[3]))
    if not math.isfinite(norm2) or norm2 <= 0.0:
        raise RotationCodecError("native quaternion must have nonzero length")
    norm = _f32(math.sqrt(norm2))
    values = tuple(_f32(x / norm) for x in values)
    # The native routine normalizes again after extracting the CFrame rotation.
    norm2 = _f32(_f32(_f32(values[0]*values[0] + values[1]*values[1]) +
                       _f32(values[2]*values[2])) + _f32(values[3]*values[3]))
    norm = _f32(math.sqrt(norm2))
    values = tuple(_f32(x / norm) for x in values)
    omitted = max(range(4), key=lambda i: abs(values[i]))  # first index wins ties
    if values[omitted] < 0.0:
        values = tuple(_f32(-x) for x in values)
    packed = []
    for index in _NATIVE_QUATERNION_COMPONENTS[omitted]:
        ratio = _f32(values[index] / _NATIVE_QUATERNION_RANGE)
        scaled = _f32(ratio * _NATIVE_QUATERNION_SCALE)
        rounded = math.trunc(_f32(scaled + (0.5 if scaled >= 0.0 else -0.5)))
        packed.append(rounded & 0x7fff)
    first_word = packed[0]
    second_word = (omitted << 30) | (packed[1] << 15) | packed[2]
    return struct.pack("<HI", first_word, second_word)


def decode_native_quaternion(data: bytes, offset: int = 0) -> tuple[tuple[float, float, float, float], int]:
    """Decode the native six-byte largest-component quaternion form."""
    raw = _need(data, 6, offset)
    first_word, second_word = struct.unpack("<HI", raw)
    omitted = second_word >> 30
    packed = (first_word & 0x7fff, (second_word >> 15) & 0x7fff,
              second_word & 0x7fff)
    values = [0.0] * 4
    for index, signed in zip(_NATIVE_QUATERNION_COMPONENTS[omitted], packed):
        scaled = _f32(_sign_extend_15(signed) / _NATIVE_QUATERNION_SCALE)
        values[index] = _f32(scaled * _NATIVE_QUATERNION_RANGE)
    values[omitted] = _f32(math.sqrt(max(1.0 - sum(x*x for x in values), 0.0)))
    norm = math.sqrt(sum(x*x for x in values))
    if not math.isfinite(norm) or norm <= 0.0:
        raise RotationCodecError("invalid native quaternion payload")
    return tuple(_f32(x / norm) for x in values), 6


def _need(data: bytes, count: int, offset: int = 0) -> bytes:
    if offset < 0 or count < 0 or offset + count > len(data):
        raise RotationCodecError("truncated rotation field")
    return data[offset:offset + count]


def decode_type27(data: bytes, offset: int = 0) -> tuple[RotationField, int]:
    """Decode type-27's selector byte and optional six-byte opaque payload."""
    selector = _need(data, 1, offset)[0]
    if selector == 0:
        return RotationField(selector, "packed-opaque", _need(data, 6, offset + 1)), 7
    return RotationField(selector, "selector", None), 1


def encode_type27(field: RotationField) -> bytes:
    if not 0 <= field.selector <= 255:
        raise RotationCodecError("type-27 selector must fit in one byte")
    if field.selector == 0:
        if field.kind != "packed-opaque" or not isinstance(field.value, bytes) or len(field.value) != 6:
            raise RotationCodecError("selector zero requires exactly six opaque bytes")
        return bytes((0,)) + field.value
    if field.kind != "selector" or field.value is not None:
        raise RotationCodecError("nonzero type-27 selectors are opaque selector forms")
    return bytes((field.selector,))


def decode_type51(data: bytes, offset: int = 0) -> tuple[RotationField | None, int]:
    """Decode the type-51 optional rotation field (high bit is presence)."""
    flag = _need(data, 1, offset)[0]
    if not flag & 0x80:
        return None, 1
    if flag == 0x80:
        return RotationField(flag, "packed-opaque", _need(data, 6, offset + 1)), 7
    return RotationField(flag, "selector", flag & 0x7f), 1


def encode_type51(field: RotationField | None) -> bytes:
    if field is None:
        return b"\0"
    if not 0 <= field.selector <= 255:
        raise RotationCodecError("type-51 flag must fit in one byte")
    if not field.selector & 0x80:
        raise RotationCodecError("type-51 rotation flag must set presence bit")
    if field.selector == 0x80:
        if field.kind != "packed-opaque" or not isinstance(field.value, bytes) or len(field.value) != 6:
            raise RotationCodecError("type-51 flag 0x80 requires six opaque bytes")
        return bytes((field.selector,)) + field.value
    if field.kind != "selector" or field.value != (field.selector & 0x7f):
        raise RotationCodecError("non-0x80 type-51 flags retain their low-seven-bit selector")
    return bytes((field.selector,))


def decode_type57(data: bytes, offset: int = 0) -> tuple[RotationField, int]:
    """Decode type-57 rotation using the low five bits of its flags byte.

    Selector 31 carries seven little-endian float32 values, selector 0 carries
    six opaque bytes, and selectors 1..30 are retained as markers. This is a
    byte-level description only; no rotation math is inferred.
    """
    flags = _need(data, 1, offset)[0]
    selector = flags & 0x1f
    if selector == 31:
        raw = _need(data, 28, offset + 1)
        return RotationField(selector, "float32x7", struct.unpack("<7f", raw)), 29
    if selector == 0:
        return RotationField(selector, "packed-opaque", _need(data, 6, offset + 1)), 7
    return RotationField(selector, "selector", selector), 1


def encode_type57(field: RotationField, flags: int = 0) -> bytes:
    if not 0 <= field.selector <= 31:
        raise RotationCodecError("type-57 selector must fit in five bits")
    if not 0 <= flags <= 255 or (flags & 0x1f) not in (0, field.selector):
        raise RotationCodecError("type-57 flags must preserve the selected low-five-bit rotation")
    header = (flags & 0xe0) | field.selector
    if field.selector == 31:
        if field.kind != "float32x7" or not isinstance(field.value, (tuple, list)) or len(field.value) != 7:
            raise RotationCodecError("selector 31 requires seven float32 values")
        try:
            return bytes((header,)) + struct.pack("<7f", *field.value)
        except (OverflowError, struct.error) as exc:
            raise RotationCodecError("invalid type-57 float32 rotation") from exc
    if field.selector == 0:
        if field.kind != "packed-opaque" or not isinstance(field.value, bytes) or len(field.value) != 6:
            raise RotationCodecError("selector zero requires exactly six opaque bytes")
        return bytes((header,)) + field.value
    if field.kind != "selector" or field.value != field.selector:
        raise RotationCodecError("selectors 1..30 are retained as opaque markers")
    return bytes((header,))


def rotation_is_identity(field: RotationField | None, record_type: str = "type27") -> bool | None:
    """Return identity status where the native parser format is matched.

    The native decoder maps selectors 1..24 through a table then expands each
    axis-pair code into a basis matrix. Code 1 expands to (+X,+Y,+Z), the
    identity matrix. The type-27 reader candidate's exact position/selector/
    conditional-six-byte quaternion layout matches the repository's type-27
    parser; nonzero selector 2 expands code 1 to identity. Type 51's optional-
    frame and six-byte quaternion branch matches its native reader; type 57's
    flags, compact selector table, position, quaternion, and velocity fields
    match its native reader.
    """
    if field is None or field.kind != "selector":
        return None
    if record_type == "type27":
        # Native `0x104F233B0` reads the repository type-27 shape exactly:
        # position 3xf32, a selector byte, and six quaternion bytes only for
        # selector zero. Nonzero selectors expand as (selector - 1), so 2
        # selects native axis-pair code 1, the identity basis. This is a
        # layout-matched inference from the pinned native reader; its numeric
        # property-type registration is not independently recovered.
        return field.selector == 2
    if record_type == "type51":
        # Native 0x104F23698 reads a signed flag. The high bit marks the
        # optional frame; low-seven-bit ID 0 decodes the six-byte quaternion,
        # while nonzero IDs expand (ID - 1). ID 2 therefore expands code 1,
        # the identity basis.
        if not field.selector & 0x80 or type(field.value) is not int:
            return None
        return field.value == 2
    if record_type == "type57":
        # Native 0x104F213A4 selects the low five flag bits; IDs 1..24 pass
        # through byte_105EAA3E3 to the basis expander. ID 1 maps to code 1,
        # the identity basis. ID 0 uses a quaternion; ID 31 uses a separate
        # seven-float path, so both remain unknown here.
        if type(field.value) is not int or field.value != field.selector:
            return None
        if field.selector == 0 or field.selector >= 25:
            return None
        return _COMPACT_SELECTOR_TO_AXIS_PAIR[field.selector] == 1
    if record_type != "native_physics":
        raise RotationCodecError("unknown record type for identity check")
    selector = field.selector
    if not 0 <= selector <= 255:
        return None
    if selector > 24:
        return None
    if selector == 0:
        return None
    axis_pair_code = _COMPACT_SELECTOR_TO_AXIS_PAIR[selector]
    return axis_pair_code == 1
