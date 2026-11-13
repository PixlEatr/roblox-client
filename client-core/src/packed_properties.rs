//! keep received properties packed without losing anything.
//! decoded maps are temporary; keeping one for every node would eat a lot of ram.
use crate::shared_payload::{compress_bytes, decompress_bytes, SharedPayload};
use crate::property_storage::StoredBytes;
use serde::{
    de::{DeserializeSeed, IgnoredAny, MapAccess, Visitor},
    ser::SerializeMap,
    Deserialize, Serialize, Serializer,
};
use serde_json::{Map, Value};
use std::{borrow::Cow, fmt, io};
use std::{
    cell::RefCell,
    collections::{hash_map::DefaultHasher, HashMap},
    hash::{Hash, Hasher},
    sync::{Arc, Weak},
};

pub(crate) fn packed_value_layout_bytes() -> usize {
    std::mem::size_of::<PackedValue>()
}

thread_local! {
    // Weak, bounded deduplication of immutable large values. Removing a world
    // releases its payloads; this cache cannot retain an old world's data.
    static LARGE_VALUES: RefCell<HashMap<u64,Vec<Weak<PackedValue>>>> = RefCell::new(HashMap::new());
}

pub(crate) type SharedPayloadResolver<'a> = dyn Fn(&[u8]) -> Option<Arc<SharedPayload>> + 'a;

#[derive(Clone, Debug)]
pub(crate) struct CompactSharedProperty {
    pub(crate) action_index: usize,
    pub(crate) property: String,
    pub(crate) payload: Arc<SharedPayload>,
    pub(crate) force_bytes: bool,
}

#[derive(Clone, Debug)]
pub(crate) struct SharedPropertyValue {
    pub(crate) key: String,
    pub(crate) payload: Arc<SharedPayload>,
    pub(crate) force_bytes: bool,
}

#[derive(Clone, Debug)]
struct PackedValue {
    bytes: Option<StoredBytes>,
    expanded: usize,
    compressed: bool,
    shared_payload: Option<Arc<SharedPayload>>,
    shared_hex: bool,
}

impl PackedValue {
    fn new(raw: Vec<u8>) -> Self {
        let expanded = raw.len();
        let packed = if expanded >= 64 {
            compress_bytes(&raw)
        } else {
            None
        };
        if let Some(bytes) = packed.filter(|v| v.len() + 16 < expanded) {
            return Self {
                bytes: Some(StoredBytes::new(bytes)),
                expanded,
                compressed: true,
                shared_payload: None,
                shared_hex: false,
            };
        }
        Self {
            bytes: Some(StoredBytes::new(raw)),
            expanded,
            compressed: false,
            shared_payload: None,
            shared_hex: false,
        }
    }

    fn from_shared_hex(payload: Arc<SharedPayload>) -> Self {
        Self {
            expanded: 17usize.saturating_add(payload.len().saturating_mul(2)),
            bytes: None,
            compressed: false,
            shared_payload: Some(payload),
            shared_hex: true,
        }
    }

    fn from_shared_payload(payload: Arc<SharedPayload>, is_text: bool, expanded: usize) -> Self {
        if is_text {
            Self::from_shared_text(payload, expanded)
        } else {
            Self {
                expanded,
                ..Self::from_shared_hex(payload)
            }
        }
    }

    fn from_shared_text(payload: Arc<SharedPayload>, expanded: usize) -> Self {
        Self {
            bytes: None,
            expanded,
            compressed: false,
            shared_payload: Some(payload),
            shared_hex: false,
        }
    }

    fn raw(&self) -> std::borrow::Cow<'_, [u8]> {
        // A shared binary payload is not a JSON byte buffer. If a caller asks
        // for the encoded property representation, materialize the tagged JSON
        // form rather than parsing the raw payload as JSON.
        if self.shared_payload.is_some() {
            return serde_json::to_vec(&self.value())
                .expect("shared binary property JSON serializes")
                .into();
        }
        let bytes = self.bytes.as_ref().expect("encoded packed value bytes").read();
        if self.compressed {
            decompress_bytes(
                &bytes,
                self.expanded,
            )
            .into()
        } else {
            bytes
        }
    }
    fn value(&self) -> Value {
        if let Some(payload) = &self.shared_payload {
            let raw = payload.raw();
            if !self.shared_hex {
                return Value::String(
                    std::str::from_utf8(&raw)
                        .expect("shared text payload was matched to valid UTF-8")
                        .to_owned(),
                );
            }
            let mut hex = String::with_capacity(raw.len().saturating_mul(2));
            const HEX: &[u8; 16] = b"0123456789abcdef";
            for byte in raw.iter() {
                hex.push(HEX[(byte >> 4) as usize] as char);
                hex.push(HEX[(byte & 0x0f) as usize] as char);
            }
            return serde_json::json!({"$bytes_hex": hex});
        }
        serde_json::from_slice(&self.raw()).expect("internally encoded property value")
    }
    fn intern(raw: Vec<u8>) -> Arc<Self> {
        let mut hasher = DefaultHasher::new();
        raw.hash(&mut hasher);
        let hash = hasher.finish();
        LARGE_VALUES.with(|cell| {
            let mut cache = cell.borrow_mut();
            if cache.len() >= 16384 {
                cache.retain(|_, v| {
                    v.retain(|w| w.strong_count() > 0);
                    !v.is_empty()
                });
            }
            if let Some(bucket) = cache.get_mut(&hash) {
                bucket.retain(|w| w.strong_count() > 0);
                for item in bucket.iter().filter_map(Weak::upgrade) {
                    if item.shared_payload.is_none() && item.raw().as_ref() == raw {
                        return item;
                    }
                }
            }
            let result = Arc::new(Self::new(raw));
            if cache.len() < 16384 {
                let bucket = cache.entry(hash).or_default();
                if bucket.len() < 8 {
                    bucket.push(Arc::downgrade(&result));
                }
            }
            result
        })
    }
}

/// Match a JSON object key without allocating a `String` for every skipped
/// entry. The comparison happens while serde's escaped-key scratch is valid.
struct KeyMatches<'a>(&'a str);

