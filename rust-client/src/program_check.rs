//! Native packet-155 framing, RSB1 integrity and genuine V1 signature
//! verification. ProgramAuth runs only verified code in the official VM with
//! its allocation's issued context. It returns the real numeric result and
//! never supplies an expected answer or another session's cached response.
use crate::login::ticket_hash;
use crate::session_integrity::SessionIntegrityObserver;
use rove_client_core::wire::decompress_bytes;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{fmt, fs, io::Read, path::Path};

const MAX_PROGRAM: usize = 128 * 1024;
const TRAILER: usize = 24;
const COEFFICIENTS: usize = 8257;
const TABLE_HASH: &str = "0b6cdf37e5817b29638fe705319618df725ffd2aafc0491d768ab6f162f42c3c";
const SOURCE_HASH: &str = "b6b2184109237a8fea5de33d3265ac026cda3db05dbe3d66f749c91b4297b870";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ProgramError(&'static str);
impl fmt::Display for ProgramError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.0)
    }
}
impl std::error::Error for ProgramError {}
type Result<T> = std::result::Result<T, ProgramError>;

pub struct ProgramCheck<'a> {
    first: u32,
    second: u32,
    program: &'a [u8],
}
impl<'a> ProgramCheck<'a> {
    pub fn parse(packet: &'a [u8]) -> Result<Self> {
        if packet.len() < 13 || packet[0] != 155 {
            return Err(ProgramError("invalid-program-check-header"));
        }
        let word = |at| u32::from_le_bytes(packet[at..at + 4].try_into().expect("checked header"));
        let length = word(9) as usize;
        if !(1..=MAX_PROGRAM).contains(&length) || packet.len() != 13 + length {
            return Err(ProgramError("invalid-program-check-size"));
        }
        Ok(Self {
            first: word(1),
            second: word(5),
            program: &packet[13..],
        })
    }
    pub fn arguments(&self) -> [u32; 2] {
        [self.second, self.first]
    }
    pub fn verify(&self, trust: &PublicTrust) -> Result<VerifiedProgram> {
        self.verify_with_integrity(trust, None)
    }
    fn verify_with_integrity(
        &self,
        trust: &PublicTrust,
        observer: Option<&SessionIntegrityObserver>,
    ) -> Result<VerifiedProgram> {
        let original = unpack_container_observed(self.program, observer)?;
        trust.verify(&original)?;
        Ok(VerifiedProgram {
            original,
            arguments: self.arguments(),
        })
    }
}

/// Only a successful cryptographic verification can construct this value.
/// Its original encoded body and complete signature trailer stay unchanged.
pub struct VerifiedProgram {
    original: Vec<u8>,
    arguments: [u32; 2],
}
impl VerifiedProgram {
    pub fn original_bytes(&self) -> &[u8] {
        &self.original
    }
    pub fn arguments(&self) -> [u32; 2] {
        self.arguments
    }
    pub fn summary(&self) -> Value {
        json!({"signature_verified":true,"signature_version":1,"body_bytes":self.original.len()-TRAILER,
            "trailer_bytes":TRAILER,"program_executed":false,"reply_sent":false})
    }
}

