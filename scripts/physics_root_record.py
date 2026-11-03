"""Proven root-record framing for restricted native client/server branches.

The module composes the selected build's 104D4CBAC envelope, client direct-current
body, and restricted server no-history body. It does not add the outer packet
header or transport send, and requires the caller to provide current runtime
mapping and branch values.
"""
from __future__ import annotations

from dataclasses import dataclass


def _varuint(value: int) -> bytes:
    if type(value) is not int or not 0 <= value < 1 << 64:
        raise PhysicsRootRecordError("reference tag must be an unsigned 64-bit integer")
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


class PhysicsRootRecordError(ValueError):
    """Invalid inputs to the statically mapped root envelope."""


def _root_current_bytes(record):
    # D46074 passes root=1 into D4654C. Its single-current branch calls the
    # scoped-reference reader DCD1F58 after the transform, not the child index
    # reader. A byte-valued CurrentRecord.state is therefore not a root suffix.
    reference = getattr(record, 'reference_bytes', None)
    if (getattr(record, 'state', None) is not None
            or not isinstance(reference, bytes) or not reference):
        raise PhysicsRootRecordError('root current record requires an encoded reference suffix')
    try:
        current = record.encode()
    except (AttributeError, TypeError, ValueError) as exc:
        raise PhysicsRootRecordError('could not encode root current record') from exc
    if not isinstance(current, bytes) or not current:
        raise PhysicsRootRecordError('current record must encode to non-empty bytes')
    return current


@dataclass(frozen=True)
class ClientChildBody:
    """An explicit dependent body in native DCEC preorder.

    Caller establishes the actual body tree and scoped identity. Dependent
    current records use their explicit one-byte origin-cache suffix.
    """
    object_reference: bytes
    current_record: object
    motor_records: tuple = ()
    children: tuple = ()


def _client_children_bytes(children, velocity_mode):
    from physics_children import encode_motor_pose_block
    if not isinstance(children,(tuple,list)):
        raise PhysicsRootRecordError('Child source must be an ordered sequence')
    out=bytearray(); seen=set(); count=0
    def visit(items):
        nonlocal count
        for child in items:
            if not isinstance(child,ClientChildBody):
                raise PhysicsRootRecordError('Child source requires ClientChildBody values')
            count+=1
            if count>256:
                raise PhysicsRootRecordError('Child source exceeds body budget')
            reference=child.object_reference
            if not isinstance(reference,bytes) or not reference or reference==b'\x00' or reference in seen:
                raise PhysicsRootRecordError('Child source reference is missing or duplicated')
            seen.add(reference)
            record=child.current_record
            if (type(getattr(record,'state',None)) is not int
                    or not 0<=record.state<=255
                    or getattr(record,'reference_bytes',None) is not None
                    or getattr(record,'velocity_mode',None)!=velocity_mode):
                raise PhysicsRootRecordError('Dependent body requires the matching byte-suffix current record')
            out.extend(reference)
            out.extend(encode_motor_pose_block(child.motor_records))
            out.extend(record.encode())
            if not isinstance(child.children,(tuple,list)):
                raise PhysicsRootRecordError('Child descendants must be an ordered sequence')
            visit(child.children)
    visit(children)
    return bytes(out)


@dataclass(frozen=True)
class ZeroChildNoHistoryConditions:
    """Explicit native-layout values for one narrow supported branch."""

    sender_mode: int
    history_value: int
    motor_records: tuple
    children: tuple
    cached_block: bytes | None
    stream_available: bool
    record_state: int
    body_selected: bool

    def validate(self):
        if (type(self.sender_mode) is not int or self.sender_mode != 1
                or type(self.history_value) is not int or self.history_value != 0):
            raise PhysicsRootRecordError("only the native no-history body branch is supported")
        if (not isinstance(self.motor_records, (tuple, list)) or len(self.motor_records) != 0
                or not isinstance(self.children, (tuple, list)) or len(self.children) != 0):
            raise PhysicsRootRecordError("only empty motor and child source lists are supported")
        if self.cached_block is not None:
            raise PhysicsRootRecordError("cached root block encoding is not supported")
        if type(self.stream_available) is not bool or not self.stream_available:
            raise PhysicsRootRecordError("native stream must be available")
        if type(self.record_state) is not int or self.record_state != 1:
            raise PhysicsRootRecordError("native root record state byte must equal one")
        if type(self.body_selected) is not bool or not self.body_selected:
            raise PhysicsRootRecordError("native root body branch must be selected")


