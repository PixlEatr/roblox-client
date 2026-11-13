//! Normal server-issued game/login packet codecs shared with the Rust client.
//!
//! Opaque credentials are accepted only from the issued join response and
//! passed through unchanged. This module does not answer integrity challenges.

use base64::{engine::general_purpose::STANDARD, Engine as _};
use serde_json::Value;
use std::{fmt, str};

pub const MAX_FRAME: usize = 8 * 1024 * 1024;
const BUILD_SEED: u32 = 1_966_058_251;
const VERSION: u32 = 36;
const SUPPORTED_BYTECODE: &[u8] = &[6, 9, 13, 14];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LoginCodecError(&'static str);

impl LoginCodecError {
    fn new(message: &'static str) -> Self {
        Self(message)
    }
}

impl fmt::Display for LoginCodecError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.0)
    }
}

impl std::error::Error for LoginCodecError {}

/// Encode an unsigned network BitStream integer (LEB128), not a QUIC varint.
pub fn encode_unsigned(mut value: u64) -> Result<Vec<u8>, LoginCodecError> {
    let mut encoded = Vec::with_capacity(10);
    while value > 0x7f {
        encoded.push(((value as u8) & 0x7f) | 0x80);
        value >>= 7;
    }
    encoded.push(value as u8);
    Ok(encoded)
}

pub fn encode_signed(value: i64) -> Vec<u8> {
    let zigzag = ((value as u64) << 1) ^ ((value >> 63) as u64);
    // Every u64 has a valid LEB128 representation.
    encode_unsigned(zigzag).expect("u64 is encodable")
}

pub fn encode_string(value: &str) -> Result<Vec<u8>, LoginCodecError> {
    let bytes = value.as_bytes();
    if bytes.len() > MAX_FRAME {
        return Err(LoginCodecError::new(
            "Network string exceeds the configured limit",
        ));
    }
    let mut encoded = encode_unsigned(bytes.len() as u64)?;
    encoded.reserve(bytes.len());
    encoded.extend_from_slice(bytes);
    Ok(encoded)
}

/// Serialize the two opaque blobs in the server-issued join ticket.
pub fn issued_early_auth(ticket: &str) -> Result<Vec<u8>, LoginCodecError> {
    if ticket.len() > MAX_FRAME {
        return Err(LoginCodecError::new(
            "Issued authentication ticket is too large",
        ));
    }
    let mut fields = ticket.split(';');
    let Some(_field0) = fields.next() else {
        return Err(LoginCodecError::new(
            "Unrecognized issued join ticket structure",
        ));
    };
    let Some(_field1) = fields.next() else {
        return Err(LoginCodecError::new(
            "Unrecognized issued join ticket structure",
        ));
    };
    let first_b64 = fields.next().ok_or(LoginCodecError::new(
        "Unrecognized issued join ticket structure",
    ))?;
    let second_b64 = fields.next().ok_or(LoginCodecError::new(
        "Unrecognized issued join ticket structure",
    ))?;
    let version_text = fields.next().ok_or(LoginCodecError::new(
        "Unrecognized issued join ticket structure",
    ))?;
    if fields.next().is_some()
        || version_text.is_empty()
        || !version_text.bytes().all(|byte| byte.is_ascii_digit())
    {
        return Err(LoginCodecError::new(
            "Unrecognized issued join ticket structure",
        ));
    }
    let version: u16 = version_text
        .parse()
        .map_err(|_| LoginCodecError::new("Invalid issued authentication field encoding"))?;
    if version > u8::MAX as u16 {
        return Err(LoginCodecError::new(
            "Unsupported issued authentication field sizes",
        ));
    }
    let first = STANDARD
        .decode(first_b64)
        .map_err(|_| LoginCodecError::new("Invalid issued authentication field encoding"))?;
    let second = STANDARD
        .decode(second_b64)
        .map_err(|_| LoginCodecError::new("Invalid issued authentication field encoding"))?;
    if !matches!(first.len(), 32 | 33 | 50) || !(1..=u8::MAX as usize).contains(&second.len()) {
        return Err(LoginCodecError::new(
            "Unsupported issued authentication field sizes",
        ));
    }
    let mut packet = Vec::with_capacity(first.len() + second.len() + 4);
    packet.extend_from_slice(&[0xa8, version as u8, first.len() as u8]);
    packet.extend_from_slice(&first);
    packet.push(second.len() as u8);
    packet.extend_from_slice(&second);
    Ok(packet)
}

