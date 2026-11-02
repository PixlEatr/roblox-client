"""Data-only parsing of the recovered legacy replication ping layout.

Native PingBackItem writer: 104d949c4. The reply includes client-maintained
fields whose complete meanings are not established, so this module does not
generate replies or fill those fields with guessed values.
"""
from wire_reader import Reader, WireError


def read_ping(reader, kind):
    if kind not in (5, 6):
        raise WireError('Not a replication ping item')
    flags = reader.u8()
    # Extended tracing flags have a different layout and are not negotiated.
    if flags & ~5:
        raise WireError('Unsupported replication ping flags')
    result = {'kind': kind, 'flags': flags, 'timestamp': reader.u64(),
              'first_word': reader.u32()}
    result['reply_words'] = [reader.u32() for _ in range(6)] if flags & 1 else []
    result['trailer_words'] = [reader.u32(), reader.u32()]
    unreliable = reader.u8()
    if unreliable > 1:
        raise WireError('Invalid ping channel flag')
    result['unreliable'] = bool(unreliable)
    if flags & 4:
        result['extra_word'] = reader.u32()
    return result


def ping_packet(payload):
    """Parse a complete ping-only channel-3 packet; reject partial records."""
    reader = Reader(payload)
    if reader.u8() != 131:
        raise WireError('Not a replication data packet')
    result = []
    for _ in range(64):
        kind = reader.u8()
        if kind == 0:
            reader.finish()
            return result
        result.append(read_ping(reader, kind))
    raise WireError('Too many ping items')
