"""Static candidate metadata for the 0.745 physics transport route.

This is not a wire encoder and never sends data. The app ID and context-field
association are assumptions; effective channel mode depends on a runtime bit
that static inspection has not resolved.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PhysicsTransportCandidate:
    channel_index: int = 6
    packed_open_descriptor: int = 0x0000000200000001
    assumed_application_id: int = 4
    input_mode: int = 1
    lower_app_specific_bit: bool | None = None
    verified_context_binding: bool = False
    verified_application_id: bool = False

    @property
    def effective_mode(self) -> int | None:
        """Model the observed wrapper: mode 1 becomes 0 when the bit is set."""
        if self.lower_app_specific_bit is None:
            return None
        if self.input_mode == 1 and self.lower_app_specific_bit:
            return 0
        return self.input_mode

    @property
    def unreliable_open_control_expected(self) -> bool | None:
        """The native path creates OpenUnreliableChannelControl only at mode 1."""
        mode = self.effective_mode
        return None if mode is None else mode == 1


def candidate_mapping() -> dict[str, object]:
    candidate = PhysicsTransportCandidate()
    result = asdict(candidate)
    result.update(
        effective_mode=candidate.effective_mode,
        unreliable_open_control_expected=candidate.unreliable_open_control_expected,
        status="unverified_candidate",
        assumptions=[
            "context+264 is an RbxTransportConnection",
            "its injected app interface carries application ID 4",
            "the lower app-specific mode bit is unresolved",
        ],
    )
    return result


def map_packet_family(packet_family: int) -> dict[str, object] | None:
    """Map the evidenced 0x85 physics family without guessing for others."""
    if type(packet_family) is not int or packet_family != 0x85:
        return None
    return candidate_mapping()


if __name__ == "__main__":
    import json

    print(json.dumps(candidate_mapping(), indent=2))
