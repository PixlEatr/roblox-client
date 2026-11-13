//! Bounded transport framing shared by the Rust client session.
//!
//! QUIC variable integers use the top two bits of the first byte for width.
//! This module handles byte framing only; it does not create credentials,
//! authenticate a session, or implement QUIC/TLS itself.

use std::fmt;

pub const CONTROL_CHANNEL: u32 = 0x6374_726c;
pub const MAX_FRAME: usize = 8 * 1024 * 1024;
pub const MAX_DATAGRAM_PAYLOAD: usize = 1100;
pub const MAX_RUPP_HEADER: usize = u16::MAX as usize;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ChannelError(pub &'static str);

impl fmt::Display for ChannelError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.0)
    }
}

impl std::error::Error for ChannelError {}

/// Encode an RFC 9000 QUIC variable integer, optionally using an explicit width.
pub fn encode_varint(value: u64, width: Option<usize>) -> Result<Vec<u8>, ChannelError> {
    let selected = match width {
        Some(width) => width,
        None if value < (1u64 << 6) => 1,
        None if value < (1u64 << 14) => 2,
        None if value < (1u64 << 30) => 4,
        None if value < (1u64 << 62) => 8,
        None => return Err(ChannelError("invalid QUIC variable integer")),
    };
    let marker = match selected {
        1 if value < (1u64 << 6) => 0u8,
        2 if value < (1u64 << 14) => 0x40,
        4 if value < (1u64 << 30) => 0x80,
        8 if value < (1u64 << 62) => 0xc0,
        _ => return Err(ChannelError("integer does not fit requested QUIC width")),
    };
    let raw = value | ((marker as u64) << (8 * (selected - 1)));
    let bytes = raw.to_be_bytes();
    Ok(bytes[8 - selected..].to_vec())
}

fn varint_width(first: u8) -> usize {
    1usize << (first >> 6)
}

fn decode_varint(bytes: &[u8]) -> Result<(u64, usize), ChannelError> {
    let first = *bytes
        .first()
        .ok_or(ChannelError("truncated QUIC variable integer"))?;
    let width = varint_width(first);
    if bytes.len() < width {
        return Err(ChannelError("truncated QUIC variable integer"));
    }
    let mut value = (first & 0x3f) as u64;
    for byte in &bytes[1..width] {
        value = (value << 8) | *byte as u64;
    }
    Ok((value, width))
}

/// Prefix a message with the native four-byte QUIC-varint form.
pub fn frame(payload: &[u8]) -> Result<Vec<u8>, ChannelError> {
    if payload.len() > MAX_FRAME {
        return Err(ChannelError("channel frame exceeds configured limit"));
    }
    let mut encoded = encode_varint(payload.len() as u64, Some(4))?;
    encoded.reserve(payload.len());
    encoded.extend_from_slice(payload);
    Ok(encoded)
}

/// Incremental reliable-stream frame decoder.
///
/// It consumes each input chunk once and retains at most one bounded frame and
/// an eight-byte prefix. Completed payloads are moved out, avoiding front-drain
/// memmoves and an extra copy of bytes that span transport reads.
#[derive(Debug)]
pub struct FrameDecoder {
    prefix: [u8; 8],
    prefix_len: usize,
    expected: Option<usize>,
    payload: Vec<u8>,
    max_frame: usize,
}

impl Default for FrameDecoder {
    fn default() -> Self {
        Self::new(MAX_FRAME)
    }
}

impl FrameDecoder {
    pub fn new(max_frame: usize) -> Self {
        Self {
            prefix: [0; 8],
            prefix_len: 0,
            expected: None,
            payload: Vec::new(),
            max_frame,
        }
    }

    pub fn buffered_len(&self) -> usize {
        self.prefix_len + self.payload.len()
    }