/// Parse the ordinary server feature-name/value packet, preserving wire order.
pub fn feature_configuration(payload: &[u8]) -> Result<Vec<(String, String)>, LoginCodecError> {
    if payload.len() < 4 || payload.len() > MAX_FRAME || payload[..2] != [0x93, 0] {
        return Err(LoginCodecError::new(
            "Unrecognized feature-configuration packet",
        ));
    }
    let count = u16::from_le_bytes([payload[2], payload[3]]) as usize;
    let mut offset = 4usize;
    let mut entries = Vec::with_capacity(count.min(payload.len() / 4));
    for _ in 0..count {
        let name = read_config_string(payload, &mut offset)?;
        let value = read_config_string(payload, &mut offset)?;
        if entries.iter().any(|(existing, _)| existing == &name) {
            return Err(LoginCodecError::new("Duplicate feature-configuration name"));
        }
        entries.push((name, value));
    }
    if offset != payload.len() {
        return Err(LoginCodecError::new("Trailing feature-configuration bytes"));
    }
    Ok(entries)
}

fn read_config_string(payload: &[u8], offset: &mut usize) -> Result<String, LoginCodecError> {
    let end = offset.checked_add(2).ok_or(LoginCodecError::new(
        "Truncated feature-configuration length",
    ))?;
    if end > payload.len() {
        return Err(LoginCodecError::new(
            "Truncated feature-configuration length",
        ));
    }
    let length = u16::from_le_bytes([payload[*offset], payload[*offset + 1]]) as usize;
    *offset = end;
    let end = offset.checked_add(length).ok_or(LoginCodecError::new(
        "Truncated feature-configuration value",
    ))?;
    if end > payload.len() {
        return Err(LoginCodecError::new(
            "Truncated feature-configuration value",
        ));
    }
    let text = str::from_utf8(&payload[*offset..end])
        .map_err(|_| LoginCodecError::new("Feature-configuration value is not UTF-8"))?;
    *offset = end;
    Ok(text.to_owned())
}

const ISSUED_JOIN_FIELDS: &[&str] = &[
    "UserId",
    "UserName",
    "DisplayName",
    "HasVerifiedBadge",
    "PartyId",
    "RandomSeed1",
    "VerifiedAMP",
    "PrivateServerID",
    "PrivateServerOwnerID",
    "DomainUserId",
    "UserIdMode",
    "ServerClaimedTime",
    "APIsecurityToken",
    "Rm3Tier0CadenceSeconds",
    "ServerDMRecording",
    "SuperSafeChat",
    "MembershipType",
    "HasRobloxSubscription",
    "AgeChecked",
    "AccountAge",
    "WhoCanWhisperChatWithMeInExperiences",
    "GameChatType",
    "CharacterAppearance",
    "FollowUserId",
    "MatchmakingAttributes",
    "ShowRobloxTranslations",
    "TranslationDisplayMode",
    "ImageTranslationContentVariantType",
];

/// Keep only issued fields needed by packets 138/144, rather than retaining
/// the whole HTTPS allocation document and its unrelated transport metadata.
pub(crate) fn retain_issued_game_fields(join: &serde_json::Map<String, Value>) -> Value {
    let mut fields=serde_json::Map::new();
    for key in ISSUED_JOIN_FIELDS.iter().copied().chain([
        "characterAppearanceId","IsUnknownOrUnder13","CountryCode","RobloxLocale","UgcLocale",
        "RawJoinData","ClientJoinData","GameJoinContext","ClientTicket","SessionId",
    ]) {
        if let Some(value)=join.get(key) {fields.insert(key.to_owned(),value.clone());}
    }
    Value::Object(fields)
}

