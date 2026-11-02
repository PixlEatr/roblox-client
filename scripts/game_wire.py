"""Bounded Roblox channel framing and server-issued early authentication.

Evidence: 102236878/1021554e4 (QUIC varint frame size), 104c95b0c
(ticket fields), 104c95e68 (copy issued blobs), 104c93480 (packet 0xa8).
This module does not construct or sign authentication credentials.
"""
import base64
import binascii
import struct
import json


CONTROL_CHANNEL = 0x6374726C
MAX_FRAME = 8 * 1024 * 1024


def encode_varint(value, width=None):
    if not isinstance(value, int) or not 0 <= value < 1 << 62:
        raise ValueError("Invalid QUIC variable integer")
    widths = (1, 2, 4, 8)
    if width is None:
        width = next(w for w in widths if value < 1 << (8 * w - 2))
    if width not in widths or value >= 1 << (8 * width - 2):
        raise ValueError("Integer does not fit the requested width")
    return (value | (widths.index(width) << (8 * width - 2))).to_bytes(width, "big")


def frame(payload):
    if len(payload) > MAX_FRAME:
        raise ValueError("Channel frame exceeds the configured limit")
    # Native NetStream uses the legal four-byte form even for short frames.
    return encode_varint(len(payload), 4) + payload


class FrameDecoder:
    """Incremental framing; TCP/QUIC delivery boundaries are not message boundaries."""

    def __init__(self, max_frame=MAX_FRAME):
        self.buffer = bytearray()
        self.max_frame = max_frame
        self.expected = None

    def feed(self, data):
        self.buffer.extend(data)
        messages = []
        while self.buffer:
            if self.expected is None:
                width = 1 << (self.buffer[0] >> 6)
                if len(self.buffer) < width:
                    break
                self.expected = int.from_bytes(self.buffer[:width], "big") & ((1 << (8 * width - 2)) - 1)
                del self.buffer[:width]
                if self.expected > self.max_frame:
                    raise ValueError("Peer frame exceeds the configured limit")
            if len(self.buffer) < self.expected:
                break
            messages.append(bytes(self.buffer[:self.expected]))
            del self.buffer[:self.expected]
            self.expected = None
        # A zero-size frame whose prefix ended the chunk is already complete.
        if self.expected == 0:
            messages.append(b"")
            self.expected = None
        return messages

    def finish(self):
        if self.buffer or self.expected is not None:
            raise ValueError("Stream ended inside a channel frame")


def issued_early_auth(ticket):
    """Serialize the two opaque blobs issued in this account's join ticket."""
    fields = ticket.split(";")
    if len(fields) != 5 or not fields[4].isdecimal():
        raise ValueError("Unrecognized issued join ticket structure")
    try:
        first, second = (base64.b64decode(fields[i], validate=True) for i in (2, 3))
        version = int(fields[4])
    except (ValueError, binascii.Error):
        raise ValueError("Invalid issued authentication field encoding") from None
    if not 0 <= version <= 255 or len(first) not in (32, 33, 50) or not 1 <= len(second) <= 255:
        raise ValueError("Unsupported issued authentication field sizes")
    return bytes((0xA8, version, len(first))) + first + bytes((len(second),)) + second


def feature_configuration(payload):
    """Server packet 147: subtype, LE16 count, pairs of LE16-length strings.

    Native client handler 104cb2b78; exact parsing also consumes all 1816 bytes
    of the first captured server configuration (47 pairs).
    """
    if len(payload) < 4 or payload[:2] != b"\x93\0":
        raise ValueError("Unrecognized feature-configuration packet")
    count = struct.unpack_from("<H", payload, 2)[0]
    offset, result = 4, {}
    for _ in range(count):
        pair = []
        for field in range(2):
            if offset + 2 > len(payload):
                raise ValueError("Truncated feature-configuration length")
            size = struct.unpack_from("<H", payload, offset)[0]
            offset += 2
            if offset + size > len(payload):
                raise ValueError("Truncated feature-configuration value")
            pair.append(payload[offset:offset + size].decode("utf-8"))
            offset += size
        if pair[0] in result:
            raise ValueError("Duplicate feature-configuration name")
        result[pair[0]] = pair[1]
    if offset != len(payload):
        raise ValueError("Trailing feature-configuration bytes")
    return result