    pub fn feed(&mut self, mut data: &[u8]) -> Result<Vec<Vec<u8>>, ChannelError> {
        // A single huge caller chunk could otherwise produce an unbounded
        // Vec<Vec<u8>> of empty frames. Network drivers should feed bounded
        // transport reads and call again with the remaining bytes.
        const MAX_MESSAGES_PER_FEED: usize = 4096;
        let mut messages = Vec::new();
        while !data.is_empty() {
            if self.expected.is_none() {
                if self.prefix_len == 0 {
                    self.prefix[0] = data[0];
                    self.prefix_len = 1;
                    data = &data[1..];
                }
                let width = varint_width(self.prefix[0]);
                if self.prefix_len < width {
                    let count = (width - self.prefix_len).min(data.len());
                    self.prefix[self.prefix_len..self.prefix_len + count]
                        .copy_from_slice(&data[..count]);
                    self.prefix_len += count;
                    data = &data[count..];
                    if self.prefix_len < width {
                        break;
                    }
                }
                let (length, _) = decode_varint(&self.prefix[..width])?;
                self.prefix_len = 0;
                let length = usize::try_from(length)
                    .map_err(|_| ChannelError("peer frame length is out of range"))?;
                if length > self.max_frame {
                    self.reset();
                    return Err(ChannelError("peer frame exceeds configured limit"));
                }
                self.expected = Some(length);
                if length == 0 {
                    self.expected = None;
                    messages.push(Vec::new());
                    if messages.len() > MAX_MESSAGES_PER_FEED {
                        self.reset();
                        return Err(ChannelError("too many frames in one feed"));
                    }
                    continue;
                }
                self.payload.reserve(length);
            }

            let expected = self.expected.expect("expected length set above");
            let remaining = expected - self.payload.len();
            let count = remaining.min(data.len());
            self.payload.extend_from_slice(&data[..count]);
            data = &data[count..];
            if self.payload.len() == expected {
                messages.push(std::mem::take(&mut self.payload));
                self.expected = None;
                if messages.len() > MAX_MESSAGES_PER_FEED {
                    self.reset();
                    return Err(ChannelError("too many frames in one feed"));
                }
            }
        }
        Ok(messages)
    }

    pub fn finish(&self) -> Result<(), ChannelError> {
        if self.prefix_len != 0 || self.expected.is_some() || !self.payload.is_empty() {
            return Err(ChannelError("stream ended inside a channel frame"));
        }
        Ok(())
    }

    fn reset(&mut self) {
        self.prefix_len = 0;
        self.expected = None;
        self.payload.clear();
    }
}

/// Serialize OpenReliableChannel's six-byte body.
pub fn reliable_header(application: u8, channel: u32) -> Result<[u8; 7], ChannelError> {
    if application > 8 {
        return Err(ChannelError("invalid reliable channel header field"));
    }
    let mut out = [0; 7];
    out[0] = 6;
    out[1] = 1;
    out[2] = application;
    out[3..7].copy_from_slice(&channel.to_be_bytes());
    Ok(out)
}

/// Parse one OpenReliableChannel header; trailing stream bytes remain payload.
pub fn parse_reliable_header(data: &[u8]) -> Result<(u8, u32), ChannelError> {
    if data.len() < 7 {
        return Err(ChannelError("incomplete reliable channel header"));
    }
    let application = data[2];
    if data[0] != 6 || data[1] != 1 || application > 8 {
        return Err(ChannelError("unrecognized reliable channel header"));
    }
    let channel = u32::from_be_bytes([data[3], data[4], data[5], data[6]]);
    Ok((application, channel))
}

pub fn control_open(application: u8, channel: u8, wire_id: u32) -> Result<[u8; 10], ChannelError> {
    if application > 8 || channel >= 32 || wire_id == 0 || wire_id >= (1u32 << 31) {
        return Err(ChannelError("invalid unreliable channel"));
    }
    let mut out = [0; 10];
    out[0] = 2;
    out[1] = application;
    out[2..6].copy_from_slice(&(channel as u32).to_be_bytes());
    out[6..10].copy_from_slice(&wire_id.to_be_bytes());
    Ok(out)
}

pub fn parse_control_open(data: &[u8]) -> Result<(u8, u8, u32), ChannelError> {
    if data.len() != 10 || data[0] != 2 {
        return Err(ChannelError("invalid unreliable channel announcement"));
    }
    let application = data[1];
    let channel = u32::from_be_bytes([data[2], data[3], data[4], data[5]]);
    let channel = u8::try_from(channel)
        .map_err(|_| ChannelError("invalid unreliable channel announcement"))?;
    let wire_id = u32::from_be_bytes([data[6], data[7], data[8], data[9]]);
    control_open(application, channel, wire_id)?;
    // Bytes 3..6 are reserved in the recovered layout. The Python reader
    // historically accepts any reserved value and only reads the other fields.
    Ok((application, channel, wire_id))
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct UnreliableDatagram<'a> {
    pub wire_id: u32,
    pub sequence: u64,
    pub total: u64,
    pub offset: u64,
    pub complete: bool,
    /// Borrowed from the input datagram; no second payload allocation.
    pub payload: &'a [u8],
}