impl<'de> DeserializeSeed<'de> for KeyMatches<'_> {
    type Value = bool;

    fn deserialize<D: serde::Deserializer<'de>>(self, deserializer: D) -> Result<bool, D::Error> {
        struct MatchVisitor<'a>(&'a str);
        impl<'de> Visitor<'de> for MatchVisitor<'_> {
            type Value = bool;
            fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("a JSON object key")
            }
            fn visit_str<E: serde::de::Error>(self, value: &str) -> Result<bool, E> {
                Ok(value == self.0)
            }
            fn visit_borrowed_str<E: serde::de::Error>(self, value: &'de str) -> Result<bool, E> {
                Ok(value == self.0)
            }
            fn visit_string<E: serde::de::Error>(self, value: String) -> Result<bool, E> {
                Ok(value == self.0)
            }
        }
        deserializer.deserialize_str(MatchVisitor(self.0))
    }
}

/// Select a nested object path while deserializing only the selected value.
/// All unrelated values are consumed as `IgnoredAny`, so the complete JSON
/// input is still validated without building their `Value` trees.
struct SelectPath<'a>(&'a [&'a str]);

impl<'de> DeserializeSeed<'de> for SelectPath<'_> {
    type Value = Option<Value>;

    fn deserialize<D: serde::Deserializer<'de>>(
        self,
        deserializer: D,
    ) -> Result<Self::Value, D::Error> {
        deserializer.deserialize_any(SelectVisitor(self.0))
    }
}

struct SelectVisitor<'a>(&'a [&'a str]);

impl<'de> Visitor<'de> for SelectVisitor<'_> {
    type Value = Option<Value>;

    fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("a JSON object")
    }

    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Self::Value, A::Error> {
        let Some((segment, rest)) = self.0.split_first() else {
            while map.next_entry::<IgnoredAny, IgnoredAny>()?.is_some() {}
            return Ok(None);
        };
        let mut selected = None;
        while let Some(matches) = map.next_key_seed(KeyMatches(segment))? {
            if matches && selected.is_none() {
                selected = if rest.is_empty() {
                    Some(Some(map.next_value::<Value>()?))
                } else {
                    Some(map.next_value_seed(SelectPath(rest))?)
                };
            } else {
                let _: IgnoredAny = map.next_value()?;
            }
        }
        Ok(selected.flatten())
    }

    fn visit_seq<A: serde::de::SeqAccess<'de>>(self, mut seq: A) -> Result<Self::Value, A::Error> {
        while seq.next_element::<IgnoredAny>()?.is_some() {}
        Ok(None)
    }
    fn visit_bool<E: serde::de::Error>(self, _: bool) -> Result<Self::Value, E> {
        Ok(None)
    }
    fn visit_i64<E: serde::de::Error>(self, _: i64) -> Result<Self::Value, E> {
        Ok(None)
    }
    fn visit_i128<E: serde::de::Error>(self, _: i128) -> Result<Self::Value, E> {
        Ok(None)
    }
    fn visit_u64<E: serde::de::Error>(self, _: u64) -> Result<Self::Value, E> {
        Ok(None)
    }
    fn visit_u128<E: serde::de::Error>(self, _: u128) -> Result<Self::Value, E> {
        Ok(None)
    }
    fn visit_f64<E: serde::de::Error>(self, _: f64) -> Result<Self::Value, E> {
        Ok(None)
    }
    fn visit_str<E: serde::de::Error>(self, _: &str) -> Result<Self::Value, E> {
        Ok(None)
    }
    fn visit_borrowed_str<E: serde::de::Error>(self, _: &'de str) -> Result<Self::Value, E> {
        Ok(None)
    }
    fn visit_string<E: serde::de::Error>(self, _: String) -> Result<Self::Value, E> {
        Ok(None)
    }
    fn visit_none<E: serde::de::Error>(self) -> Result<Self::Value, E> {
        Ok(None)
    }
    fn visit_unit<E: serde::de::Error>(self) -> Result<Self::Value, E> {
        Ok(None)
    }
}

fn select_path(bytes: &[u8], path: &[&str]) -> Option<Value> {
    let mut deserializer = serde_json::Deserializer::from_slice(bytes);
    let selected = SelectPath(path)
        .deserialize(&mut deserializer)
        .expect("internally encoded property JSON");
    deserializer
        .end()
        .expect("complete internally encoded property JSON");
    selected
}

fn check(value: &Value, depth: usize) -> Result<(), String> {
    if depth > 96 {
        return Err("property nesting exceeds storage limit".into());
    }
    match value {
        Value::Array(items) => {
            for item in items {
                check(item, depth + 1)?;
            }
        }
        Value::Object(items) => {
            for item in items.values() {
                check(item, depth + 1)?;
            }
        }
        _ => (),
    }
    Ok(())
}
fn large(value: &Value) -> bool {
    value.as_str().is_some_and(|v| v.len() >= 256)
        || value.as_object().is_some_and(|v| {
            v.values()
                .any(|v| v.as_str().is_some_and(|v| v.len() >= 256))
        })
}

fn canonical_hex_bytes(value: &Value) -> Option<Vec<u8>> {
    let object = value.as_object()?;
    if object.len() != 1 {
        return None;
    }
    let text = object.get("$bytes_hex")?.as_str()?;
    if text.len() % 2 != 0 {
        return None;
    }
    let mut bytes = Vec::with_capacity(text.len() / 2);
    for pair in text.as_bytes().chunks_exact(2) {
        let digit = |value: u8| match value {
            b'0'..=b'9' => Some(value - b'0'),
            b'a'..=b'f' => Some(value - b'a' + 10),
            _ => None,
        };
        let high = digit(pair[0])?;
        let low = digit(pair[1])?;
        bytes.push((high << 4) | low);
    }
    Some(bytes)
}

struct CountingWriter {
    written: usize,
}