pub struct PublicTrust {
    coefficients: Box<[u128]>,
}
impl PublicTrust {
    pub fn from_files(table_path: &Path, provenance_path: &Path) -> Result<Self> {
        let info =
            fs::metadata(table_path).map_err(|_| ProgramError("public-trust-asset-unavailable"))?;
        let provenance_info = fs::metadata(provenance_path)
            .map_err(|_| ProgramError("public-trust-asset-unavailable"))?;
        if !info.is_file()
            || info.len() != (COEFFICIENTS * 12) as u64
            || !provenance_info.is_file()
            || provenance_info.len() > 65536
        {
            return Err(ProgramError("public-trust-provenance-mismatch"));
        }
        let bounded_read = |path: &Path, max: usize| -> Result<Vec<u8>> {
            let mut bytes = Vec::new();
            fs::File::open(path)
                .map_err(|_| ProgramError("public-trust-asset-unavailable"))?
                .take(max as u64 + 1)
                .read_to_end(&mut bytes)
                .map_err(|_| ProgramError("public-trust-asset-unavailable"))?;
            if bytes.len() > max {
                return Err(ProgramError("public-trust-provenance-mismatch"));
            }
            Ok(bytes)
        };
        let raw = bounded_read(table_path, COEFFICIENTS * 12)?;
        let provenance: Value = serde_json::from_slice(&bounded_read(provenance_path, 65536)?)
            .map_err(|_| ProgramError("public-trust-provenance-mismatch"))?;
        Self::from_pinned_bytes(&raw, &provenance)
    }
    fn from_pinned_bytes(raw: &[u8], provenance: &Value) -> Result<Self> {
        if raw.len() != COEFFICIENTS * 12
            || format!("{:x}", Sha256::digest(raw)) != TABLE_HASH
            || provenance["table_sha256"] != TABLE_HASH
            || provenance["source_binary_sha256"] != SOURCE_HASH
        {
            return Err(ProgramError("public-trust-provenance-mismatch"));
        }
        let coefficients = raw
            .chunks_exact(12)
            .map(|bytes| {
                let mut expanded = [0; 16];
                expanded[..12].copy_from_slice(bytes);
                u128::from_le_bytes(expanded)
            })
            .collect::<Vec<_>>()
            .into_boxed_slice();
        Ok(Self { coefficients })
    }
    pub fn verify(&self, original: &[u8]) -> Result<()> {
        if !(0x118..=MAX_PROGRAM).contains(&original.len()) {
            return Err(ProgramError("signed-program-size-outside-bounds"));
        }
        if original[0] == 0 {
            return Err(ProgramError("invalid-bytecode-header"));
        }
        let (body, trailer) = original.split_at(original.len() - TRAILER);
        let version = [
            trailer[0] ^ trailer[20],
            trailer[1] ^ trailer[21],
            trailer[2] ^ trailer[22],
            trailer[3] ^ trailer[23],
        ];
        if version[..3] != [0x32, 0xc4, 0x6a] {
            return Err(ProgramError("unrecognized-signature-envelope"));
        }
        if version[3] != 1 {
            return Err(ProgramError("unsupported-signature-version"));
        }
        let word = |at| u32::from_le_bytes(trailer[at..at + 4].try_into().expect("fixed trailer"));
        if word(0) ^ word(16) != 0x74171870 {
            return Err(ProgramError("signature-envelope-tag-mismatch"));
        }
        let observed = evaluate_public_map(&trailer[..16], &self.coefficients)?;
        let digest = blake3::hash(body);
        if observed
            .iter()
            .zip(&digest.as_bytes()[4..16])
            .fold(0u8, |sum, (a, b)| sum | (a ^ b))
            != 0
        {
            return Err(ProgramError("signature-verification-failed"));
        }
        Ok(())
    }
}

/// One live allocation owns one handler and one bounded duplicate cache.
/// No process-global or cross-session program answers are retained.
pub struct ProgramAuth {
    job_id: String,
    trust: PublicTrust,
    cache: Vec<([u8; 32], Vec<u8>)>,
    verified: u64,
    executed: u64,
    prepared: u64,
    duplicates: u64,
    last_format: Option<Value>,
    last_runtime_integrity: Option<Value>,
    integrity: Option<SessionIntegrityObserver>,
}
impl ProgramAuth {
    pub fn for_allocation(
        allocation: &crate::allocation::IssuedJoinAllocation,
        trust: PublicTrust,
    ) -> Result<Self> {
        let job_id = allocation
            .game_id
            .as_deref()
            .ok_or(ProgramError("issued-job-id-missing"))?;
        if job_id.len() != 36
            || !job_id.bytes().enumerate().all(|(i, b)| {
                if matches!(i, 8 | 13 | 18 | 23) {
                    b == b'-'
                } else {
                    b.is_ascii_hexdigit()
                }
            })
        {
            return Err(ProgramError("issued-job-id-invalid"));
        }
        Ok(Self {
            job_id: job_id.to_owned(),
            trust,
            cache: Vec::new(),
            verified: 0,
            executed: 0,
            prepared: 0,
            duplicates: 0,
            last_format: None,
            last_runtime_integrity: None,
            integrity: None,
        })
    }
    pub fn handle(&mut self, packet: &[u8]) -> Result<Vec<u8>> {
        let check = ProgramCheck::parse(packet)?;
        let key: [u8; 32] = Sha256::digest(packet).into();
        if let Some((_, reply)) = self.cache.iter().find(|(hash, _)| *hash == key) {
            self.duplicates += 1;
            return Ok(reply.clone());
        }
        if self.cache.len() >= 16 {
            return Err(ProgramError("session-program-check-limit-reached"));
        }
        let verified = check.verify_with_integrity(&self.trust, self.integrity.as_ref())?;
        self.verified += 1;
        let (result, format) = crate::native::run_verified_program(&verified, &self.job_id)
            .map_err(|_| ProgramError("verified-program-execution-failed"))?;
        self.executed += 1;
        self.last_format = Some(format);
        // Preserve real VM observations for the live integrity owner. No clean
        // fallback is supplied if the VM omits the observation.
        self.last_runtime_integrity = result.get("runtime_protected_integrity").cloned();
        if let Some(integrity) = self.integrity.as_ref() {
            integrity.observe_oneshot_response(&result);
        }
        if result.get("ok").and_then(Value::as_bool) != Some(true) {
            return Err(ProgramError("verified-program-runtime-failed"));
        }
        let value = result
            .get("values")
            .and_then(Value::as_array)
            .and_then(|v| v.first())
            .and_then(Value::as_f64)
            .ok_or(ProgramError("program-returned-no-number"))?;
        let number = native_unsigned_result(value)?;
        let mut response = vec![155];
        response.extend(check.second.to_le_bytes());
        response.extend(number.to_le_bytes());
        self.cache.push((key, response.clone()));
        self.prepared += 1;
        Ok(response)
    }
    pub fn last_runtime_integrity(&self) -> Option<&Value> {
        self.last_runtime_integrity.as_ref()
    }
    pub fn set_integrity(&mut self, observer: SessionIntegrityObserver) {
        self.integrity = Some(observer);
    }
    pub fn summary(&self) -> Value {
        json!({"signatures_verified":self.verified,"programs_executed":self.executed,
            "responses_prepared":self.prepared,"duplicate_checks":self.duplicates,"last_program_format":self.last_format})
    }
}