/// Build the native packet-144 fields from data in the current issued join.
/// The ordered vector retains native top-level property order.
pub fn player_join_info(
    join: &Value,
    join_ticket: Option<&Value>,
) -> Result<Vec<(String, Value)>, LoginCodecError> {
    let source = join.as_object().ok_or(LoginCodecError::new(
        "Issued join data must be a JSON object",
    ))?;
    let mut result: Vec<(String, Value)> = ISSUED_JOIN_FIELDS
        .iter()
        .filter_map(|key| {
            source
                .get(*key)
                .map(|value| ((*key).to_owned(), value.clone()))
        })
        .collect();
    for (target, source_key) in [
        ("CharacterAppearanceId", "characterAppearanceId"),
        ("IsUnder13", "IsUnknownOrUnder13"),
        ("CountryRegionCode", "CountryCode"),
        ("LocaleId", "RobloxLocale"),
        ("LocaleIdInternal", "UgcLocale"),
    ] {
        if let Some(value) = source.get(source_key) {
            insert_or_replace(&mut result, target, value.clone());
        }
    }
    for (key, value) in [
        ("IsTeleportedIn", Value::Bool(false)),
        ("VrDevice", Value::String(String::new())),
        (
            "UserAgentStr",
            Value::String("RobloxProtocolResearch/0.1 (macOS)".to_owned()),
        ),
        ("OsPlatform", Value::String("OSX".to_owned())),
        ("OsPlatformName", Value::String("MacOS".to_owned())),
        ("ClientRobloxChannel", Value::String(String::new())),
    ] {
        insert_or_replace(&mut result, key, value);
    }
    for key in ["RawJoinData", "ClientJoinData", "GameJoinContext"] {
        let value = source
            .get(key)
            .cloned()
            .unwrap_or_else(|| Value::String(String::new()));
        insert_or_replace(&mut result, key, value);
    }
    if let Some((_, age)) = result.iter_mut().find(|(key, _)| key == "AgeChecked") {
        if let Some(integer) = integer_as_string(age) {
            *age = Value::String(integer);
        }
    }
    if let Some(ticket) = join_ticket {
        insert_or_replace(
            &mut result,
            "__joinTicket",
            Value::String(json_ascii(ticket)),
        );
    }
    Ok(result)
}

fn insert_or_replace(fields: &mut Vec<(String, Value)>, name: &str, value: Value) {
    if let Some((_, existing)) = fields.iter_mut().find(|(key, _)| key == name) {
        *existing = value;
    } else {
        fields.push((name.to_owned(), value));
    }
}

fn integer_as_string(value: &Value) -> Option<String> {
    let number = value.as_number()?;
    if let Some(integer) = number.as_i64() {
        Some(integer.to_string())
    } else {
        number.as_u64().map(|integer| integer.to_string())
    }
}

/// Compact Python `json.dumps(..., ensure_ascii=True)` for the embedded ticket.
/// The ticket is placed inside packet 144 as a string, not an object.
fn json_ascii(value: &Value) -> String {
    match value {
        Value::Null | Value::Bool(_) | Value::Number(_) => value.to_string(),
        Value::String(text) => json_string(text, true),
        Value::Array(items) => format!(
            "[{}]",
            items.iter().map(json_ascii).collect::<Vec<_>>().join(",")
        ),
        Value::Object(object) => {
            json_object(object.iter().map(|(key, item)| (key.as_str(), item)), true)
        }
    }
}

fn json_string(value: &str, ensure_ascii: bool) -> String {
    let mut result = String::with_capacity(value.len() + 2);
    result.push('"');
    for character in value.chars() {
        match character {
            '"' => result.push_str("\\\""),
            '\\' => result.push_str("\\\\"),
            '\u{0008}' => result.push_str("\\b"),
            '\u{000c}' => result.push_str("\\f"),
            '\n' => result.push_str("\\n"),
            '\r' => result.push_str("\\r"),
            '\t' => result.push_str("\\t"),
            value if (value as u32) < 0x20 => {
                result.push_str(&format!("\\u{:04x}", value as u32));
            }
            value if ensure_ascii && (value as u32) > 0x7f => {
                let code = value as u32;
                if code <= 0xffff {
                    result.push_str(&format!("\\u{code:04x}"));
                } else {
                    let adjusted = code - 0x1_0000;
                    let high = 0xd800 | (adjusted >> 10);
                    let low = 0xdc00 | (adjusted & 0x3ff);
                    result.push_str(&format!("\\u{high:04x}\\u{low:04x}"));
                }
            }
            value => result.push(value),
        }
    }
    result.push('"');
    result
}