pub fn encode_datagram(
    wire_id: u32,
    sequence: u64,
    payload: &[u8],
) -> Result<Vec<u8>, ChannelError> {
    if wire_id == 0
        || wire_id >= (1u32 << 31)
        || sequence >= (1u64 << 62)
        || payload.len() > MAX_DATAGRAM_PAYLOAD
    {
        return Err(ChannelError("unreliable packet exceeds supported bounds"));
    }
    let mut result = encode_varint(wire_id as u64, None)?;
    result.extend_from_slice(&encode_varint(sequence, None)?);
    result.push(0); // unfragmented total-length marker observed by the client.
    result.extend_from_slice(payload);
    Ok(result)
}

pub fn parse_datagram(data: &[u8]) -> Result<UnreliableDatagram<'_>, ChannelError> {
    let (wire_id, first_width) = decode_varint(data)?;
    let (sequence, second_width) = decode_varint(&data[first_width..])?;
    let mut cursor = first_width + second_width;
    let (total, total_width) = decode_varint(&data[cursor..])?;
    cursor += total_width;
    let offset = if total == 0 {
        0
    } else {
        let (offset, width) = decode_varint(&data[cursor..])?;
        cursor += width;
        offset
    };
    let payload = &data[cursor..];
    if wire_id == 0
        || wire_id >= (1u64 << 31)
        || total > 65_536
        || (total != 0
            && offset
                .checked_add(payload.len() as u64)
                .is_none_or(|end| end > total))
    {
        return Err(ChannelError("invalid unreliable datagram bounds"));
    }
    Ok(UnreliableDatagram {
        wire_id: wire_id as u32,
        sequence,
        total,
        offset,
        complete: total == 0 || total == payload.len() as u64,
        payload,
    })
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RuppField<'a> {
    pub kind: u8,
    pub value: &'a [u8],
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RuppPacket<'a> {
    pub kind: u8,
    pub flags: u8,
    pub header_size: usize,
    pub fields: Vec<RuppField<'a>>,
    pub payload: &'a [u8],
}

