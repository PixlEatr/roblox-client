//! keep one copy of each shared payload.
//! if it compresses well, only keep the zstd bytes and unpack them when needed.
//! the wire digest and logical length always refer to the original raw bytes.
use blake2::{digest::consts::U16, Blake2b, Digest};
use crate::property_storage::StoredBytes;
use std::{borrow::Cow, cell::RefCell, sync::Arc};

// reuse a synthetic vocabulary for repeated property names and value shapes.
// this is only for our storage, never a wire dictionary or captured session data.
const PROPERTY_DICTIONARY: &[u8] = include_bytes!("property_dictionary.jsonl");

thread_local! {
    // Shared with PackedValue so the client-core does not retain separate
    // zstd contexts for property JSON and shared binary strings.
    static COMPRESSOR: RefCell<Option<zstd::bulk::Compressor<'static>>> = const { RefCell::new(None) };
    static DECOMPRESSOR: RefCell<Option<zstd::bulk::Decompressor<'static>>> = const { RefCell::new(None) };
}

#[derive(Debug)]
enum Storage {
    Raw(StoredBytes),
    Zstd(StoredBytes),
}

/// Shared-string data stored once across the replication decoder and world.
/// `len` and `digest` describe the logical raw payload, even when `storage`
/// contains compressed bytes.
#[derive(Debug)]
pub struct SharedPayload {
    digest: [u8; 16],
    len: usize,
    storage: Storage,
}

impl PartialEq for SharedPayload {
    fn eq(&self, other: &Self) -> bool {
        self.digest == other.digest
            && self.len == other.len
            && self.raw().as_ref() == other.raw().as_ref()
    }
}

impl Eq for SharedPayload {}

impl SharedPayload {
    /// Take ownership of raw bytes and retain their compressed form only when
    /// it saves at least 16 bytes. The input representation is never retained
    /// alongside a successful compressed result.
    pub fn from_raw(raw: Vec<u8>) -> Arc<Self> {
        let digest = digest_raw(&raw);
        let len = raw.len();
        let compressed = if len >= 256 {
            compress_bytes(&raw).filter(|bytes| bytes.len().saturating_add(16) < len)
        } else {
            None
        };
        let storage = match compressed {
            Some(bytes) => Storage::Zstd(StoredBytes::new(bytes)),
            None => Storage::Raw(StoredBytes::new(raw)),
        };
        Arc::new(Self {
            digest,
            len,
            storage,
        })
    }

    pub fn digest(&self) -> [u8; 16] {
        self.digest
    }

    /// Logical, uncompressed byte length.
    pub fn len(&self) -> usize {
        self.len
    }

    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    /// Bytes held by the retained payload representation, excluding struct
    /// and allocator overhead.
    pub fn stored_bytes(&self) -> usize {
        match &self.storage {
            Storage::Raw(bytes) | Storage::Zstd(bytes) => bytes.len(),
        }
    }

    /// Borrow raw payload bytes or decompress into a transient owned buffer.
    /// The caller must not persist the returned `Cow` as a mirror of storage.
    pub fn raw(&self) -> Cow<'_, [u8]> {
        match &self.storage {
            Storage::Raw(bytes) => bytes.read(),
            Storage::Zstd(bytes) => Cow::Owned(decompress_bytes(&bytes.read(), self.len)),
        }
    }

    /// Compare the logical bytes, including decompression when the retained
    /// representation is zstd. Used after digest lookup to guard collisions.
    pub fn matches_exact(&self, raw: &[u8]) -> bool {
        self.len == raw.len() && self.raw().as_ref() == raw
    }

    pub fn is_compressed(&self) -> bool {
        matches!(&self.storage, Storage::Zstd(_))
    }
}

pub(crate) fn digest_raw(raw: &[u8]) -> [u8; 16] {
    type Blake2b128 = Blake2b<U16>;
    let digest = Blake2b128::digest(raw);
    let mut result = [0; 16];
    result.copy_from_slice(&digest);
    result
}

pub(crate) fn compress_bytes(raw: &[u8]) -> Option<Vec<u8>> {
    COMPRESSOR.with(|cell| {
        let mut context = cell.borrow_mut();
        if context.is_none() {
            *context = zstd::bulk::Compressor::with_dictionary(1, PROPERTY_DICTIONARY).ok();
        }
        context
            .as_mut()
            .and_then(|context| context.compress(raw).ok())
    })
}

pub(crate) fn decompress_bytes(bytes: &[u8], expanded: usize) -> Vec<u8> {
    DECOMPRESSOR.with(|cell| {
        let mut context = cell.borrow_mut();
        let context = context.get_or_insert_with(|| {
            zstd::bulk::Decompressor::with_dictionary(PROPERTY_DICTIONARY)
                .expect("zstd decompressor allocation")
        });
        let raw = context
            .decompress(bytes, expanded)
            .expect("internally compressed client-core payload");
        assert_eq!(raw.len(), expanded, "internal client-core payload size");
        raw
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dictionary_round_trip_is_exact_across_owner_threads() {
        let mut bytes = br#"{"Anchored":false,"CFrame":{"position":[12.345678901234567,-0.0,999.0],"rotation_id":2},"size":{"$vector3":[1.0,2.0,3.0]}}"#.to_vec();
        bytes.extend_from_slice(&[0, 255, 128, 0, 10]);
        let expected=bytes.clone();
        let packed=compress_bytes(&bytes).unwrap();
        std::thread::spawn(move || {
            assert_eq!(decompress_bytes(&packed,expected.len()),expected);
        }).join().unwrap();
    }

    #[test]
    fn raw_and_compressed_payloads_keep_logical_digest_and_length() {
        let raw: Vec<u8> = (0..32).collect();
        let payload = SharedPayload::from_raw(raw.clone());
        assert!(!payload.is_compressed());
        assert_eq!(payload.raw().as_ref(), raw);
        assert_eq!(payload.digest(), digest_raw(&raw));
        assert_eq!(payload.len(), raw.len());
        assert_eq!(payload.stored_bytes(), raw.len());

        let repeated = vec![0x5a; 16 * 1024];
        let compressed = SharedPayload::from_raw(repeated.clone());
        assert!(compressed.is_compressed());
        assert!(compressed.stored_bytes() < compressed.len());
        assert_eq!(compressed.raw().as_ref(), repeated);
        assert_eq!(compressed.digest(), digest_raw(&repeated));
        assert!(compressed.matches_exact(&repeated));
        assert!(!compressed.matches_exact(b"different"));
        let mut same_length_wrong = repeated.clone();
        same_length_wrong[0] ^= 1;
        assert_eq!(same_length_wrong.len(), compressed.len());
        assert!(!compressed.matches_exact(&same_length_wrong));

        let mut seed = 0x1234_5678u32;
        let incompressible = (0..4096)
            .map(|_| {
                seed ^= seed << 13;
                seed ^= seed >> 17;
                seed ^= seed << 5;
                seed as u8
            })
            .collect::<Vec<_>>();
        let raw_fallback = SharedPayload::from_raw(incompressible.clone());
        assert!(!raw_fallback.is_compressed());
        assert_eq!(raw_fallback.stored_bytes(), incompressible.len());
        assert_eq!(raw_fallback.raw().as_ref(), incompressible);

        let cloned = Arc::clone(&compressed);
        assert!(Arc::ptr_eq(&compressed, &cloned));
    }
}