fn json_object<'a>(
    items: impl Iterator<Item = (&'a str, &'a Value)>,
    ensure_ascii: bool,
) -> String {
    let mut result = String::from("{");
    for (index, (key, value)) in items.enumerate() {
        if index != 0 {
            result.push(',');
        }
        result.push_str(&json_string(key, ensure_ascii));
        result.push(':');
        result.push_str(&json_value(value, ensure_ascii));
    }
    result.push('}');
    result
}

fn json_value(value: &Value, ensure_ascii: bool) -> String {
    match value {
        Value::Null | Value::Bool(_) | Value::Number(_) => value.to_string(),
        Value::String(text) => json_string(text, ensure_ascii),
        Value::Array(items) => format!(
            "[{}]",
            items
                .iter()
                .map(|item| json_value(item, ensure_ascii))
                .collect::<Vec<_>>()
                .join(",")
        ),
        Value::Object(object) => json_object(
            object.iter().map(|(key, item)| (key.as_str(), item)),
            ensure_ascii,
        ),
    }
}

/// Packet 144 with server-issued identity plus an optional static-reader range.
pub fn protocol_sync(
    join: &Value,
    join_ticket: Option<&Value>,
    luau_range: Option<(u8, u8)>,
) -> Result<Vec<u8>, LoginCodecError> {
    let info = player_join_info(join, join_ticket)?;
    if let Some((low, high)) = luau_range {
        if low > high || !(low..=high).all(|version| SUPPORTED_BYTECODE.contains(&version)) {
            return Err(LoginCodecError::new(
                "Bytecode range includes a version unsupported by the static reader",
            ));
        }
    }
    let encoded_info = json_object(info.iter().map(|(key, value)| (key.as_str(), value)), false);
    let mut packet = vec![0x90, 0x01];
    packet.extend_from_slice(&VERSION.to_le_bytes());
    if let Some((low, high)) = luau_range {
        packet.extend_from_slice(&[low, high]);
    }
    packet.extend_from_slice(&0u16.to_le_bytes());
    packet.extend_from_slice(&encode_string(&encoded_info)?);
    for constant in [
        0x85cb_1f5e_u32,
        0xbf90_7b98,
        0x8ab1_a79e,
        0x85cb_1f5e,
        0x1149_6016,
        0xcbeffe78,
    ] {
        packet.extend_from_slice(&constant.to_le_bytes());
    }
    Ok(packet)
}

fn rol32(value: u32, count: u32) -> u32 {
    value.rotate_left(count)
}

fn ror32(value: u32, count: u32) -> u32 {
    value.rotate_right(count)
}

/// XXH32 used for the ordinary packet-138 ticket field.
pub fn ticket_hash(data: &[u8], seed: u32) -> u32 {
    const P1: u32 = 0x9e37_79b1;
    const P2: u32 = 0x85eb_ca77;
    const P3: u32 = 0xc2b2_ae3d;
    const P4: u32 = 0x27d4_eb2f;
    const P5: u32 = 0x1656_67b1;

    let mut offset = 0usize;
    let mut hash;
    if data.len() >= 16 {
        let mut lanes = [
            seed.wrapping_add(P1).wrapping_add(P2),
            seed.wrapping_add(P2),
            seed,
            seed.wrapping_sub(P1),
        ];
        while offset <= data.len() - 16 {
            for (lane, bytes) in lanes
                .iter_mut()
                .zip(data[offset..offset + 16].chunks_exact(4))
            {
                let word = u32::from_le_bytes(bytes.try_into().expect("four-byte word"));
                *lane = rol32(lane.wrapping_add(word.wrapping_mul(P2)), 13).wrapping_mul(P1);
            }
            offset += 16;
        }
        hash = rol32(lanes[0], 1)
            .wrapping_add(rol32(lanes[1], 7))
            .wrapping_add(rol32(lanes[2], 12))
            .wrapping_add(rol32(lanes[3], 18));
    } else {
        hash = seed.wrapping_add(P5);
    }
    hash = hash.wrapping_add(data.len() as u32);
    while offset <= data.len().saturating_sub(4) && data.len() - offset >= 4 {
        let word = u32::from_le_bytes(data[offset..offset + 4].try_into().expect("four-byte word"));
        hash = rol32(hash.wrapping_add(word.wrapping_mul(P3)), 17).wrapping_mul(P4);
        offset += 4;
    }
    for byte in &data[offset..] {
        hash = rol32(hash.wrapping_add(u32::from(*byte).wrapping_mul(P5)), 11).wrapping_mul(P1);
    }
    hash ^= hash >> 15;
    hash = hash.wrapping_mul(P2);
    hash ^= hash >> 13;
    hash = hash.wrapping_mul(P3);
    hash ^ (hash >> 16)
}