def encode_instance_reference(context_tag: int, key: tuple[int, int] | None) -> bytes:
    """Encode 104CD1DD0/E48's reference word.

    ``key`` is the two-u32 value at the native object's +48 field. A first
    word of 0xffffffff selects null and emits ULEB128 zero. Otherwise the
    first word itself is the ULEB128 tag when nonzero; a zero first word falls
    back to the context tag. Native then writes the second word as
    four little-endian bytes. The caller must source the context tag and key
    from the live instance map.
    """
    if key is None:
        return _varuint(0)
    if (not isinstance(key, tuple) or len(key) != 2
            or type(key[0]) is not int or type(key[1]) is not int
            or not 0 <= key[0] <= 0xFFFFFFFF
            or not 0 <= key[1] <= 0xFFFFFFFF):
        raise PhysicsRootRecordError("reference key must contain two u32 words")
    sentinel, raw_id = key
    if sentinel == 0xFFFFFFFF:
        return _varuint(0)
    if type(context_tag) is not int or not 0 <= context_tag <= 0xFFFFFFFF:
        raise PhysicsRootRecordError("context tag must be a u32")
    tag = sentinel if sentinel != 0 else context_tag
    return _varuint(tag) + raw_id.to_bytes(4, "little")


def encode_zero_child_no_history_body(
    current_record: CurrentRecord, *, d76c_flags: int,
    conditions: ZeroChildNoHistoryConditions,
) -> bytes:
    """Encode the supported DBB0/DCEC path for a proven empty source state.

    This composes DE2C's zero motor-record count, DBB0's sender+88==1/a4==0
    current-body prefix and E704 record, then D76C's null child terminator.
    It must only be selected when the native motor source is empty and the
    low bit of state+94 clears child traversal. The outer C650 null reference
    remains outside this root body and should be appended by the packet layer.
    """
    if not isinstance(conditions, ZeroChildNoHistoryConditions):
        raise PhysicsRootRecordError("explicit no-history branch evidence is required")
    conditions.validate()
    if type(d76c_flags) is not int or not 0 <= d76c_flags <= 255:
        raise PhysicsRootRecordError("D76C flags must be an explicit byte")
    # Native v11 is state+94: it is shifted into flags bit 5 and its low bit
    # independently gates DCEC, so the emitted bit is a reliable branch check.
    if d76c_flags & 0x20:
        raise PhysicsRootRecordError("D76C flags select the branch that omits child traversal")
    expected_mode = 'short' if d76c_flags & 0x40 else 'compact'
    if getattr(current_record, 'velocity_mode', None) != expected_mode:
        raise PhysicsRootRecordError('velocity codec must agree with D76C bit6')
    current = _root_current_bytes(current_record)
    # DE2C's verified ULEB count is zero for the explicitly empty source list.
    # DBB0's mode==1/a4==0 prefix is byte 1 then a four-byte zero.
    no_history_prefix = b"\x01\x00\x00\x00\x00"
    return (bytes((d76c_flags,)) + b"\x00" + no_history_prefix
            + current + encode_instance_reference(0, None))


