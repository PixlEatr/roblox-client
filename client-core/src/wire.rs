//! Bounded readers for the little-endian game protocol.
use std::{
    error::Error,
    fmt,
    io::{self, Read},
};

/// A malformed, truncated, or over-limit network field.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WireError {
    message: String,
}

impl WireError {
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl fmt::Display for WireError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}
impl Error for WireError {}
impl From<io::Error> for WireError {
    fn from(error: io::Error) -> Self {
        Self::new(format!("Compressed stream error: {error}"))
    }
}

/// A cursor over an immutable byte slice. Reads never advance on truncation.
#[derive(Debug, Clone)]
pub struct Reader<'a> {
    data: &'a [u8],
    pos: usize,
}

impl<'a> Reader<'a> {
    pub fn new(data: &'a [u8]) -> Self {
        Self { data, pos: 0 }
    }
    pub fn remaining(&self) -> usize {
        self.data.len() - self.pos
    }
    pub fn position(&self) -> usize {
        self.pos
    }

    pub fn take(&mut self, size: usize) -> Result<&'a [u8], WireError> {
        let end = self
            .pos
            .checked_add(size)
            .ok_or_else(|| WireError::new(format!("Truncated field at offset {}", self.pos)))?;
        if end > self.data.len() {
            return Err(WireError::new(format!(
                "Truncated field at offset {}",
                self.pos
            )));
        }
        let out = &self.data[self.pos..end];
        self.pos = end;
        Ok(out)
    }

    pub fn u8(&mut self) -> Result<u8, WireError> {
        Ok(self.take(1)?[0])
    }
    pub fn i8(&mut self) -> Result<i8, WireError> {
        Ok(i8::from_le_bytes([self.u8()?]))
    }
    pub fn u16(&mut self) -> Result<u16, WireError> {
        Ok(u16::from_le_bytes(
            self.take(2)?.try_into().expect("fixed-size read"),
        ))
    }
    pub fn u32(&mut self) -> Result<u32, WireError> {
        Ok(u32::from_le_bytes(
            self.take(4)?.try_into().expect("fixed-size read"),
        ))
    }
    pub fn i32(&mut self) -> Result<i32, WireError> {
        Ok(i32::from_le_bytes(
            self.take(4)?.try_into().expect("fixed-size read"),
        ))
    }
    pub fn u64(&mut self) -> Result<u64, WireError> {
        Ok(u64::from_le_bytes(
            self.take(8)?.try_into().expect("fixed-size read"),
        ))
    }
    pub fn f32(&mut self) -> Result<f32, WireError> {
        Ok(f32::from_bits(self.u32()?))
    }
    pub fn f64(&mut self) -> Result<f64, WireError> {
        Ok(f64::from_bits(self.u64()?))
    }

    /// Decode the protocol's unsigned LEB128 field with an explicit width.
    pub fn varuint(&mut self, bits: u32) -> Result<u64, WireError> {
        if bits == 0 || bits > 64 {
            return Err(WireError::new("Variable integer width is invalid"));
        }
        let max = if bits == 64 {
            u64::MAX
        } else {
            (1u64 << bits) - 1
        };
        let mut value = 0u64;
        let mut shift = 0u32;
        while shift < bits {
            let part = self.u8()?;
            let payload = u64::from(part & 0x7f);
            if payload > (max >> shift) {
                return Err(WireError::new("Variable integer exceeds width"));
            }
            value |= payload << shift;
            if part & 0x80 == 0 {
                return Ok(value);
            }
            // There cannot be another byte when fewer than seven bits remain.
            if shift + 7 >= bits {
                return Err(WireError::new("Variable integer exceeds width"));
            }
            shift += 7;
        }
        Err(WireError::new("Variable integer exceeds width"))
    }

    pub fn signed(&mut self, bits: u32) -> Result<i64, WireError> {
        let value = self.varuint(bits)?;
        let sign = -((value & 1) as i64);
        Ok(((value >> 1) as i64) ^ sign)
    }

    pub fn count(&mut self, limit: u64) -> Result<usize, WireError> {
        let value = self.varuint(32)?;
        if value > limit {
            return Err(WireError::new("Collection exceeds limit"));
        }
        usize::try_from(value).map_err(|_| WireError::new("Collection exceeds platform limit"))
    }

    pub fn string(&mut self, limit: u64) -> Result<String, WireError> {
        let size = self.count(limit)?;
        let bytes = self.take(size)?;
        std::str::from_utf8(bytes)
            .map(str::to_owned)
            .map_err(|_| WireError::new("Invalid UTF-8 string"))
    }

    pub fn finish(&self) -> Result<(), WireError> {
        if self.remaining() != 0 {
            return Err(WireError::new(format!(
                "Unexpected trailing data at offset {}",
                self.pos
            )));
        }
        Ok(())
    }
}

/// Decode the framed, exact-size Zstandard stream used by the wire reader.
pub fn decompress_stream(reader: &mut Reader<'_>, limit: usize) -> Result<Vec<u8>, WireError> {
    let compressed_len = usize::try_from(reader.u32()?)
        .map_err(|_| WireError::new("Compressed stream exceeds limit"))?;
    let expanded_len = usize::try_from(reader.u32()?)
        .map_err(|_| WireError::new("Compressed stream exceeds limit"))?;
    if compressed_len > limit || expanded_len > limit {
        return Err(WireError::new("Compressed stream exceeds limit"));
    }
    let compressed = reader.take(compressed_len)?;
    decompress_bytes(compressed, expanded_len, limit)
}