/// Public client checksum permutation used in packet 138.
pub fn ticket_checksum(mut value: u32) -> u32 {
    let seed = BUILD_SEED;
    let constant = 0x557b_b5d7;
    let flags = seed.wrapping_mul(0x0729_bbaa) & 0xffff;
    value = ror32(
        value.wrapping_add(constant),
        if flags & 2 != 0 { 25 } else { 7 },
    );
    value = value.wrapping_sub(if flags & 4 != 0 { seed } else { constant });
    let factor = if flags & 8 != 0 { seed } else { constant };
    value = ror32(
        value.wrapping_mul(factor),
        if flags & 0x10 != 0 { 19 } else { 13 },
    );
    let left = if flags & 0x20 != 0 { seed } else { constant };
    let right = if flags & 0x40 != 0 { seed } else { constant };
    value = right ^ left.wrapping_sub(value);
    value = ror32(value, if flags & 0x80 != 0 { 15 } else { 17 });
    value = value
        .wrapping_add(if flags & 0x100 != 0 {
            seed
        } else {
            seed.wrapping_neg()
        })
        .wrapping_add(if flags & 0x200 != 0 {
            constant
        } else {
            constant.wrapping_neg()
        });
    value = ror32(value, if flags & 0x400 != 0 { 9 } else { 23 });
    if flags & 0x800 != 0 {
        value = value.wrapping_neg();
    }
    value = seed.wrapping_add(value);
    if flags & 0x1000 != 0 {
        value = value.wrapping_neg();
    }
    value = ror32(
        value.wrapping_add(constant),
        if flags & 0x2000 != 0 { 3 } else { 29 },
    );
    value ^= if flags & 0x4000 != 0 { seed } else { constant };
    if flags & 0x8000 == 0 {
        value = value.wrapping_neg();
    }
    value
}

fn build_identifier() -> String {
    let mut identifier = String::from("2p2q778rnqo4263sp76os47q2s3s6717ro2nsr41")
        .chars()
        .map(|character| match character {
            'a'..='m' => ((character as u8) + 13) as char,
            'n'..='z' => ((character as u8) - 13) as char,
            'A'..='M' => ((character as u8) + 13) as char,
            'N'..='Z' => ((character as u8) - 13) as char,
            other => other,
        })
        .collect::<String>();
    identifier.push('!');
    for shift in [0, 8, 16, 24] {
        for (index, word) in [0x85cb_1f5e_u32, 0xbf90_7b98, 0x8ab1_a79e, 0x6133_538c]
            .iter()
            .enumerate()
        {
            let mask = if index == 0 { 0xdf } else { 0xff };
            identifier.push_str(&format!("{:02x}", (word >> shift) & mask));
        }
    }
    identifier.push('.');
    identifier
}

fn issued_string<'a>(join: &'a Value, field: &'static str) -> Result<&'a str, LoginCodecError> {
    join.as_object()
        .and_then(|object| object.get(field))
        .and_then(Value::as_str)
        .ok_or(LoginCodecError::new(
            "Issued join data is missing a required string",
        ))
}

fn issued_user_id(join: &Value) -> Result<i64, LoginCodecError> {
    let value = join
        .as_object()
        .and_then(|object| object.get("UserId"))
        .ok_or(LoginCodecError::new("Issued join data is missing UserId"))?;
    value.as_i64().ok_or(LoginCodecError::new(
        "Issued UserId must be a signed integer",
    ))
}

