"""Partial framing helpers for native physics current/history body paths.

This is deliberately a body-layout codec, not an outer packet sender. It
implements only the fields proven in 0x104D4DBB0/DEB0/E704. It does not emit a
complete DBB0 body: DBB0 always writes the preceding DE2C motor-like record
block, and the zero-count case is valid only when the actual source list is
empty. Reference bytes are supplied by the caller because native
instance-reference IDs are session data and their mapping is not part of this
static trace. These helpers do not produce a complete outer physics packet.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from typing import Iterable

from physics_packet import TransformHeader, TransformComponent, encode_transform_component


class PhysicsHistoryError(ValueError):
    """Invalid or unsupported current/history body data."""


# Default value read from the selected binary's __DATA initializer at
# 0x106ACD6D0, which DEB0 loads directly. The named FastFlag can override it.
NATIVE_DEFAULT_MAX_SEND_INDEX = 255
NATIVE_MIN_SEND_INDEX = 2


@dataclass(frozen=True)
class CurrentRecord:
    """One E704 transform followed by the native state or reference suffix.

    The decompile has mutually exclusive suffix paths: a state byte, or an
    object reference written by 104CD1E48. Reference bytes must come from the
    caller's verified instance-reference codec.
    """

    header: TransformHeader
    position: tuple[float, float, float]
    rotation: object
    linear_velocity: object | None
    angular_velocity: object | None
    velocity_mode: str
    state: int | None = None
    reference_bytes: bytes | None = None

    def encode(self) -> bytes:
        if (self.state is None) == (self.reference_bytes is None):
            raise PhysicsHistoryError("provide exactly one state byte or encoded reference")
        try:
            body = bytearray(encode_transform_component(
                header=self.header,
                position_value=self.position,
                rotation=self.rotation,
                linear_velocity=self.linear_velocity,
                angular_velocity=self.angular_velocity,
                velocity_mode=self.velocity_mode,
            ))
        except (TypeError, ValueError, OverflowError) as exc:
            raise PhysicsHistoryError("invalid current transform") from exc
        if self.state is not None:
            if type(self.state) is not int or not 0 <= self.state <= 255:
                raise PhysicsHistoryError("state must fit in one byte")
            body.append(self.state)
        else:
            if not isinstance(self.reference_bytes, bytes) or not self.reference_bytes:
                raise PhysicsHistoryError("reference_bytes must be a non-empty encoded reference")
            body.extend(self.reference_bytes)
        return bytes(body)


@dataclass(frozen=True)
class HistoryEntry:
    """A DEB0 sample: float32 time delta followed by an E704 transform record."""

    delta_seconds: float
    record: CurrentRecord

    def encode(self) -> bytes:
        try:
            delta = float(self.delta_seconds)
        except (TypeError, ValueError, OverflowError) as exc:
            raise PhysicsHistoryError("history delta must be a finite float") from exc
        if not math.isfinite(delta) or delta < 0.0:
            raise PhysicsHistoryError("history delta must be finite and non-negative")
        return struct.pack("<f", delta) + self.record.encode()


def encode_no_history_current(record: CurrentRecord) -> bytes:
    """Encode the DBB0 sender+88==1, a4==0 branch fragment.

    The fragment is byte 1, uint32 zero, then E704's transform and explicit
    state/reference suffix. It excludes DBB0's mandatory preceding DE2C
    motor-like block; it is not a complete DBB0 body or packet.
    """
    return b"\x01\x00\x00\x00\x00" + record.encode()


def encode_history(entries: Iterable[HistoryEntry], *,
                   max_send_index: int = NATIVE_DEFAULT_MAX_SEND_INDEX,
                   force_single_sample: bool = False) -> bytes:
    """Encode DBB0's mode==1, a4!=0 branch: count byte and DEB0 entries.

    Entries must already be eligible and ordered like the native ring traversal.
    DEB0 retains the first `max_send_index` entries; its sender+89 branch instead
    retains only the first. The default `DFIntGameNetLocalSpaceMaxSendIndex` in
    this binary is 255, and a resolved runtime override is constrained to 2..255.
    """
    if (type(max_send_index) is not int
            or not NATIVE_MIN_SEND_INDEX <= max_send_index <= NATIVE_DEFAULT_MAX_SEND_INDEX):
        raise PhysicsHistoryError("max_send_index must be between 2 and 255")
    if type(force_single_sample) is not bool:
        raise PhysicsHistoryError("force_single_sample must be a boolean")
    entries = tuple(entries)
    selected = entries[:1 if force_single_sample else max_send_index]
    return bytes((len(selected),)) + b"".join(entry.encode() for entry in selected)


def native_clock_milliseconds(clock_seconds: float, input_seconds: float,
                              workspace_adjustment_seconds: float) -> int:
    """Model 4BCDC's final clock conversion after its inputs are resolved.

    Native code adds the client's double offset and adjusted input to the
    monotonic clock value, multiplies by 1000, then converts to uint64. This
    helper takes the already-resolved clock, input, and Workspace adjustment;
    it does not claim to reproduce the unresolved source of the latter two.
    """
    values = (clock_seconds, input_seconds, workspace_adjustment_seconds)
    if any(not math.isfinite(float(value)) for value in values):
        raise PhysicsHistoryError("clock inputs must be finite")
    value = (float(clock_seconds) + float(input_seconds)
             + float(workspace_adjustment_seconds)) * 1000.0
    if value < 0.0 or value >= 1 << 64:
        raise PhysicsHistoryError("converted timestamp is outside uint64 range")
    return int(value)