/// Decode one frame, rejecting trailing frames and bounding the zstd window/output.
pub fn decompress_bytes(data: &[u8], expanded: usize, limit: usize) -> Result<Vec<u8>, WireError> {
    if data.len() > limit || expanded > limit {
        return Err(WireError::new("Compressed stream exceeds limit"));
    }
    let frame_size = zstd::zstd_safe::find_frame_compressed_size(data)
        .map_err(|e| WireError::new(format!("Invalid Zstandard frame: {e:?}")))?;
    if frame_size != data.len() {
        return Err(WireError::new(
            "Unexpected trailing data after compressed frame",
        ));
    }
    match zstd::zstd_safe::get_frame_content_size(data) {
        Ok(Some(size)) if size != expanded as u64 => {
            return Err(WireError::new("Compression size header mismatch"))
        }
        Ok(None) | Ok(Some(_)) => {}
        Err(_) => return Err(WireError::new("Invalid Zstandard frame size header")),
    }
    let mut decoder = zstd::stream::read::Decoder::new(data)?;
    let window_log = if limit <= 1 {
        0
    } else {
        usize::BITS - (limit - 1).leading_zeros()
    };
    if window_log > 0 {
        decoder.window_log_max(window_log)?;
    }
    let mut decoder = decoder.single_frame();
    let mut output = Vec::with_capacity(expanded.min(limit));
    decoder
        .by_ref()
        .take((expanded as u64).saturating_add(1))
        .read_to_end(&mut output)?;
    if output.len() != expanded {
        return Err(WireError::new("Decompressed length mismatch"));
    }
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn encode_varuint(mut value: u64) -> Vec<u8> {
        let mut bytes = Vec::new();
        loop {
            let mut byte = (value & 0x7f) as u8;
            value >>= 7;
            if value != 0 {
                byte |= 0x80;
            }
            bytes.push(byte);
            if value == 0 {
                return bytes;
            }
        }
    }

    #[test]
    fn reads_little_endian_fields_and_zigzag() {
        let mut reader = Reader::new(&[0x34, 0x12, 0, 0, 0, 0, 0x7f]);
        assert_eq!(reader.u16().unwrap(), 0x1234);
        assert_eq!(reader.f32().unwrap(), 0.0);
        assert_eq!(reader.signed(8).unwrap(), -64);
        reader.finish().unwrap();
    }

    #[test]
    fn reads_signed_little_endian_fields_and_rejects_truncation() {
        let mut reader = Reader::new(&[0x80, 0x00, 0x00, 0x00, 0x80]);
        assert_eq!(reader.i8().unwrap(), i8::MIN);
        assert_eq!(reader.i32().unwrap(), i32::MIN);
        reader.finish().unwrap();
        assert!(Reader::new(&[]).i8().is_err());
        assert!(Reader::new(&[1, 2, 3]).i32().is_err());
    }

    #[test]
    fn variable_integers_cover_width_edges_and_reject_overflow() {
        for value in [0, 127, 128, 16_384, u32::MAX as u64, u64::MAX] {
            assert_eq!(
                Reader::new(&encode_varuint(value))
                    .varuint(if value > u32::MAX as u64 { 64 } else { 32 })
                    .unwrap(),
                value
            );
        }
        assert!(Reader::new(&[0x80, 0x00]).varuint(32).is_ok()); // non-canonical forms are accepted by the Python reader
        assert!(Reader::new(&[0xff; 5]).varuint(32).is_err());
        assert!(Reader::new(&[0x80; 10]).varuint(64).is_err());
        assert!(Reader::new(&[0x80]).varuint(64).is_err());
    }

    #[test]
    fn truncation_does_not_advance_cursor_and_finish_rejects_trailing() {
        let mut reader = Reader::new(b"abc");
        assert!(reader.take(4).is_err());
        assert_eq!(reader.position(), 0);
        assert!(reader.finish().is_err());
        assert_eq!(Reader::new(b"").finish(), Ok(()));
    }

    #[test]
    fn zstd_size_bounds_and_extra_frames() {
        let raw = b"world fixture".repeat(100);
        let compressed = zstd::bulk::compress(&raw, 1).unwrap();
        let mut framed = Vec::new();
        framed.extend_from_slice(&(compressed.len() as u32).to_le_bytes());
        framed.extend_from_slice(&(raw.len() as u32).to_le_bytes());
        framed.extend_from_slice(&compressed);
        let mut reader = Reader::new(&framed);
        assert_eq!(decompress_stream(&mut reader, 1 << 20).unwrap(), raw);
        reader.finish().unwrap();

        let mut bad = framed.clone();
        bad[4..8].copy_from_slice(&((raw.len() - 1) as u32).to_le_bytes());
        assert!(decompress_stream(&mut Reader::new(&bad), 1 << 20).is_err());
        assert!(decompress_stream(&mut Reader::new(&framed), 8).is_err());

        let mut doubled = compressed.clone();
        doubled.extend_from_slice(&compressed);
        assert!(decompress_bytes(&doubled, raw.len(), 1 << 20).is_err());
    }
}