impl io::Write for CountingWriter {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.written = self
            .written
            .checked_add(bytes.len())
            .ok_or_else(|| io::Error::new(io::ErrorKind::Other, "JSON byte count overflow"))?;
        Ok(bytes.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

fn json_encoded_len(value: &Value) -> Result<usize, String> {
    let mut writer = CountingWriter { written: 0 };
    serde_json::to_writer(&mut writer, value).map_err(|_| "property serialization failed")?;
    Ok(writer.written)
}

fn shared_property_value(
    payload: Arc<SharedPayload>,
    force_bytes: bool,
) -> Result<(Option<Arc<PackedValue>>, Option<Value>), String> {
    if force_bytes && payload.len().saturating_mul(2) >= 256 {
        // Pending shared references are always exposed as hex bytes. Their
        // logical length is enough for the threshold and encoded-size fields;
        // avoid inflating compressed payloads during staged world apply.
        return Ok((Some(Arc::new(PackedValue::from_shared_hex(payload))), None));
    }

    let raw = payload.raw();
    let text = if force_bytes {
        None
    } else {
        std::str::from_utf8(raw.as_ref()).ok()
    };
    let is_large = text.map_or_else(
        || raw.len().saturating_mul(2) >= 256,
        |value| value.len() >= 256,
    );
    if !is_large {
        let value = if let Some(text) = text {
            Value::String(text.to_owned())
        } else {
            let mut hex = String::with_capacity(raw.len().saturating_mul(2));
            const HEX: &[u8; 16] = b"0123456789abcdef";
            for byte in raw.iter() {
                hex.push(HEX[(byte >> 4) as usize] as char);
                hex.push(HEX[(byte & 0x0f) as usize] as char);
            }
            serde_json::json!({"$bytes_hex":hex})
        };
        return Ok((None, Some(value)));
    }

    let expanded = if let Some(text) = text {
        let mut writer = CountingWriter { written: 0 };
        serde_json::to_writer(&mut writer, text).map_err(|_| "property serialization failed")?;
        writer.written
    } else {
        17usize.saturating_add(raw.len().saturating_mul(2))
    };
    let is_text = text.is_some();
    drop(raw);
    Ok((
        Some(Arc::new(PackedValue::from_shared_payload(
            payload, is_text, expanded,
        ))),
        None,
    ))
}

fn pack_large_value(
    value: &Value,
    resolver: Option<&SharedPayloadResolver<'_>>,
) -> Result<Arc<PackedValue>, String> {
    if let Some(resolver) = resolver {
        if let Some(decoded) = canonical_hex_bytes(value) {
            if let Some(shared) = resolver(&decoded).filter(|shared| shared.matches_exact(&decoded))
            {
                return Ok(Arc::new(PackedValue::from_shared_hex(shared)));
            }
        }
        if let Some(text) = value.as_str() {
            if let Some(shared) =
                resolver(text.as_bytes()).filter(|shared| shared.matches_exact(text.as_bytes()))
            {
                let expanded = json_encoded_len(value)?;
                return Ok(Arc::new(PackedValue::from_shared_text(shared, expanded)));
            }
        }
    }
    Ok(PackedValue::intern(
        serde_json::to_vec(value).map_err(|_| "property serialization failed")?,
    ))
}

/// Serializes the small-property subset directly from the received map.
/// Keeping values borrowed here avoids a second JSON tree while packing.
struct SmallProperties<'a>(&'a Map<String, Value>);

impl Serialize for SmallProperties<'_> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut map = serializer.serialize_map(None)?;
        for (name, value) in self.0 {
            if !large(value) {
                map.serialize_entry(name, value)?;
            }
        }
        map.end()
    }
}

#[derive(Clone, Debug)]
pub(crate) struct PackedProperties {
    small: PackedValue,
    large: Option<Arc<Vec<(String, Arc<PackedValue>)>>>,
    len: usize,
}
impl PackedProperties {
    pub fn new(map: &Map<String, Value>) -> Result<Self, String> {
        Self::new_with_shared(map, None)
    }

    pub(crate) fn validate_map(map: &Map<String, Value>) -> Result<(), String> {
        for value in map.values() {
            check(value, 0)?;
        }
        Ok(())
    }

    pub(crate) fn new_validated(map: &Map<String, Value>) -> Result<Self, String> {
        Self::new_validated_with_shared(map, None)
    }

    fn new_validated_with_shared(
        map: &Map<String, Value>,
        resolver: Option<&SharedPayloadResolver<'_>>,
    ) -> Result<Self, String> {
        let mut blobs = Vec::new();
        for (name, value) in map {
            if large(value) {
                blobs.push((name.clone(), pack_large_value(value, resolver)?));
            }
        }
        Ok(Self {
            small: PackedValue::new(
                serde_json::to_vec(&SmallProperties(map))
                    .map_err(|_| "property serialization failed")?,
            ),
            large: if blobs.is_empty() {
                None
            } else {
                Some(Arc::new(blobs))
            },
            len: map.len(),
        })
    }

    pub fn new_with_shared(
        map: &Map<String, Value>,
        resolver: Option<&SharedPayloadResolver<'_>>,
    ) -> Result<Self, String> {
        Self::validate_map(map)?;
        Self::new_validated_with_shared(map, resolver)
    }
    fn small_map(&self) -> Map<String, Value> {
        match self.small.value() {
            Value::Object(map) => map,
            _ => unreachable!("internally encoded property map"),
        }
    }
    pub fn unpack(&self) -> Map<String, Value> {
        let mut map = self.small_map();
        if let Some(blobs) = &self.large {
            for (name, value) in blobs.iter() {
                map.insert(name.clone(), value.value());
            }
        }
        map
    }
    pub fn get(&self, key: &str) -> Option<Value> {
        self.get_path(&[key])
    }
    pub fn get_path(&self, path: &[&str]) -> Option<Value> {
        let Some((key, _)) = path.split_first() else {
            return None;
        };
        if let Some(value) = self
            .large
            .as_ref()
            .and_then(|v| v.iter().find(|(name, _)| name == key))
        {
            return if path.len() == 1 {
                Some(value.1.value())
            } else if value.1.shared_payload.is_some() {
                let materialized = value.1.value();
                let mut selected = &materialized;
                for segment in &path[1..] {
                    selected = selected.as_object()?.get(*segment)?;
                }
                Some(selected.clone())
            } else {
                select_path(&value.1.raw(), &path[1..])
            };
        }
        select_path(&self.small.raw(), path)
    }