/// Packet 138 for the ordinary non-RakNet transport.
pub fn ticket_packet(join: &Value) -> Result<Vec<u8>, LoginCodecError> {
    let user_id = issued_user_id(join)?;
    let ticket = issued_string(join, "ClientTicket")?;
    let session_id = issued_string(join, "SessionId")?;
    let checksum = ticket_checksum(ticket_hash(ticket.as_bytes(), 1));
    let mut packet = vec![0x8a];
    packet.extend_from_slice(&encode_signed(user_id));
    packet.extend_from_slice(&encode_string(ticket)?);
    packet.extend_from_slice(&VERSION.to_le_bytes());
    packet.extend_from_slice(&encode_string(&build_identifier())?);
    packet.extend_from_slice(&encode_string("OSX")?);
    packet.extend_from_slice(&encode_string("?")?);
    packet.extend_from_slice(&encode_unsigned(u64::from(checksum))?);
    packet.extend_from_slice(&encode_unsigned(u64::from(
        checksum.wrapping_sub(195_948_557),
    ))?);
    packet.extend_from_slice(&encode_string("")?);
    packet.extend_from_slice(&encode_string(session_id)?);
    packet.extend_from_slice(&0xc001_cafeu32.to_le_bytes());
    Ok(packet)
}

/// Ordinary non-Studio path sends an empty server ID.
pub fn server_id_packet() -> Vec<u8> {
    let mut packet = vec![0x8f];
    // Empty strings always fit the network string bound.
    packet.extend_from_slice(&[0]);
    packet
}

