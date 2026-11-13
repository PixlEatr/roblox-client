//! Typed decoder for the recovered network schema v3.
use crate::wire::{decompress_stream, Reader, WireError};
use serde_json::{json, Value};

const SCHEMA_PACKET: u8 = 151;
const SCHEMA_VERSION: u64 = 3;
const MAX_SCHEMA_BYTES: usize = 16 * 1024 * 1024;
const MAX_ITEMS: u64 = 65_535;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EnumDesc {
    pub id: usize,
    pub name: String,
    pub bits: u8,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PropertyDesc {
    pub id: usize,
    pub class: usize,
    pub name: String,
    pub type_id: u8,
    pub enum_id: Option<u16>,
    pub lr_type: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EventArgument {
    pub type_id: u8,
    pub enum_id: u16,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EventDesc {
    pub id: usize,
    pub class: usize,
    pub name: String,
    pub arguments: Vec<EventArgument>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClassDesc {
    pub id: usize,
    pub name: String,
    pub properties: Vec<usize>,
    pub events: Vec<usize>,
    pub isr_properties: Vec<u32>,
    pub unknown_u16: u16,
}

/// Compact, ordered schema tables used by the Rust replication decoder.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Schema {
    pub version: u64,
    pub flags: u64,
    pub enums: Vec<EnumDesc>,
    pub classes: Vec<ClassDesc>,
    pub properties: Vec<PropertyDesc>,
    pub events: Vec<EventDesc>,
    pub strings: Vec<String>,
    pub names: Vec<String>,
    pub lr_types: Vec<Vec<u64>>,
    pub parent_property_id: usize,
    pub decoded_bytes: usize,
}

impl Schema {
    /// Convert to the established JSON shape for incremental migration callers.
    pub fn to_json(&self) -> Value {
        json!({
            "version": self.version,
            "flags": self.flags,
            "enums": self.enums.iter().map(|e| json!({"id":e.id,"name":e.name,"bits":e.bits})).collect::<Vec<_>>(),
            "classes": self.classes.iter().map(|c| json!({
                "id":c.id,"name":c.name,"properties":c.properties,"events":c.events,
                "isr_properties":c.isr_properties,"unknown_u16":c.unknown_u16
            })).collect::<Vec<_>>(),
            "properties": self.properties.iter().map(|p| {
                let mut row=json!({"id":p.id,"class":p.class,"name":p.name,"type":p.type_id});
                if let Some(value)=p.enum_id { row["enum"]=json!(value); }
                if let Some(value)=p.lr_type { row["lr_type"]=json!(value); }
                row
            }).collect::<Vec<_>>(),
            "events": self.events.iter().map(|e| json!({
                "id":e.id,"class":e.class,"name":e.name,
                "arguments":e.arguments.iter().map(|a|json!({"type":a.type_id,"enum":a.enum_id})).collect::<Vec<_>>()
            })).collect::<Vec<_>>(),
            "strings": self.strings,
            "names": self.names,
            "lr_types": self.lr_types,
            "parent_property_id": self.parent_property_id,
            "decoded_bytes": self.decoded_bytes,
        })
    }
}

/// Parse and validate the network schema packet and its compressed v3 payload.
pub fn parse_schema(payload: &[u8]) -> Result<Schema, WireError> {
    let mut outer = Reader::new(payload);
    if outer.u8()? != SCHEMA_PACKET {
        return Err(WireError::new("Not a network schema packet"));
    }
    let expanded = decompress_stream(&mut outer, MAX_SCHEMA_BYTES)?;
    outer.finish()?;

    let mut r = Reader::new(&expanded);
    let version = r.varuint(64)?;
    if version != SCHEMA_VERSION {
        return Err(WireError::new("Unsupported schema version"));
    }
    let flags = r.u64()?;
    let mut enums = Vec::new();
    for id in 0..r.count(MAX_ITEMS)? {
        enums.push(EnumDesc {
            id,
            name: r.string(1 << 20)?,
            bits: r.u8()?,
        });
    }
    let class_count = r.count(MAX_ITEMS)?;
    let expected_properties = r.count(MAX_ITEMS)?;
    let expected_events = r.count(MAX_ITEMS)?;
    let mut classes = Vec::with_capacity(class_count);
    let mut properties = Vec::new();
    let mut events = Vec::new();

    for class_id in 0..class_count {
        let name = r.string(1 << 20)?;
        let mut class_properties = Vec::new();
        for _ in 0..r.count(MAX_ITEMS)? {
            let id = properties.len();
            let property_name = r.string(1 << 20)?;
            let type_id = r.u8()?;
            let mut enum_id = None;
            let mut lr_type = None;
            if type_id == 7 {
                let value = r.u16()?;
                if value != u16::MAX && usize::from(value) >= enums.len() {
                    return Err(WireError::new("Invalid schema enum"));
                }
                enum_id = Some(value);
            } else if type_id == 60 {
                lr_type = Some(r.varuint(64)?);
            }
            properties.push(PropertyDesc {
                id,
                class: class_id,
                name: property_name,
                type_id,
                enum_id,
                lr_type,
            });
            class_properties.push(id);
        }
        let unknown_u16 = r.u16()?;
        let mut class_events = Vec::new();
        for _ in 0..r.count(MAX_ITEMS)? {
            let id = events.len();
            let event_name = r.string(1 << 20)?;
            let mut arguments = Vec::new();
            for _ in 0..r.count(1024)? {
                arguments.push(EventArgument {
                    type_id: r.u8()?,
                    enum_id: r.u16()?,
                });
            }
            events.push(EventDesc {
                id,
                class: class_id,
                name: event_name,
                arguments,
            });
            class_events.push(id);
        }
        classes.push(ClassDesc {
            id: class_id,
            name,
            properties: class_properties,
            events: class_events,
            isr_properties: Vec::new(),
            unknown_u16,
        });
    }

    if properties.len() != expected_properties || events.len() != expected_events {
        return Err(WireError::new("Schema counts do not match"));
    }
    let parent_property_id = properties.len();
    let mut strings = vec![String::new()];
    let mut names = Vec::new();
    let mut lr_types = Vec::new();
    if r.remaining() > 0 {
        for _ in 0..r.count(MAX_ITEMS)? {
            strings.push(r.string(1 << 20)?);
        }
    }
    if r.remaining() > 0 {
        for _ in 0..r.count(MAX_ITEMS)? {
            names.push(r.string(1 << 20)?);
        }
    }
    if r.remaining() > 0 {
        let count = r.u32()?;
        if u64::from(count) > class_count as u64 {
            return Err(WireError::new("Too many ISR classes"));
        }
        for _ in 0..count {
            let class_id = r.u32()?;
            let property_count = r.u32()?;
            if u64::from(class_id) >= class_count as u64 || property_count > 128 {
                return Err(WireError::new("Invalid ISR class/property count"));
            }
            let mut ids = Vec::with_capacity(property_count as usize);
            for _ in 0..property_count {
                let property_id = r.u32()?;
                if usize::try_from(property_id).map_or(true, |id| id >= expected_properties) {
                    return Err(WireError::new("Invalid ISR property reference"));
                }
                ids.push(property_id);
            }
            classes[class_id as usize].isr_properties = ids;
        }
    }
    if r.remaining() > 0 {
        for _ in 0..r.count(MAX_ITEMS)? {
            let mut entry = vec![r.varuint(64)?, r.varuint(64)?, r.varuint(64)?];
            if entry[2] != 0 {
                entry.push(r.varuint(64)?);
            }
            lr_types.push(entry);
        }
    }
    r.finish()?;
    let decoded_bytes = r.position();
    Ok(Schema {
        version,
        flags,
        enums,
        classes,
        properties,
        events,
        strings,
        names,
        lr_types,
        parent_property_id,
        decoded_bytes,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn varuint(mut value: u64, out: &mut Vec<u8>) {
        loop {
            let mut b = (value & 127) as u8;
            value >>= 7;
            if value != 0 {
                b |= 128;
            }
            out.push(b);
            if value == 0 {
                break;
            }
        }
    }
    fn string(value: &str, out: &mut Vec<u8>) {
        varuint(value.len() as u64, out);
        out.extend_from_slice(value.as_bytes());
    }
    fn schema_bytes() -> Vec<u8> {
        let mut out = Vec::new();
        varuint(3, &mut out);
        out.extend_from_slice(&0x8877u64.to_le_bytes());
        varuint(1, &mut out);
        string("Color", &mut out);
        out.push(4);
        varuint(1, &mut out);
        varuint(2, &mut out);
        varuint(1, &mut out);
        string("Part", &mut out);
        varuint(2, &mut out);
        string("Material", &mut out);
        out.push(7);
        out.extend_from_slice(&0u16.to_le_bytes());
        string("Extension", &mut out);
        out.push(60);
        varuint(300, &mut out);
        out.extend_from_slice(&9u16.to_le_bytes());
        varuint(1, &mut out);
        string("Changed", &mut out);
        varuint(1, &mut out);
        out.push(28);
        out.extend_from_slice(&u16::MAX.to_le_bytes());
        varuint(1, &mut out);
        string("shared", &mut out);
        varuint(1, &mut out);
        string("part", &mut out);
        out.extend_from_slice(&1u32.to_le_bytes());
        out.extend_from_slice(&0u32.to_le_bytes());
        out.extend_from_slice(&2u32.to_le_bytes());
        out.extend_from_slice(&0u32.to_le_bytes());
        out.extend_from_slice(&1u32.to_le_bytes());
        varuint(1, &mut out);
        varuint(1, &mut out);
        varuint(2, &mut out);
        varuint(3, &mut out);
        varuint(4, &mut out);
        out
    }
    fn packet(expanded: &[u8]) -> Vec<u8> {
        let compressed = zstd::bulk::compress(expanded, 1).unwrap();
        let mut out = vec![SCHEMA_PACKET];
        out.extend_from_slice(&(compressed.len() as u32).to_le_bytes());
        out.extend_from_slice(&(expanded.len() as u32).to_le_bytes());
        out.extend_from_slice(&compressed);
        out
    }

    #[test]
    fn parses_typed_schema_and_python_compatible_json_shape() {
        let value = parse_schema(&packet(&schema_bytes())).unwrap();
        assert_eq!(value.version, 3);
        assert_eq!(value.flags, 0x8877);
        assert_eq!(
            value.enums[0],
            EnumDesc {
                id: 0,
                name: "Color".into(),
                bits: 4
            }
        );
        assert_eq!(value.classes[0].properties, [0, 1]);
        assert_eq!(value.classes[0].events, [0]);
        assert_eq!(value.classes[0].isr_properties, [0, 1]);
        assert_eq!(value.classes[0].unknown_u16, 9);
        assert_eq!(value.properties[0].enum_id, Some(0));
        assert_eq!(value.properties[1].lr_type, Some(300));
        assert_eq!(
            value.events[0].arguments,
            [EventArgument {
                type_id: 28,
                enum_id: u16::MAX
            }]
        );
        assert_eq!(value.strings, ["", "shared"]);
        assert_eq!(value.names, ["part"]);
        assert_eq!(value.lr_types, [vec![1, 2, 3, 4]]);
        let json = value.to_json();
        assert_eq!(json["parent_property_id"], 2);
        assert_eq!(json["properties"][1]["lr_type"], 300);
        assert_eq!(json["events"][0]["arguments"][0]["enum"], u16::MAX);
        assert_eq!(json["decoded_bytes"], schema_bytes().len());
    }

    #[test]
    fn rejects_invalid_headers_counts_references_and_trailing_data() {
        assert!(parse_schema(&[]).is_err());
        assert!(parse_schema(&[0]).is_err());
        assert!(parse_schema(&[0, SCHEMA_PACKET]).is_err());
        let mut unsupported = schema_bytes();
        unsupported[0] = 2;
        assert!(parse_schema(&packet(&unsupported))
            .unwrap_err()
            .to_string()
            .contains("Unsupported schema version"));
        let mut mismatch = schema_bytes();
        // version=1 byte + flags=8 + enum table (count/name/bits) precede class/property/event totals.
        mismatch[18] = 1;
        assert!(parse_schema(&packet(&mismatch)).is_err());
        let mut trailing = packet(&schema_bytes());
        trailing.push(0);
        assert!(parse_schema(&trailing).is_err());
    }
}