    pub fn for_each_shared_payload(&self, mut visit: impl FnMut(&Arc<SharedPayload>)) {
        if let Some(values) = &self.large {
            for (_, value) in values.iter() {
                if let Some(payload) = &value.shared_payload {
                    visit(payload);
                }
            }
        }
    }
    pub fn set(&mut self, key: &str, value: Value) -> Result<(), String> {
        self.set_with_shared(key, value, None)
    }

    pub fn set_with_shared(
        &mut self,
        key: &str,
        value: Value,
        resolver: Option<&SharedPayloadResolver<'_>>,
    ) -> Result<(), String> {
        self.set_cow_with_shared(key, Cow::Owned(value), resolver)
    }

    pub(crate) fn set_ref_with_shared(
        &mut self,
        key: &str,
        value: &Value,
        resolver: Option<&SharedPayloadResolver<'_>>,
    ) -> Result<(), String> {
        self.set_cow_with_shared(key, Cow::Borrowed(value), resolver)
    }

    pub(crate) fn set_shared_payload(
        &mut self,
        key: &str,
        payload: Arc<SharedPayload>,
        force_bytes: bool,
    ) -> Result<(), String> {
        let (large_value, small_value) = shared_property_value(payload, force_bytes)?;
        let mut small = self.small_map();
        let had_small = small.remove(key).is_some();
        let mut blobs = self.large.as_deref().cloned().unwrap_or_default();
        let had_large = blobs.iter().any(|(name, _)| name == key);
        blobs.retain(|(name, _)| name != key);
        if let Some(value) = large_value {
            blobs.push((key.to_owned(), value));
        } else if let Some(value) = small_value {
            small.insert(key.to_owned(), value);
        }
        let encoded_small = PackedValue::new(
            serde_json::to_vec(&small).map_err(|_| "property serialization failed")?,
        );
        self.small = encoded_small;
        self.large = if blobs.is_empty() {
            None
        } else {
            Some(Arc::new(blobs))
        };
        if !had_small && !had_large {
            self.len += 1;
        }
        Ok(())
    }

    /// Apply a group of direct shared values with one small-map decode/encode
    /// and one large-value vector rebuild. Duplicate keys use last-write wins.
    pub(crate) fn set_shared_payloads(
        &mut self,
        values: impl IntoIterator<Item = SharedPropertyValue>,
    ) -> Result<(), String> {
        let mut latest = HashMap::<String, (Arc<SharedPayload>, bool)>::new();
        for value in values {
            latest.insert(value.key, (value.payload, value.force_bytes));
        }
        if latest.is_empty() {
            return Ok(());
        }

        let replacement_count = latest.len();
        let mut small = self.small_map();
        let mut blobs = self.large.as_deref().cloned().unwrap_or_default();
        let mut removed_large = 0usize;
        blobs.retain(|(name, _)| {
            let keep = !latest.contains_key(name);
            if !keep {
                removed_large += 1;
            }
            keep
        });
        let mut removed_small = 0usize;

        for (key, (payload, force_bytes)) in latest {
            if small.remove(&key).is_some() {
                removed_small += 1;
            }
            let (large_value, small_value) = shared_property_value(payload, force_bytes)?;
            if let Some(value) = large_value {
                blobs.push((key, value));
            } else if let Some(value) = small_value {
                small.insert(key, value);
            }
        }
        let encoded_small = PackedValue::new(
            serde_json::to_vec(&small).map_err(|_| "property serialization failed")?,
        );
        let new_keys = replacement_count
            .checked_sub(removed_large + removed_small)
            .ok_or("invalid packed-property partition")?;
        self.small = encoded_small;
        self.large = if blobs.is_empty() {
            None
        } else {
            Some(Arc::new(blobs))
        };
        self.len += new_keys;
        Ok(())
    }