/// Native packet 146. The nonce is generated afresh from the same OS-backed
/// source as join-attempt UUIDs; its first four bytes have no UUID bit masks.
pub fn verification_packet() -> Result<Vec<u8>, LoginCodecError> {
    let id=crate::allocation::new_join_attempt_id().map_err(|_|LoginCodecError::new("Could not generate verification nonce"))?;
    let nonce=u32::from_str_radix(&id[..8],16).map_err(|_|LoginCodecError::new("Invalid generated verification nonce"))?;
    Ok(verification_with_nonce(nonce))
}
fn verification_with_nonce(nonce:u32)->Vec<u8> {
    let constant=0x1f5e_a79eu32 ^ 0x1149_6016;
    let value=(u64::from(nonce)<<32)|u64::from(nonce^constant);
    let mut packet=vec![146];packet.extend(encode_signed(value as i64));packet
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn fixture() -> Value {
        serde_json::from_str(include_str!(
            "../tests/fixtures/login/python_reference_vectors.json"
        ))
        .expect("valid synthetic Python reference vectors")
    }

    fn unhex(text: &str) -> Vec<u8> {
        text.as_bytes()
            .chunks_exact(2)
            .map(|pair| u8::from_str_radix(str::from_utf8(pair).unwrap(), 16).unwrap())
            .collect()
    }

    #[test]
    fn native_verification_nonce_vectors_match_existing_codec() {
        for (nonce,hex) in [(0,"92909ebee101"),(1,"92929ebee121"),
            (0x80000000,"92efe1c19eeeffffffff01"),(u32::MAX,"92919ebee101")] {
            assert_eq!(verification_with_nonce(nonce),unhex(hex));
        }
        assert_eq!(verification_packet().unwrap()[0],146);
    }

    #[test]
    fn retained_game_fields_preserve_packet_content_without_unrelated_allocation_data() {
        let mut join=json!({"UserId":42,"ClientTicket":"owned-ticket","SessionId":"owned-session",
            "RawJoinData":"issued-data","CountryCode":"US","AgeChecked":1,"NetStackTokenValue":"do-not-retain"});
        join["ExtraLargeUnrelatedObject"]=json!(["ignored"]);
        let compact=retain_issued_game_fields(join.as_object().unwrap());
        assert_eq!(protocol_sync(&join,None,Some((13,14))).unwrap(),protocol_sync(&compact,None,Some((13,14))).unwrap());
        assert_eq!(ticket_packet(&join).unwrap(),ticket_packet(&compact).unwrap());
        assert!(compact.get("NetStackTokenValue").is_none());
        assert!(compact.get("ExtraLargeUnrelatedObject").is_none());
    }

    #[test]
    fn issued_authentication_blobs_are_copied_unchanged() {
        let vector = &fixture()["early_auth"];
        assert_eq!(
            issued_early_auth(vector["ticket"].as_str().unwrap()).unwrap(),
            unhex(vector["packet_hex"].as_str().unwrap())
        );
        for bad in ["not-a-ticket", "a;b;***;YQ==;1", "a;b;YQ==;YQ==;1"] {
            assert!(issued_early_auth(bad).is_err());
        }
    }

    #[test]
    fn server_feature_configuration_preserves_order_and_checks_bounds() {
        let vector = &fixture()["feature_configuration"];
        let packet = unhex(vector["packet_hex"].as_str().unwrap());
        let entries = feature_configuration(&packet).unwrap();
        assert_eq!(entries, vec![("Example".to_owned(), "true".to_owned())]);
        for length in 0..packet.len() {
            assert!(feature_configuration(&packet[..length]).is_err());
        }
        assert!(feature_configuration(&[0x93, 0, 1, 0, 0, 0, b'a', 0, 0, 0]).is_err());
        assert!(feature_configuration(&[0x93, 0, 0, 0, 0]).is_err());
    }

    #[test]
    fn wire_integer_and_string_vectors_match_python() {
        for pair in fixture()["signed_int_vectors"].as_array().unwrap() {
            let value = pair[0].as_i64().unwrap();
            let encoded = unhex(pair[1].as_str().unwrap());
            assert_eq!(encode_signed(value), encoded);
        }
        assert_eq!(encode_unsigned(127).unwrap(), [0x7f]);
        assert_eq!(encode_unsigned(128).unwrap(), [0x80, 0x01]);
        assert_eq!(encode_unsigned(624485).unwrap(), [0xe5, 0x8e, 0x26]);
        assert_eq!(encode_string("é").unwrap(), [2, 0xc3, 0xa9]);
        assert!(encode_string(&"x".repeat(MAX_FRAME + 1)).is_err());
    }

    #[test]
    fn protocol_sync_layout_issued_fields_defaults_and_ranges_match_python() {
        let vector = fixture();
        let join = &vector["protocol_sync"]["input_join"];
        let ticket = &vector["protocol_sync"]["join_ticket"];
        let packet = protocol_sync(join, Some(ticket), Some((13, 14))).unwrap();
        assert_eq!(
            packet,
            unhex(vector["protocol_sync"]["packet_hex"].as_str().unwrap())
        );
        assert_eq!(&packet[..10], &[0x90, 1, 36, 0, 0, 0, 13, 14, 0, 0]);
        let default = protocol_sync(&json!({"UserId":123}), None, None).unwrap();
        assert_eq!(&default[..8], &[0x90, 1, 36, 0, 0, 0, 0, 0]);
        assert!(protocol_sync(&json!({}), None, None).is_ok());
        for range in [(6, 9), (7, 7), (9, 13), (14, 13)] {
            assert!(protocol_sync(join, None, Some(range)).is_err());
        }
        let fields = player_join_info(join, Some(ticket)).unwrap();
        let find = |key: &str| {
            fields
                .iter()
                .find(|(name, _)| name == key)
                .unwrap()
                .1
                .clone()
        };
        assert_eq!(find("OsPlatform"), json!("OSX"));
        assert_eq!(find("OsPlatformName"), json!("MacOS"));
        assert_eq!(find("AgeChecked"), json!("7"));
        assert_eq!(find("UserName"), json!("synthetic"));
        assert_eq!(find("__joinTicket"), json!(json_ascii(ticket)));
    }

    #[test]
    fn packet_138_and_server_id_match_synthetic_python_vectors() {
        let vector = fixture();
        let ticket = &vector["ticket"];
        assert_eq!(ticket_hash(b"", 0), 0x02cc_5d05);
        for pair in ticket["checksum_vectors"].as_array().unwrap() {
            assert_eq!(
                ticket_checksum(pair[0].as_u64().unwrap() as u32),
                pair[1].as_u64().unwrap() as u32
            );
        }
        assert_eq!(
            build_identifier(),
            ticket["build_identifier"].as_str().unwrap()
        );
        assert_eq!(
            ticket_packet(&ticket["join"]).unwrap(),
            unhex(ticket["packet_hex"].as_str().unwrap())
        );
        assert!(ticket_packet(&json!({"UserId":1,"ClientTicket":"t"})).is_err());
        assert_eq!(
            server_id_packet(),
            unhex(vector["server_id"]["packet_hex"].as_str().unwrap())
        );
    }
}