def encode_unsigned(value):
    """Network BitStream unsigned LEB128, 1015f9530; distinct from QUIC varint."""
    if not isinstance(value, int) or not 0 <= value < 1 << 64:
        raise ValueError("Unsigned network integer is outside uint64")
    data = bytearray()
    while value > 127:
        data.append((value & 127) | 128)
        value >>= 7
    data.append(value)
    return bytes(data)


def encode_string(value):
    data = value.encode("utf-8")
    if len(data) > MAX_FRAME:
        raise ValueError("Network string exceeds the configured limit")
    return encode_unsigned(len(data)) + data


def player_join_info(join, join_ticket=None):
    """Account/player values from the issued response; no identity invention.

    The native builder at 104d5a380 constructs this object before connecting.
    Retain issued restrictions and opaque credentials without modifying them.
    """
    fields = ("UserId", "UserName", "DisplayName", "HasVerifiedBadge", "PartyId",
              "RandomSeed1", "VerifiedAMP", "PrivateServerID", "PrivateServerOwnerID",
              "DomainUserId", "UserIdMode", "ServerClaimedTime", "APIsecurityToken",
              "Rm3Tier0CadenceSeconds", "ServerDMRecording", "SuperSafeChat",
              "MembershipType", "HasRobloxSubscription", "AgeChecked", "AccountAge",
              "WhoCanWhisperChatWithMeInExperiences", "GameChatType", "CharacterAppearance",
              "FollowUserId", "MatchmakingAttributes", "ShowRobloxTranslations",
              "TranslationDisplayMode", "ImageTranslationContentVariantType")
    info = {key: join[key] for key in fields if key in join}
    for target, source in (("CharacterAppearanceId", "characterAppearanceId"),
                           ("IsUnder13", "IsUnknownOrUnder13"), ("CountryRegionCode", "CountryCode"),
                           ("LocaleId", "RobloxLocale"), ("LocaleIdInternal", "UgcLocale")):
        if source in join:
            info[target] = join[source]
    info.update({"IsTeleportedIn": False, "VrDevice": "", "UserAgentStr": "RobloxProtocolResearch/0.1 (macOS)",
                 # Native 104d5a380 uses 102c23b80 / 102c23b90 for this pair.
                 "OsPlatform": "OSX", "OsPlatformName": "MacOS", "ClientRobloxChannel": ""})
    # Native direct joins use empty context strings without launch/teleport
    # context (104d545ac, 104d54b04/0c, 104d5a380).
    for field in ("RawJoinData", "ClientJoinData", "GameJoinContext"):
        info[field] = join.get(field, "")
    # Native string getter reaches libc++ std::to_string(int) for this field.
    if type(info.get("AgeChecked")) is int:
        info["AgeChecked"] = str(info["AgeChecked"])
    if join_ticket is not None:
        info["__joinTicket"] = json.dumps(join_ticket, separators=(",", ":"))
    return info


def protocol_sync(join, join_ticket=None, luau_range=None):
    """Packet 144 at 104c8f4d0, with optional bytecode-range negotiation.

    Version 36 is initialized at 104d42a4c. The trailing six build constants
    are copied/decoded from this exact binary, not signatures or credentials.
    The range field requires capability 0x100000. No optional integrity
    extension is claimed by this encoder.
    """
    info = json.dumps(player_join_info(join, join_ticket), separators=(",", ":"), ensure_ascii=False)
    header = b"\x90\x01" + struct.pack("<I", 36)
    if luau_range is not None:
        low, high = luau_range
        if not 13 <= low <= high <= 14:
            raise ValueError("Bytecode range is outside the existing static reader's versions")
        header += bytes((low, high))
    header += struct.pack("<H", 0)  # supported feature-name count = 0
    constants = (0x85CB1F5E, 0xBF907B98, 0x8AB1A79E, 0x85CB1F5E, 0x11496016, 0xCBEFFE78)
    return header + encode_string(info) + struct.pack("<6I", *constants)