def encode_client_current_body(current_record, *, d76c_flags,
                               motor_records=(), child_records=()):
    """Encode DBB0's ClientReplicator direct-current branch.

    Client constructor 104C9E150 receives role zero at 104C9DA18; the base
    stores it at +252. Sender+88 is consequently false, so DBB0 writes the
    motor block followed directly by E704, without a history count/time prefix.
    Motor records must be explicitly ordered MotorPoseRecord values. Outer bit6
    selects velocity precision; bit5 suppresses child traversal/terminator.
    The bit7 local-space variant requires additional producer evidence.
    """
    if type(d76c_flags) is not int or not 0 <= d76c_flags <= 255:
        raise PhysicsRootRecordError('D76C flags must be an explicit byte')
    if d76c_flags & 0x80:
        raise PhysicsRootRecordError('the optional local-space variant is unsupported')
    if not isinstance(child_records, (tuple, list)):
        raise PhysicsRootRecordError('client child source must be an ordered sequence')
    if child_records and d76c_flags & 0x20:
        raise PhysicsRootRecordError('D76C flags suppress the supplied child source')
    from physics_children import encode_motor_pose_block
    try:
        motors=encode_motor_pose_block(motor_records)
    except (TypeError,ValueError,OverflowError) as error:
        raise PhysicsRootRecordError('client motor source could not be encoded') from error
    expected_mode = 'short' if d76c_flags & 0x40 else 'compact'
    if getattr(current_record, 'velocity_mode', None) != expected_mode:
        raise PhysicsRootRecordError('velocity codec must agree with D76C bit6')
    current = _root_current_bytes(current_record)
    children = _client_children_bytes(child_records,expected_mode)
    children_end = b'' if d76c_flags & 0x20 else encode_instance_reference(0, None)
    return bytes((d76c_flags,)) + motors + current + children + children_end


def encode_zero_child_no_history_root_record(
    context_tag: int, reference_key: tuple[int, int], current_record: CurrentRecord,
    *, d76c_flags: int, conditions: ZeroChildNoHistoryConditions,
) -> bytes:
    """Compose one root envelope for the explicit empty/no-history branch.

    Caller preconditions are: native root record gate is enabled; no cached
    block is selected; DBB0 uses sender+88==1 and a4==0; the actual motor
    source and dynamic child list are empty; state+94's low bit clears child
    traversal; and ``context_tag``/``reference_key`` come from the live mapping.
    The returned bytes are one CBAC root record, not the C650 outer null or
    the complete transport packet.
    """
    if not isinstance(conditions, ZeroChildNoHistoryConditions):
        raise PhysicsRootRecordError("explicit no-history root branch evidence is required")
    conditions.validate()
    root_reference = encode_instance_reference(context_tag, reference_key)
    body = encode_zero_child_no_history_body(
        current_record, d76c_flags=d76c_flags, conditions=conditions)
    return RootRecordEnvelope(root_reference, body).encode(
        stream_available=conditions.stream_available,
        record_enabled=conditions.record_state == 1)


def _opaque_bytes(value, label, *, allow_empty=False):
    if not isinstance(value, bytes) or (not allow_empty and not value):
        raise PhysicsRootRecordError(f"{label} must be explicitly supplied encoded bytes")
    return value


@dataclass(frozen=True)
class RootRecordEnvelope:
    """The 4CBAC sequence around an optional cached block and D76C body.

    Native order is object reference, cached-present boolean, optional cached
    block, body-present boolean, optional D76C body. Boolean writes are the
    native one-byte stream-writer calls. The caller must supply exact opaque
    subfields from a separately verified codec.
    """

    object_reference: bytes
    body: bytes | None
    cached_block: bytes | None = None

    def encode(self, *, stream_available: bool, record_enabled: bool) -> bytes:
        """Encode only when the native 4CBAC prerequisites are satisfied.

        The native function returns before writing if its stream is null or
        its record-state byte is not one. These are caller-supplied facts, not
        inferred from payload bytes.
        """
        if type(stream_available) is not bool or type(record_enabled) is not bool:
            raise PhysicsRootRecordError("native gate inputs must be explicit booleans")
        if not stream_available or not record_enabled:
            raise PhysicsRootRecordError("native root-record gate would return before writing")

        reference = _opaque_bytes(self.object_reference, "object_reference")
        cached_present = self.cached_block is not None
        body_present = self.body is not None
        cached = (b"" if not cached_present else
                  _opaque_bytes(self.cached_block, "cached_block", allow_empty=True))
        body = (b"" if not body_present else
                _opaque_bytes(self.body, "body", allow_empty=True))
        return (reference + bytes((cached_present,)) + cached
                + bytes((body_present,)) + body)


__all__ = ["RootRecordEnvelope", "PhysicsRootRecordError",
           "ClientChildBody",
           "encode_client_current_body",
           "encode_instance_reference", "encode_zero_child_no_history_body",
           "encode_zero_child_no_history_root_record", "ZeroChildNoHistoryConditions"]