/// Parse the observed RUPP TLV envelope, leaving token values borrowed.
/// Unwrapped QUIC bytes return `Ok(None)`. This parser does not construct or
/// rotate routing tokens.
pub fn parse_rupp(data: &[u8]) -> Result<Option<RuppPacket<'_>>, ChannelError> {
    let Some(&kind) = data.first() else {
        return Ok(None);
    };
    if !matches!(kind, 1 | 3 | 4) {
        return Ok(None);
    }
    if data.len() < 4 {
        return Err(ChannelError("truncated RUPP header"));
    }
    let flags = data[1];
    let header_size = u16::from_be_bytes([data[2], data[3]]) as usize;
    if header_size < 4 || header_size > data.len() || header_size > MAX_RUPP_HEADER {
        return Err(ChannelError("invalid RUPP header length"));
    }
    let mut fields = Vec::new();
    let mut cursor = 4;
    while cursor < header_size {
        if header_size - cursor < 2 {
            return Err(ChannelError("truncated RUPP TLV"));
        }
        let field_kind = data[cursor];
        let length = data[cursor + 1] as usize;
        cursor += 2;
        if length > header_size - cursor {
            return Err(ChannelError("truncated RUPP value"));
        }
        if fields.len() == 4096 {
            return Err(ChannelError("too many RUPP fields"));
        }
        fields.push(RuppField {
            kind: field_kind,
            value: &data[cursor..cursor + length],
        });
        cursor += length;
    }
    Ok(Some(RuppPacket {
        kind,
        flags,
        header_size,
        fields,
        payload: &data[header_size..],
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rfc_9000_varint_vectors() {
        for (value, expected) in [
            (37, "25"),
            (15_293, "7bbd"),
            (494_878_333, "9d7f3e7d"),
            (151_288_809_941_952_652, "c2197c5eff14e88c"),
        ] {
            let bytes = encode_varint(value, None).unwrap();
            assert_eq!(
                bytes.iter().map(|b| format!("{b:02x}")).collect::<String>(),
                expected
            );
            assert_eq!(decode_varint(&bytes).unwrap(), (value, bytes.len()));
        }
        assert!(encode_varint(1 << 62, None).is_err());
        assert!(encode_varint(64, Some(1)).is_err());
    }

    #[test]
    fn stream_decoder_handles_every_split_and_coalescing() {
        let bytes = [0x80, 0, 0, 2, 0, 0];
        assert_eq!(frame(&[0, 0]).unwrap(), bytes);
        for split in 0..=bytes.len() {
            let mut decoder = FrameDecoder::default();
            let mut frames = decoder.feed(&bytes[..split]).unwrap();
            frames.extend(decoder.feed(&bytes[split..]).unwrap());
            assert_eq!(frames, vec![vec![0, 0]]);
            decoder.finish().unwrap();
        }
        let mut decoder = FrameDecoder::default();
        assert_eq!(
            decoder
                .feed(&[0, 1, b'x', 0x40, 2, b'a', b'b', 0x80, 0, 0, 1, b'c'])
                .unwrap(),
            vec![b"".to_vec(), b"x".to_vec(), b"ab".to_vec(), b"c".to_vec()]
        );
        decoder.finish().unwrap();
    }

    #[test]
    fn stream_decoder_bounds_memory_and_detects_truncation() {
        let mut decoder = FrameDecoder::new(16);
        assert_eq!(
            decoder.feed(&[0x80, 0, 0, 17]),
            Err(ChannelError("peer frame exceeds configured limit"))
        );
        assert_eq!(decoder.buffered_len(), 0);
        for input in [&[0x80][..], &[0x80, 0, 0, 2, b'a'][..]] {
            let mut decoder = FrameDecoder::default();
            decoder.feed(input).unwrap();
            assert_eq!(
                decoder.finish(),
                Err(ChannelError("stream ended inside a channel frame"))
            );
        }
        // Minimal-size form is legal even when four bytes are used by the peer.
        let mut decoder = FrameDecoder::default();
        assert_eq!(decoder.feed(&[0x80, 0, 0, 0]), Ok(vec![Vec::new()]));
    }

    #[test]
    fn reliable_header_matches_python_vectors() {
        let header = reliable_header(4, 0x1020_3040).unwrap();
        assert_eq!(header, [6, 1, 4, 0x10, 0x20, 0x30, 0x40]);
        assert_eq!(parse_reliable_header(&header).unwrap(), (4, 0x1020_3040));
        assert_eq!(
            parse_reliable_header(&[6, 1, 4, 0x10, 0x20, 0x30, 0x40, 9]).unwrap(),
            (4, 0x1020_3040)
        );
        for bad in [
            &[][..],
            &[6, 1, 9, 0, 0, 0, 0][..],
            &[7, 1, 1, 0, 0, 0, 0][..],
        ] {
            assert!(parse_reliable_header(bad).is_err());
        }
    }

    #[test]
    fn unreliable_headers_and_borrowed_fragment_payload_match_python() {
        assert_eq!(
            encode_datagram(2, 64, b"abc").unwrap(),
            b"\x02\x40\x40\0abc"
        );
        let packet = parse_datagram(b"\x02\x40\x40\0abc").unwrap();
        assert_eq!(
            (packet.wire_id, packet.sequence, packet.total, packet.offset),
            (2, 64, 0, 0)
        );
        assert!(packet.complete);
        assert_eq!(packet.payload, b"abc");
        let packet = parse_datagram(b"\x03\x01\x05\x02abc").unwrap();
        assert!(!packet.complete);
        assert_eq!(
            (packet.total, packet.offset, packet.payload),
            (5, 2, &b"abc"[..])
        );
        for bad in [
            &[][..],
            &[0xc0, 0][..],
            &[3, 1][..],
            &[3, 1, 2, 1, b'a', b'b', b'c'][..],
            &[0, 1, 0, b'a', b'b', b'c'][..],
        ] {
            assert!(parse_datagram(bad).is_err());
        }
    }

    #[test]
    fn unreliable_control_fields_are_network_endian() {
        let raw = [2, 4, 0, 0, 0, 6, 0, 0, 0, 7];
        assert_eq!(control_open(4, 6, 7).unwrap(), raw);
        assert_eq!(parse_control_open(&raw).unwrap(), (4, 6, 7));
        assert!(control_open(9, 0, 1).is_err());
        assert!(control_open(4, 32, 1).is_err());
        assert!(control_open(4, 1, 1 << 31).is_err());
    }

    #[test]
    fn rupp_parser_preserves_unknown_fields_and_borrows_values() {
        let data = [1, 0, 0, 8, 200, 2, 0xaa, 0xbb, 3, 4];
        let packet = parse_rupp(&data).unwrap().unwrap();
        assert_eq!((packet.kind, packet.flags, packet.header_size), (1, 0, 8));
        assert_eq!(
            (packet.fields[0].kind, packet.fields[0].value),
            (200, &[0xaa, 0xbb][..])
        );
        assert_eq!(packet.payload, &[3, 4]);
        assert!(parse_rupp(&[9, 1, 2]).unwrap().is_none());
        for bad in [&[1][..], &[1, 0, 0, 5, 2][..], &[1, 0, 0, 7, 2, 3, 4][..]] {
            assert!(parse_rupp(bad).is_err());
        }
    }
}