fn native_unsigned_result(value: f64) -> Result<u32> {
    if !value.is_finite() {
        return Err(ProgramError("program-returned-nonfinite-number"));
    }
    Ok(value.clamp(0., u32::MAX as f64).trunc() as u32)
}

fn evaluate_public_map(signature: &[u8], coefficients: &[u128]) -> Result<[u8; 12]> {
    if signature.len() != 16 || coefficients.len() != COEFFICIENTS {
        return Err(ProgramError("invalid-public-map-input"));
    }
    let selected: Vec<usize> = (0..128)
        .filter(|bit| signature[bit / 8] & (0x80 >> (bit % 8)) != 0)
        .collect();
    let mut result = coefficients[0];
    for &bit in &selected {
        result ^= coefficients[1 + bit];
    }
    for (position, &left) in selected.iter().enumerate() {
        let row = 129 + 127 * left - left * left.saturating_sub(1) / 2;
        for &right in &selected[position + 1..] {
            result ^= coefficients[row + right - left - 1];
        }
    }
    Ok(result.to_le_bytes()[..12]
        .try_into()
        .expect("fixed public result"))
}

pub fn unpack_container(blob: &[u8]) -> Result<Vec<u8>> {
    unpack_container_observed(blob, None)
}

fn unpack_container_observed(
    blob: &[u8],
    observer: Option<&SessionIntegrityObserver>,
) -> Result<Vec<u8>> {
    if !(9..=MAX_PROGRAM).contains(&blob.len()) {
        return Err(ProgramError("invalid-bytecode-container-size"));
    }
    let key: Vec<u8> = (0..4)
        .map(|i| (blob[i] ^ b"RSB1"[i]).wrapping_sub((i * 41) as u8))
        .collect();
    let raw: Vec<u8> = blob
        .iter()
        .enumerate()
        .map(|(i, b)| b ^ key[i % 4].wrapping_add((i.wrapping_mul(41)) as u8))
        .collect();
    let magic_matches = &raw[..4] == b"RSB1";
    let checksum_matches = magic_matches
        && ticket_hash(&raw, 42) == u32::from_le_bytes(key[..].try_into().expect("four-byte key"));
    if let Some(observer) = observer {
        observer.observe_container_comparison(checksum_matches);
    }
    if !checksum_matches {
        return Err(ProgramError("bytecode-container-checksum-mismatch"));
    }
    let expanded =
        u32::from_le_bytes(raw[4..8].try_into().expect("checked container header")) as usize;
    if expanded == 0 {
        return Ok(raw[8..].to_vec());
    }
    if expanded > MAX_PROGRAM {
        return Err(ProgramError("bytecode-container-expansion-exceeds-limit"));
    }
    decompress_bytes(&raw[8..], expanded, MAX_PROGRAM)
        .map_err(|_| ProgramError("invalid-compressed-bytecode-frame"))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn numeric_return_matches_finite_native_saturation() {
        assert_eq!(native_unsigned_result(-1.).unwrap(), 0);
        assert_eq!(native_unsigned_result(1.75).unwrap(), 1);
        assert_eq!(native_unsigned_result(1e99).unwrap(), u32::MAX);
        assert!(native_unsigned_result(f64::NAN).is_err());
        assert!(native_unsigned_result(f64::INFINITY).is_err());
    }
    #[test]
    fn authentication_requires_issued_job_and_never_executes_unsigned_input() {
        let trust = || PublicTrust {
            coefficients: vec![0; COEFFICIENTS].into_boxed_slice(),
        };
        let mut allocation = crate::allocation::test_issued_allocation();
        assert!(ProgramAuth::for_allocation(&allocation, trust()).is_err());
        allocation.game_id = Some("00000000-0000-4000-8000-000000000001".into());
        let mut handler = ProgramAuth::for_allocation(&allocation, trust()).unwrap();
        let mut packet = vec![155];
        packet.extend([0; 8]);
        packet.extend(9u32.to_le_bytes());
        packet.extend([0; 9]);
        assert!(handler.handle(&packet).is_err());
        assert_eq!(handler.summary()["programs_executed"], 0);
        assert_eq!(handler.summary()["responses_prepared"], 0);
        assert_eq!(handler.last_runtime_integrity(), None);
    }
    #[test]
    fn framing_preserves_argument_order_and_rejects_partial_or_extra_bytes() {
        let mut packet = vec![155];
        packet.extend(7u32.to_le_bytes());
        packet.extend(11u32.to_le_bytes());
        packet.extend(1u32.to_le_bytes());
        packet.push(1);
        assert_eq!(ProgramCheck::parse(&packet).unwrap().arguments(), [11, 7]);
        for size in 0..packet.len() {
            assert!(ProgramCheck::parse(&packet[..size]).is_err());
        }
        packet.push(0);
        assert!(ProgramCheck::parse(&packet).is_err());
    }
    #[test]
    fn rsb_uncompressed_roundtrip_and_corruption_failure() {
        let body = b"owned nonexecutable container fixture";
        let mut raw = b"RSB1\0\0\0\0".to_vec();
        raw.extend(body);
        let key = ticket_hash(&raw, 42).to_le_bytes();
        let mut blob: Vec<_> = raw
            .iter()
            .enumerate()
            .map(|(i, b)| b ^ key[i % 4].wrapping_add((i * 41) as u8))
            .collect();
        assert_eq!(unpack_container(&blob).unwrap(), body);
        *blob.last_mut().unwrap() ^= 1;
        assert_eq!(
            unpack_container(&blob).unwrap_err().0,
            "bytecode-container-checksum-mismatch"
        );
    }
    #[test]
    fn checksum_observer_counts_only_the_actual_container_comparison() {
        let body = b"fixture";
        let mut raw = b"RSB1\0\0\0\0".to_vec();
        raw.extend(body);
        let key = ticket_hash(&raw, 42).to_le_bytes();
        let mut blob: Vec<_> = raw
            .iter()
            .enumerate()
            .map(|(i, b)| b ^ key[i % 4].wrapping_add((i * 41) as u8))
            .collect();
        let observer = SessionIntegrityObserver::new();
        assert!(unpack_container_observed(&blob, Some(&observer)).is_ok());
        assert_eq!(observer.report()["checksum_comparisons"], 1);
        assert_eq!(observer.report()["observed_failure_flags"], 0);
        *blob.last_mut().unwrap() ^= 1;
        assert!(unpack_container_observed(&blob, Some(&observer)).is_err());
        assert_eq!(observer.report()["checksum_comparisons"], 2);
        assert_eq!(observer.report()["checksum_failures"], 1);
        assert_eq!(observer.report()["observed_failure_flags"], 0x40);
        assert!(unpack_container_observed(&[1, 2], Some(&observer)).is_err());
        assert_eq!(observer.report()["checksum_comparisons"], 2);
    }
    #[test]
    fn public_map_uses_msb_first_bits_and_all_pairs() {
        let values: Vec<u128> = (0..COEFFICIENTS).map(|v| v as u128).collect();
        assert_eq!(evaluate_public_map(&[0; 16], &values).unwrap(), [0; 12]);
        let mut signature = [0; 16];
        signature[0] = 0x80;
        signature[15] = 1;
        let expected = (1u128 ^ 128 ^ 255).to_le_bytes();
        assert_eq!(
            evaluate_public_map(&signature, &values).unwrap(),
            expected[..12]
        );
    }
    #[test]
    fn modified_trust_assets_and_unsigned_code_are_rejected() {
        assert!(PublicTrust::from_pinned_bytes(
            &vec![0; COEFFICIENTS * 12],
            &json!({"table_sha256":TABLE_HASH,"source_binary_sha256":SOURCE_HASH})
        )
        .is_err());
        let trust = PublicTrust {
            coefficients: vec![0; COEFFICIENTS].into_boxed_slice(),
        };
        assert!(trust.verify(&[1; 0x118]).is_err());
        let mut original = vec![1; 0x118];
        let at = original.len() - TRAILER;
        original[at..].fill(0);
        original[at + 16..at + 20].copy_from_slice(&0x74171870u32.to_le_bytes());
        original[at + 20..].copy_from_slice(&[0x32, 0xc4, 0x6a, 1]);
        assert_eq!(
            trust.verify(&original).unwrap_err().0,
            "signature-verification-failed"
        );
    }
}