    fn set_cow_with_shared<'a>(
        &mut self,
        key: &str,
        value: Cow<'a, Value>,
        resolver: Option<&SharedPayloadResolver<'_>>,
    ) -> Result<(), String> {
        check(value.as_ref(), 0)?;
        let mut small = self.small_map();
        let is_large = large(value.as_ref());
        let had_large = self
            .large
            .as_ref()
            .is_some_and(|v| v.iter().any(|(name, _)| name == key));
        let existed = had_large || small.contains_key(key);
        let mut large_values = self.large.clone();
        if is_large || had_large {
            let mut blobs = large_values.as_deref().cloned().unwrap_or_default();
            blobs.retain(|(name, _)| name != key);
            if is_large {
                blobs.push((key.into(), pack_large_value(value.as_ref(), resolver)?));
                small.remove(key);
            }
            large_values = if blobs.is_empty() {
                None
            } else {
                Some(Arc::new(blobs))
            };
        }
        if !is_large {
            small.insert(key.into(), value.into_owned());
        }
        let encoded = PackedValue::new(
            serde_json::to_vec(&small).map_err(|_| "property serialization failed")?,
        );
        self.small = encoded;
        self.large = large_values;
        if !existed {
            self.len += 1;
        }
        Ok(())
    }
    pub fn len(&self) -> usize {
        self.len
    }
    pub fn stored_bytes(&self) -> usize {
        // Shared large values are conservatively counted for each reference.
        std::mem::size_of::<Self>()
            + self.small.bytes.as_ref().map_or(0, |bytes| bytes.len())
            + self.large.as_ref().map_or(0, |v| {
                v.iter()
                    .map(|(k, b)| {
                        k.len()
                            + b.shared_payload.as_ref().map_or_else(
                                || b.bytes.as_ref().map_or(0, |bytes| bytes.len()),
                                |p| p.stored_bytes(),
                            )
                            + std::mem::size_of::<PackedValue>()
                    })
                    .sum::<usize>()
            })
    }
    #[cfg(test)]
    fn expanded_bytes(&self) -> usize {
        self.small.expanded
            + self
                .large
                .as_ref()
                .map_or(0, |v| v.iter().map(|(_, b)| b.expanded).sum())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn round_trip_and_copy_on_write_preserve_received_values() {
        let source = json!({"bytes":{"$bytes_hex":"00ff".repeat(4096)},"null":null,
            "float":1.2345678901234567,"large":u64::MAX,"negative":i64::MIN,"array":[true,{},[],"é"]});
        let first = PackedProperties::new(source.as_object().unwrap()).unwrap();
        assert!(first.stored_bytes() < first.expanded_bytes() / 2);
        let shared = first.clone();
        assert!(shared.small.bytes.as_ref().unwrap().shares_backing(
            first.small.bytes.as_ref().unwrap()
        ));
        assert_eq!(Value::Object(first.unpack()), source);
        let mut changed = shared.unpack();
        changed.insert("float".into(), json!(99));
        let changed = PackedProperties::new(&changed).unwrap();
        assert_eq!(first.get("float"), Some(json!(1.2345678901234567)));
        assert_eq!(changed.get("float"), Some(json!(99)));
        assert_eq!(first.get("null"), Some(Value::Null));
        assert_eq!(first.get("absent"), None);
    }

    #[test]
    fn borrowed_pack_preserves_large_scalar_null_and_source_values() {
        let source = json!({
            "number": 17,
            "text": "small",
            "explicit_null": null,
            "large_text": "x".repeat(512),
            "large_nested": {"payload": "y".repeat(300), "flag": true}
        });
        let source_map = source.as_object().unwrap();
        let expected_small = Map::from_iter([
            ("explicit_null".to_owned(), Value::Null),
            ("number".to_owned(), json!(17)),
            ("text".to_owned(), json!("small")),
        ]);
        let packed = PackedProperties::new(source_map).unwrap();

        assert_eq!(packed.small.value(), Value::Object(expected_small));
        assert_eq!(Value::Object(packed.unpack()), source);
        assert_eq!(source_map["large_text"], source["large_text"]);
        assert_eq!(source_map["large_nested"], source["large_nested"]);
    }

    #[test]
    fn direct_shared_payload_set_preserves_threshold_json_and_copy_on_write() {
        use crate::shared_payload::SharedPayload;

        let mut properties = PackedProperties::new(&Map::new()).unwrap();
        let original =
            SharedPayload::from_raw("a large shared text value ".repeat(32).into_bytes());
        let old = properties.clone();
        properties
            .set_shared_payload("Text", Arc::clone(&original), false)
            .unwrap();
        let packed = properties.large.as_ref().unwrap();
        let text_value = packed.iter().find(|(key, _)| key == "Text").unwrap();
        assert!(Arc::ptr_eq(
            text_value.1.shared_payload.as_ref().unwrap(),
            &original
        ));
        assert_eq!(
            properties.get("Text"),
            Some(json!("a large shared text value ".repeat(32)))
        );
        assert_eq!(old.get("Text"), None);

        let binary_bytes = (0..256)
            .map(|value| (value ^ 0x5a) as u8)
            .collect::<Vec<_>>();
        let binary = SharedPayload::from_raw(binary_bytes.clone());
        let mut binary_hex = String::with_capacity(binary_bytes.len() * 2);
        for byte in &binary_bytes {
            binary_hex.push_str(&format!("{byte:02x}"));
        }
        properties
            .set_shared_payload("Blob", Arc::clone(&binary), false)
            .unwrap();
        let blob = properties
            .large
            .as_ref()
            .unwrap()
            .iter()
            .find(|(key, _)| key == "Blob")
            .unwrap();
        assert!(Arc::ptr_eq(
            blob.1.shared_payload.as_ref().unwrap(),
            &binary
        ));
        assert_eq!(
            properties.get("Blob"),
            Some(json!({"$bytes_hex":binary_hex}))
        );

        let short = SharedPayload::from_raw(b"tiny".to_vec());
        properties
            .set_shared_payload("Short", Arc::clone(&short), false)
            .unwrap();
        assert_eq!(properties.get("Short"), Some(json!("tiny")));
        assert!(!properties
            .large
            .as_ref()
            .unwrap()
            .iter()
            .any(|(key, _)| key == "Short"));
    }

    #[test]
    fn batched_shared_values_match_sequential_thresholds_duplicates_and_arcs() {
        use crate::shared_payload::SharedPayload;

        let known_small = SharedPayload::from_raw(b"k".repeat(255));
        let known_large = SharedPayload::from_raw(b"l".repeat(256));
        let forced_small = SharedPayload::from_raw(b"f".repeat(127));
        let forced_large = SharedPayload::from_raw(b"g".repeat(128));
        let first_duplicate = SharedPayload::from_raw(b"old".repeat(100));
        let last_duplicate = SharedPayload::from_raw(b"new".repeat(100));
        let assignments = vec![
            SharedPropertyValue {
                key: "known_small".into(),
                payload: Arc::clone(&known_small),
                force_bytes: false,
            },
            SharedPropertyValue {
                key: "known_large".into(),
                payload: Arc::clone(&known_large),
                force_bytes: false,
            },
            SharedPropertyValue {
                key: "forced_small".into(),
                payload: Arc::clone(&forced_small),
                force_bytes: true,
            },
            SharedPropertyValue {
                key: "forced_large".into(),
                payload: Arc::clone(&forced_large),
                force_bytes: true,
            },
            SharedPropertyValue {
                key: "duplicate".into(),
                payload: Arc::clone(&first_duplicate),
                force_bytes: false,
            },
            SharedPropertyValue {
                key: "duplicate".into(),
                payload: Arc::clone(&last_duplicate),
                force_bytes: false,
            },
        ];
        let mut batched = PackedProperties::new(&Map::new()).unwrap();
        batched.set_shared_payloads(assignments.clone()).unwrap();
        let mut sequential = PackedProperties::new(&Map::new()).unwrap();
        for assignment in assignments {
            sequential
                .set_shared_payload(&assignment.key, assignment.payload, assignment.force_bytes)
                .unwrap();
        }
        assert_eq!(batched.unpack(), sequential.unpack());
        assert_eq!(batched.len(), sequential.len());
        assert_eq!(batched.get("known_small"), Some(json!("k".repeat(255))));
        assert_eq!(batched.get("known_large"), Some(json!("l".repeat(256))));
        assert_eq!(
            batched.get("forced_small"),
            Some(json!({"$bytes_hex":"66".repeat(127)}))
        );
        assert_eq!(
            batched.get("forced_large"),
            Some(json!({"$bytes_hex":"67".repeat(128)}))
        );
        assert_eq!(batched.get("duplicate"), Some(json!("new".repeat(100))));

        let shared = batched.large.as_ref().unwrap();
        assert!(Arc::ptr_eq(
            shared
                .iter()
                .find(|(key, _)| key == "known_large")
                .unwrap()
                .1
                .shared_payload
                .as_ref()
                .unwrap(),
            &known_large
        ));
        assert!(Arc::ptr_eq(
            shared
                .iter()
                .find(|(key, _)| key == "forced_large")
                .unwrap()
                .1
                .shared_payload
                .as_ref()
                .unwrap(),
            &forced_large
        ));
        assert!(Arc::ptr_eq(
            shared
                .iter()
                .find(|(key, _)| key == "duplicate")
                .unwrap()
                .1
                .shared_payload
                .as_ref()
                .unwrap(),
            &last_duplicate
        ));
        assert!(!shared
            .iter()
            .any(|(key, _)| key == "known_small" || key == "forced_small"));
    }

    #[test]
    fn batched_shared_replacements_preserve_partition_counts_and_arcs() {
        use crate::shared_payload::SharedPayload;

        let initial = Map::from_iter([
            ("existing_small".to_owned(), json!("initial")),
            ("existing_large".to_owned(), json!("i".repeat(320))),
        ]);
        let mut batched = PackedProperties::new(&initial).unwrap();
        let mut sequential = PackedProperties::new(&initial).unwrap();
        assert_eq!(batched.len(), 2);

        let replace_small_with_large = SharedPayload::from_raw(b"A".repeat(320));
        let replace_large_with_small = SharedPayload::from_raw(b"short".to_vec());
        let new_large = SharedPayload::from_raw(b"B".repeat(300));
        let duplicate_first = SharedPayload::from_raw(b"discarded".repeat(40));
        let duplicate_last = SharedPayload::from_raw(b"C".repeat(300));
        let new_small = SharedPayload::from_raw(b"tiny".to_vec());
        let assignments = vec![
            SharedPropertyValue {
                key: "existing_small".into(),
                payload: Arc::clone(&replace_small_with_large),
                force_bytes: false,
            },
            SharedPropertyValue {
                key: "existing_large".into(),
                payload: Arc::clone(&replace_large_with_small),
                force_bytes: false,
            },
            SharedPropertyValue {
                key: "new_large".into(),
                payload: Arc::clone(&new_large),
                force_bytes: false,
            },
            SharedPropertyValue {
                key: "duplicate".into(),
                payload: Arc::clone(&duplicate_first),
                force_bytes: false,
            },
            SharedPropertyValue {
                key: "new_small".into(),
                payload: Arc::clone(&new_small),
                force_bytes: false,
            },
            SharedPropertyValue {
                key: "duplicate".into(),
                payload: Arc::clone(&duplicate_last),
                force_bytes: false,
            },
        ];

        batched.set_shared_payloads(assignments.clone()).unwrap();
        for assignment in assignments {
            sequential
                .set_shared_payload(&assignment.key, assignment.payload, assignment.force_bytes)
                .unwrap();
        }

        assert_eq!(batched.unpack(), sequential.unpack());
        assert_eq!(batched.len(), sequential.len());
        assert_eq!(batched.len(), 5);
        assert_eq!(batched.get("existing_small"), Some(json!("A".repeat(320))));
        assert_eq!(batched.get("existing_large"), Some(json!("short")));
        assert_eq!(batched.get("new_large"), Some(json!("B".repeat(300))));
        assert_eq!(batched.get("new_small"), Some(json!("tiny")));
        assert_eq!(batched.get("duplicate"), Some(json!("C".repeat(300))));

        let shared = batched.large.as_ref().unwrap();
        for (key, expected) in [
            ("existing_small", &replace_small_with_large),
            ("new_large", &new_large),
            ("duplicate", &duplicate_last),
        ] {
            let value = shared.iter().find(|(name, _)| name == key).unwrap();
            assert!(Arc::ptr_eq(
                value.1.shared_payload.as_ref().unwrap(),
                expected
            ));
        }
        assert!(!shared
            .iter()
            .any(|(key, _)| key == "existing_large" || key == "new_small"));
    }

    #[test]
    fn single_shared_replacements_keep_len_across_storage_partition_changes() {
        use crate::shared_payload::SharedPayload;

        let mut properties =
            PackedProperties::new(&Map::from_iter([("value".to_owned(), json!("initial"))]))
                .unwrap();
        assert_eq!(properties.len(), 1);

        let large = SharedPayload::from_raw(b"l".repeat(300));
        properties
            .set_shared_payload("value", Arc::clone(&large), false)
            .unwrap();
        assert_eq!(properties.len(), 1);
        assert!(properties.large.as_ref().is_some_and(|values| {
            values.iter().any(|(key, value)| {
                key == "value"
                    && value
                        .shared_payload
                        .as_ref()
                        .is_some_and(|payload| Arc::ptr_eq(payload, &large))
            })
        }));

        let small = SharedPayload::from_raw(b"short".to_vec());
        properties
            .set_shared_payload("value", Arc::clone(&small), false)
            .unwrap();
        assert_eq!(properties.len(), 1);
        assert!(properties
            .large
            .as_ref()
            .is_none_or(|values| values.iter().all(|(key, _)| key != "value")));
        assert_eq!(properties.get("value"), Some(json!("short")));

        let forced_large = SharedPayload::from_raw(b"x".repeat(128));
        properties
            .set_shared_payload("value", Arc::clone(&forced_large), true)
            .unwrap();
        assert_eq!(properties.len(), 1);
        assert_eq!(
            properties.get("value"),
            Some(json!({"$bytes_hex":"78".repeat(128)}))
        );
        let forced_small = SharedPayload::from_raw(b"y".repeat(127));
        properties
            .set_shared_payload("value", forced_small, true)
            .unwrap();
        assert_eq!(properties.len(), 1);
        assert_eq!(
            properties.get("value"),
            Some(json!({"$bytes_hex":"79".repeat(127)}))
        );
        assert!(properties
            .large
            .as_ref()
            .is_none_or(|values| values.iter().all(|(key, _)| key != "value")));
    }

    #[test]
    fn selective_lookup_handles_compressed_and_uncompressed_nested_values() {
        let small_source = json!({
            "escaped\\\"key": {"child": null, "precise": 1.2345678901234567},
            "wrong": 4,
            "outer": {"large": "z".repeat(512), "target": null}
        });
        let uncompressed = PackedProperties::new(small_source.as_object().unwrap()).unwrap();
        assert_eq!(
            uncompressed.get_path(&["escaped\\\"key", "child"]),
            Some(Value::Null)
        );
        assert_eq!(
            uncompressed.get_path(&["escaped\\\"key", "precise"]),
            Some(json!(1.2345678901234567))
        );
        assert_eq!(
            uncompressed.get_path(&["outer", "target"]),
            Some(Value::Null)
        );
        assert_eq!(uncompressed.get_path(&["outer", "missing"]), None);
        assert_eq!(uncompressed.get_path(&["wrong", "child"]), None);

        let mut compressed_source = Map::new();
        compressed_source.insert("focus".into(), json!(123));
        for index in 0..128 {
            compressed_source.insert(format!("unused_{index:03}"), json!({"text":"repeat"}));
        }
        let mut compressed = PackedProperties::new(&compressed_source).unwrap();
        assert!(compressed.small.compressed);
        assert_eq!(compressed.get("focus"), Some(json!(123)));
        let old = compressed.clone();
        compressed.set("focus", json!(456)).unwrap();
        assert_eq!(old.get("focus"), Some(json!(123)));
        assert_eq!(compressed.get("focus"), Some(json!(456)));
    }

    #[test]
    fn shared_hex_payloads_reuse_exact_arc_and_preserve_json_and_cow() {
        let raw = (0..130)
            .map(|index| [index as u8, 0, 0xab, 0xff])
            .flatten()
            .collect::<Vec<_>>();
        let shared = SharedPayload::from_raw(raw.clone());
        let mut hex = String::with_capacity(raw.len() * 2);
        const HEX: &[u8; 16] = b"0123456789abcdef";
        for byte in &raw {
            hex.push(HEX[(byte >> 4) as usize] as char);
            hex.push(HEX[(byte & 0x0f) as usize] as char);
        }
        let value = json!({"$bytes_hex":hex});
        let source = Map::from_iter([("payload".to_owned(), value.clone())]);
        let resolver = |bytes: &[u8]| shared.matches_exact(bytes).then(|| Arc::clone(&shared));
        let packed = PackedProperties::new_with_shared(&source, Some(&resolver)).unwrap();

        let mut retained = Vec::new();
        packed.for_each_shared_payload(|payload| retained.push(Arc::clone(payload)));
        assert_eq!(retained.len(), 1);
        assert!(Arc::ptr_eq(&retained[0], &shared));
        assert_eq!(packed.get("payload"), Some(value.clone()));
        assert_eq!(
            packed.get_path(&["payload", "$bytes_hex"]),
            Some(json!(hex))
        );
        assert_eq!(
            Value::Object(packed.unpack()),
            Value::Object(source.clone())
        );

        let text = "shared utf8 payload ".repeat(256);
        let text_payload = SharedPayload::from_raw(text.as_bytes().to_vec());
        let text_resolver = |bytes: &[u8]| {
            text_payload
                .matches_exact(bytes)
                .then(|| Arc::clone(&text_payload))
        };
        let text_source = Map::from_iter([("text".to_owned(), Value::String(text.clone()))]);
        let text_packed =
            PackedProperties::new_with_shared(&text_source, Some(&text_resolver)).unwrap();
        assert_eq!(text_packed.get("text"), Some(Value::String(text.clone())));
        let mut text_arcs = Vec::new();
        text_packed.for_each_shared_payload(|payload| text_arcs.push(Arc::clone(payload)));
        assert_eq!(text_arcs.len(), 1);
        assert!(Arc::ptr_eq(&text_arcs[0], &text_payload));

        let miss = |_bytes: &[u8]| None;
        let unmatched = PackedProperties::new_with_shared(&source, Some(&miss)).unwrap();
        assert_eq!(unmatched.get("payload"), Some(value.clone()));
        let other = SharedPayload::from_raw(vec![1u8; raw.len()]);
        let mismatch = |_bytes: &[u8]| Some(Arc::clone(&other));
        let unverified = PackedProperties::new_with_shared(&source, Some(&mismatch)).unwrap();
        assert_eq!(unverified.get("payload"), Some(value.clone()));
        for fallback in [&unmatched, &unverified] {
            let mut refs = 0;
            fallback.for_each_shared_payload(|_| refs += 1);
            assert_eq!(refs, 0);
        }

        let uppercase = json!({"$bytes_hex":hex.to_ascii_uppercase()});
        let noncanonical = Map::from_iter([("payload".to_owned(), uppercase.clone())]);
        let upper = PackedProperties::new_with_shared(&noncanonical, Some(&resolver)).unwrap();
        assert_eq!(upper.get("payload"), Some(uppercase));
        let mut refs = 0;
        upper.for_each_shared_payload(|_| refs += 1);
        assert_eq!(refs, 0);

        let mut mixed_hex = hex.clone();
        let alpha = mixed_hex
            .find(|character: char| character.is_ascii_lowercase())
            .unwrap();
        let uppercase_digit = mixed_hex[alpha..alpha + 1].to_ascii_uppercase();
        mixed_hex.replace_range(alpha..alpha + 1, &uppercase_digit);
        let mixed = json!({"$bytes_hex":mixed_hex});
        let mixed_source = Map::from_iter([("payload".to_owned(), mixed.clone())]);
        let mixed_case = PackedProperties::new_with_shared(&mixed_source, Some(&resolver)).unwrap();
        assert_eq!(mixed_case.get("payload"), Some(mixed));
        let mut refs = 0;
        mixed_case.for_each_shared_payload(|_| refs += 1);
        assert_eq!(refs, 0);

        let mut changed = packed.clone();
        changed.set("payload", json!("small")).unwrap();
        assert_eq!(packed.get("payload"), Some(value.clone()));
        assert_eq!(changed.get("payload"), Some(json!("small")));
        let mut changed_refs = 0;
        changed.for_each_shared_payload(|_| changed_refs += 1);
        assert_eq!(changed_refs, 0);
        changed
            .set_with_shared("payload", value.clone(), Some(&resolver))
            .unwrap();
        let mut changed_refs = Vec::new();
        changed.for_each_shared_payload(|payload| changed_refs.push(Arc::clone(payload)));
        assert_eq!(changed_refs.len(), 1);
        assert!(Arc::ptr_eq(&changed_refs[0], &shared));
        let mut old_refs = Vec::new();
        packed.for_each_shared_payload(|payload| old_refs.push(Arc::clone(payload)));
        assert_eq!(old_refs.len(), 1);
        assert!(Arc::ptr_eq(&old_refs[0], &shared));
    }

    #[test]
    fn shared_text_encoded_length_counts_unicode_and_json_escaping_without_a_buffer() {
        let samples = [
            r#"unicode: café 🦊 / "quoted" \ newline"#.repeat(96),
            "é".repeat(300),
            "\0\u{0001}\r\n\t\"\\".repeat(80),
        ];
        for text in samples {
            let value = Value::String(text.clone());
            let expected_encoded = serde_json::to_vec(&value).unwrap();
            assert_eq!(json_encoded_len(&value).unwrap(), expected_encoded.len());

            let payload = SharedPayload::from_raw(text.as_bytes().to_vec());
            let resolver =
                |bytes: &[u8]| payload.matches_exact(bytes).then(|| Arc::clone(&payload));
            let properties = Map::from_iter([("text".to_owned(), value.clone())]);
            let packed = PackedProperties::new_with_shared(&properties, Some(&resolver)).unwrap();
            let mut retained = Vec::new();
            packed.for_each_shared_payload(|entry| retained.push(Arc::clone(entry)));
            assert_eq!(retained.len(), 1);
            assert!(Arc::ptr_eq(&retained[0], &payload));
            assert_eq!(packed.get("text"), Some(value));
            let packed_value = packed.large.as_ref().unwrap()[0].1.as_ref();
            assert_eq!(packed_value.expanded, expected_encoded.len());
        }
    }

    #[test]
    fn borrowed_shared_set_keeps_text_and_binary_without_owning_input_values() {
        let text = "borrowed shared text ".repeat(64);
        let text_payload = SharedPayload::from_raw(text.as_bytes().to_vec());
        let raw = (0..256).map(|i| (i ^ (i >> 3)) as u8).collect::<Vec<_>>();
        let binary_payload = SharedPayload::from_raw(raw.clone());
        let resolver = |bytes: &[u8]| {
            if text_payload.matches_exact(bytes) {
                Some(Arc::clone(&text_payload))
            } else if binary_payload.matches_exact(bytes) {
                Some(Arc::clone(&binary_payload))
            } else {
                None
            }
        };
        let mut properties = PackedProperties::new(&Map::new()).unwrap();
        let mut text_input = Value::String(text.clone());
        properties
            .set_ref_with_shared("text", &text_input, Some(&resolver))
            .unwrap();
        text_input = Value::String("mutated after borrow".into());
        drop(text_input);
        assert_eq!(properties.get("text"), Some(Value::String(text.clone())));

        let mut hex = String::with_capacity(raw.len() * 2);
        const HEX: &[u8; 16] = b"0123456789abcdef";
        for byte in &raw {
            hex.push(HEX[(byte >> 4) as usize] as char);
            hex.push(HEX[(byte & 0x0f) as usize] as char);
        }
        let mut binary_input = serde_json::json!({"$bytes_hex":hex});
        let expected_binary = binary_input.clone();
        properties
            .set_ref_with_shared("binary", &binary_input, Some(&resolver))
            .unwrap();
        binary_input["$bytes_hex"] = Value::String("00".into());
        drop(binary_input);
        assert_eq!(properties.get("binary"), Some(expected_binary));
        let mut retained = Vec::new();
        properties.for_each_shared_payload(|payload| retained.push(Arc::clone(payload)));
        assert_eq!(retained.len(), 2);
        assert!(retained
            .iter()
            .any(|value| Arc::ptr_eq(value, &text_payload)));
        assert!(retained
            .iter()
            .any(|value| Arc::ptr_eq(value, &binary_payload)));
    }

    #[test]
    fn deep_values_fail_before_publishing_a_record() {
        let mut deep = Value::Null;
        for _ in 0..100 {
            deep = Value::Array(vec![deep]);
        }
        let map = Map::from_iter([("deep".into(), deep)]);
        assert!(PackedProperties::new(&map).is_err());
    }
    #[test]
    fn repeated_large_fields_share_storage_without_sharing_mutations() {
        let source = json!({"payload":{"$bytes_hex":"01ff".repeat(1024)},"x":1});
        let first = PackedProperties::new(source.as_object().unwrap()).unwrap();
        let mut other = PackedProperties::new(source.as_object().unwrap()).unwrap();
        assert!(Arc::ptr_eq(
            &first.large.as_ref().unwrap()[0].1,
            &other.large.as_ref().unwrap()[0].1
        ));
        other.set("x", json!(2)).unwrap();
        assert!(Arc::ptr_eq(
            &first.large.as_ref().unwrap()[0].1,
            &other.large.as_ref().unwrap()[0].1
        ));
        other.set("payload", Value::Null).unwrap();
        assert_eq!(first.get("payload"), Some(source["payload"].clone()));
        assert_eq!(other.get("payload"), Some(Value::Null));
        assert_eq!(first.get("x"), Some(json!(1)));
        assert_eq!(other.get("x"), Some(json!(2)));
    }
}
