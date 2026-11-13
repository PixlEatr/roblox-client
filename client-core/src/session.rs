//! tie packet decoding, world state and the bounded replay history together.
//! this is the data-only owner. auth and integrity replies live elsewhere.

use crate::{
    journal::{JournalEntry, JournalError, ReaderId, ReplayJournal},
    replication::ReplicationDecoder,
    schema::{parse_schema, Schema},
    world::{WorldStore, WorldView},
};
use serde_json::{json, Value};
use std::{
    error::Error,
    fmt,
    mem::size_of,
    ops::{Deref, DerefMut},
    sync::{Arc, OnceLock},
};

/// Immutable journal delta. Shared binary values stay in their canonical
/// payloads until an eager JSON consumer actually asks for them. Dereferencing
/// or serializing preserves the public, fully populated JSON representation.
#[derive(Clone, Debug)]
pub struct SessionDelta {
    compact: Value,
    shared: Vec<crate::packed_properties::CompactSharedProperty>,
    eager: OnceLock<Value>,
}

impl From<Value> for SessionDelta {
    fn from(compact: Value) -> Self {
        Self {
            compact,
            shared: Vec::new(),
            eager: OnceLock::new(),
        }
    }
}

impl SessionDelta {
    pub(crate) fn compact(&self) -> &Value {
        &self.compact
    }

    /// Lifecycle headers only. Property placeholders in these actions are
    /// private to the shared runtime; use Deref for complete property values.
    pub fn lifecycle_actions(&self) -> Option<&[Value]> {
        self.compact.get("actions")?.as_array().map(Vec::as_slice)
    }

    pub(crate) fn has_shared_field(&self, index: usize, key: &str) -> bool {
        self.shared
            .iter()
            .any(|binding| binding.action_index == index && binding.property == key)
    }

    pub(crate) fn action_owned(&self, index: usize) -> Result<Value, String> {
        let mut action = self
            .compact
            .get("actions")
            .and_then(Value::as_array)
            .and_then(|actions| actions.get(index))
            .ok_or("journal action missing")?
            .clone();
        for binding in self
            .shared
            .iter()
            .filter(|binding| binding.action_index == index)
        {
            if crate::lightweight::omitted(&binding.property) { continue; }
            crate::replication::materialize_shared_property(&mut action, binding)
                .map_err(|error| error.to_string())?;
        }
        crate::lightweight::filter_action(&mut action);
        Ok(action)
    }

    fn estimated_bytes(&self) -> usize {
        // Reserve for eager readers too, without expanding all values at once.
        self.shared
            .iter()
            .fold(estimate_value(&self.compact), |bytes, binding| {
                bytes
                    .saturating_add(size_of::<crate::packed_properties::CompactSharedProperty>())
                    .saturating_add(binding.property.len())
                    .saturating_add(binding.payload.stored_bytes())
                    .saturating_add(binding.payload.len().saturating_mul(2))
                    .saturating_add(128)
            })
    }
}

impl Deref for SessionDelta {
    type Target = Value;
    fn deref(&self) -> &Value {
        if self.shared.is_empty() {
            return &self.compact;
        }
        self.eager.get_or_init(|| {
            let mut value = self.compact.clone();
            let actions = value
                .get_mut("actions")
                .and_then(Value::as_array_mut)
                .expect("committed journal action array");
            for binding in &self.shared {
                crate::replication::materialize_shared_property(
                    &mut actions[binding.action_index],
                    binding,
                )
                .expect("committed shared property binding");
            }
            value
        })
    }
}

impl serde::Serialize for SessionDelta {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        <Self as Deref>::deref(self).serialize(serializer)
    }
}

impl DerefMut for SessionDelta {
    fn deref_mut(&mut self) -> &mut Value {
        if !self.shared.is_empty() {
            self.compact = <Self as Deref>::deref(self).clone();
            self.shared.clear();
            self.eager = OnceLock::new();
        }
        &mut self.compact
    }
}

const PACKET_SCHEMA: u8 = 151;
const PACKET_GLOBALS: u8 = 129;
const PACKET_REPLICATION: u8 = 131;
const DEFAULT_RECORD_LIMIT: usize = 4096;
const DEFAULT_BYTE_LIMIT: usize = 2 * 1024 * 1024;

/// A journaled receipt. The delta and event records are shared across readers;
/// they contain no full-world snapshot.
#[derive(Clone, Debug)]
pub struct SessionRecord {
    pub packet_kind: u8,
    pub world_sequence: u64,
    pub world_delta: SessionDelta,
    pub events: Vec<Value>,
    pub pings: Vec<Value>,
    pub tags: Vec<i32>,
    pub regions: Vec<Value>,
    pub metadata: Vec<Value>,
    pub property_changes: Vec<Value>,
}

/// Result of accepting one supported packet.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum IngestOutcome {
    SchemaInstalled {
        classes: usize,
        properties: usize,
    },
    GlobalsCommitted {
        packet_sequence: u64,
        world_sequence: u64,
    },
    ReplicationCommitted {
        packet_sequence: u64,
        world_sequence: u64,
    },
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum SessionError {
    EmptyPacket,
    UnsupportedPacket { kind: u8 },
    SchemaAlreadyInstalled,
    SchemaRequired,
    GlobalsRequired,
    InvalidSchema(String),
    InvalidPacket(String),
    InvalidWorld(String),
    Journal(JournalError),
    SequenceExhausted,
}

impl fmt::Display for SessionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::EmptyPacket => f.write_str("empty incoming packet"),
            Self::UnsupportedPacket { kind } => write!(f, "unsupported packet kind {kind}"),
            Self::SchemaAlreadyInstalled => f.write_str("network schema is already installed"),
            Self::SchemaRequired => f.write_str("network schema packet is required first"),
            Self::GlobalsRequired => f.write_str("globals packet is required before replication"),
            Self::InvalidSchema(message) => write!(f, "invalid network schema: {message}"),
            Self::InvalidPacket(message) => write!(f, "invalid data packet: {message}"),
            Self::InvalidWorld(message) => write!(f, "invalid world update: {message}"),
            Self::Journal(error) => write!(f, "replay journal: {error}"),
            Self::SequenceExhausted => f.write_str("session packet sequence exhausted"),
        }
    }
}

impl Error for SessionError {}

impl From<JournalError> for SessionError {
    fn from(value: JournalError) -> Self {
        Self::Journal(value)
    }
}

/// Mutable owner for a single local data-replication session.
pub struct SessionCore {
    decoder: Option<ReplicationDecoder>,
    world: Option<WorldStore>,
    journal: ReplayJournal<SessionRecord>,
    next_packet_sequence: u64,
    committed_packets: u64,
    rejected_packets: u64,
}

impl SessionCore {
    pub fn new() -> Result<Self, JournalError> {
        Self::with_limits(DEFAULT_RECORD_LIMIT, DEFAULT_BYTE_LIMIT)
    }

    pub fn with_limits(max_records: usize, max_bytes: usize) -> Result<Self, JournalError> {
        Ok(Self {
            decoder: None,
            world: None,
            journal: ReplayJournal::with_limits(max_records, max_bytes)?,
            next_packet_sequence: 1,
            committed_packets: 0,
            rejected_packets: 0,
        })
    }

    /// Install an already parsed schema, useful for controlled integrations
    /// that receive schema out of band. Production packet flow uses `ingest`.
    pub fn with_schema(
        schema: Schema,
        max_records: usize,
        max_bytes: usize,
    ) -> Result<Self, JournalError> {
        let mut session = Self::with_limits(max_records, max_bytes)?;
        session.decoder = Some(ReplicationDecoder::new(schema));
        Ok(session)
    }

    /// Decode and atomically accept a schema (151), globals (129), or ordinary
    /// replication (131) packet. Unsupported kinds are rejected explicitly.
    pub fn ingest(&mut self, payload: &[u8]) -> Result<IngestOutcome, SessionError> {
        let Some(&kind) = payload.first() else {
            self.rejected_packets = self.rejected_packets.saturating_add(1);
            return Err(SessionError::EmptyPacket);
        };
        let result = match kind {
            PACKET_SCHEMA => self.ingest_schema(payload),
            PACKET_GLOBALS => self.ingest_globals(payload),
            PACKET_REPLICATION => self.ingest_replication(payload),
            _ => Err(SessionError::UnsupportedPacket { kind }),
        };
        if result.is_err() {
            self.rejected_packets = self.rejected_packets.saturating_add(1);
        }
        result
    }

    fn ingest_schema(&mut self, payload: &[u8]) -> Result<IngestOutcome, SessionError> {
        if self.decoder.is_some() {
            return Err(SessionError::SchemaAlreadyInstalled);
        }
        let schema = parse_schema(payload)
            .map_err(|error| SessionError::InvalidSchema(error.to_string()))?;
        let classes = schema.classes.len();
        let properties = schema.properties.len();
        self.decoder = Some(ReplicationDecoder::new(schema));
        Ok(IngestOutcome::SchemaInstalled {
            classes,
            properties,
        })
    }

    fn ingest_globals(&mut self, payload: &[u8]) -> Result<IngestOutcome, SessionError> {
        let decoder = self.decoder.as_ref().ok_or(SessionError::SchemaRequired)?;
        let mut globals = decoder
            .globals(payload)
            .map_err(|error| SessionError::InvalidPacket(error.to_string()))?;
        decoder
            .validate_globals(&globals)
            .map_err(|error| SessionError::InvalidPacket(error.to_string()))?;
        let base = self
            .world
            .as_ref()
            .map(Clone::clone)
            .unwrap_or_else(empty_world);
        let mut staged_world = base;
        staged_world
            .apply_delta(&globals.world_delta)
            .map_err(SessionError::InvalidWorld)?;
        let packet_sequence = self.next_packet_sequence;
        let next_packet_sequence = packet_sequence
            .checked_add(1)
            .ok_or(SessionError::SequenceExhausted)?;
        let world_sequence = staged_world.sequence();
        let record = Arc::new(SessionRecord {
            packet_kind: PACKET_GLOBALS,
            world_sequence,
            world_delta: std::mem::take(&mut globals.world_delta).into(),
            events: Vec::new(),
            pings: Vec::new(),
            tags: Vec::new(),
            regions: Vec::new(),
            metadata: Vec::new(),
            property_changes: Vec::new(),
        });
        let estimate = estimate_record(&record);
        self.journal
            .append_arc(packet_sequence, record, estimate)
            .map_err(|failure| SessionError::Journal(failure.error))?;
        // The accepted delta itself is retained in the journal, so the
        // World's dispatch staging vector is redundant in this owner.
        staged_world.acknowledge_events(world_sequence);
        // These commits are prevalidated against the exclusively borrowed
        // decoder; after queue acceptance, they cannot race another writer.
        self.decoder
            .as_mut()
            .expect("decoder checked above")
            .commit_globals(&globals)
            .map_err(|error| SessionError::InvalidPacket(error.to_string()))?;
        self.world = Some(staged_world);
        self.next_packet_sequence = next_packet_sequence;
        self.committed_packets += 1;
        Ok(IngestOutcome::GlobalsCommitted {
            packet_sequence,
            world_sequence,
        })
    }

    fn ingest_replication(&mut self, payload: &[u8]) -> Result<IngestOutcome, SessionError> {
        let decoder = self.decoder.as_ref().ok_or(SessionError::SchemaRequired)?;
        let world = self.world.clone().ok_or(SessionError::GlobalsRequired)?;
        let transaction = decoder
            .packet_compact(payload)
            .map_err(|error| SessionError::InvalidPacket(error.to_string()))?;
        decoder
            .validate_commit(&transaction.packet)
            .map_err(|error| SessionError::InvalidPacket(error.to_string()))?;
        self.commit_compact_transaction(PACKET_REPLICATION, transaction, &world)
    }

    fn commit_compact_transaction(
        &mut self,
        kind: u8,
        compact: crate::replication::CompactPacketTransaction,
        current_world: &WorldStore,
    ) -> Result<IngestOutcome, SessionError> {
        let mut staged_world = current_world.clone();
        {
            let decoder = self.decoder.as_ref().ok_or(SessionError::SchemaRequired)?;
            let shared = decoder.shared_payload_resolver(&compact.packet);
            staged_world
                .apply_compact_delta(
                    &compact.packet.world_delta,
                    Some(&shared),
                    &compact.shared_properties,
                )
                .map_err(SessionError::InvalidWorld)?;
        }
        // Keep the same immutable shared payloads in the accepted journal.
        // The staged apply has validated every placeholder and action index.
        let mut transaction = compact.packet;
        let shared = compact.shared_properties;
        let packet_sequence = self.next_packet_sequence;
        let next_packet_sequence = packet_sequence
            .checked_add(1)
            .ok_or(SessionError::SequenceExhausted)?;
        let world_sequence = staged_world.sequence();
        let record = Arc::new(SessionRecord {
            packet_kind: kind,
            world_sequence,
            world_delta: SessionDelta {
                compact: std::mem::take(&mut transaction.world_delta),
                shared,
                eager: OnceLock::new(),
            },
            events: std::mem::take(&mut transaction.events),
            pings: std::mem::take(&mut transaction.pings),
            tags: std::mem::take(&mut transaction.tags),
            regions: std::mem::take(&mut transaction.regions),
            metadata: std::mem::take(&mut transaction.metadata),
            property_changes: std::mem::take(&mut transaction.property_changes),
        });
        let estimate = estimate_record(&record);
        self.journal
            .append_arc(packet_sequence, record, estimate)
            .map_err(|failure| SessionError::Journal(failure.error))?;
        // The replay record owns the complete accepted delta. Keeping its
        // event actions in WorldStore as well would retain them twice and
        // eventually hit WorldStore's independent pending-event cap.
        staged_world.acknowledge_events(world_sequence);
        self.decoder
            .as_mut()
            .expect("decoder checked above")
            .commit(&transaction)
            .map_err(|error| SessionError::InvalidPacket(error.to_string()))?;
        self.world = Some(staged_world);
        self.next_packet_sequence = next_packet_sequence;
        self.committed_packets += 1;
        Ok(IngestOutcome::ReplicationCommitted {
            packet_sequence,
            world_sequence,
        })
    }

    pub fn retain(&mut self, after_sequence: u64) -> Result<ReaderId, JournalError> {
        self.journal.retain(after_sequence)
    }

    pub fn read_after(
        &self,
        reader: ReaderId,
        after_sequence: u64,
        limit: usize,
    ) -> Result<Vec<Arc<JournalEntry<SessionRecord>>>, JournalError> {
        self.journal.read_after(reader, after_sequence, limit)
    }

    pub fn acknowledge(
        &mut self,
        reader: ReaderId,
        through_sequence: u64,
    ) -> Result<(), JournalError> {
        self.journal.acknowledge(reader, through_sequence)
    }

    pub fn release(&mut self, reader: ReaderId) -> Result<(), JournalError> {
        self.journal.release(reader)
    }

    pub fn world_view(&self) -> Option<WorldView> {
        self.world.as_ref().map(WorldStore::view)
    }
    pub fn storage_profile(&self) -> Value {
        let mut profile = self
            .world
            .as_ref()
            .map(WorldStore::storage_profile)
            .unwrap_or_else(|| json!({}));
        if let Some(decoder) = &self.decoder {
            profile["shared_string_count"] = json!(decoder.shared_string_count());
            profile["shared_string_payload_bytes"] = json!(decoder.shared_string_payload_bytes());
            profile["shared_string_stored_bytes"] = json!(decoder.shared_string_stored_bytes());
        }
        profile
    }
    pub fn snapshot(&self) -> Option<Value> {
        self.world.as_ref().map(WorldStore::to_snapshot)
    }
    pub fn journal_stats(&self) -> crate::journal::JournalStats {
        self.journal.stats()
    }
    pub fn committed_packets(&self) -> u64 {
        self.committed_packets
    }
    pub fn rejected_packets(&self) -> u64 {
        self.rejected_packets
    }
    pub fn world_sequence(&self) -> Option<u64> {
        self.world.as_ref().map(WorldStore::sequence)
    }
    pub fn scope(&self) -> Option<u64> {
        self.decoder
            .as_ref()
            .and_then(ReplicationDecoder::client_scope)
    }
    pub fn set_remote_success_variants_preserved(&mut self, preserved: bool) {
        if let Some(decoder) = self.decoder.as_mut() {
            decoder.preserve_remote_success_variants = preserved;
        }
    }
    pub fn schema_installed(&self) -> bool {
        self.decoder.is_some()
    }
    pub fn schema(&self) -> Option<&Schema> {
        self.decoder.as_ref().map(|decoder| &decoder.schema)
    }
}

fn empty_world() -> WorldStore {
    WorldStore::from_snapshot(&json!({"version":1,"sequence":0,"nodes":[]}))
        .expect("built-in empty world snapshot is valid")
}

/// Conservative logical-size estimate; this is journal accounting, not RSS.
fn estimate_record(record: &SessionRecord) -> usize {
    size_of::<SessionRecord>()
        .saturating_add(record.world_delta.estimated_bytes())
        .saturating_add(record.events.iter().map(estimate_value).sum::<usize>())
        .saturating_add(record.pings.iter().map(estimate_value).sum::<usize>())
        .saturating_add(record.regions.iter().map(estimate_value).sum::<usize>())
        .saturating_add(record.metadata.iter().map(estimate_value).sum::<usize>())
        .saturating_add(
            record
                .property_changes
                .iter()
                .map(estimate_value)
                .sum::<usize>(),
        )
        .saturating_add(record.tags.len().saturating_mul(size_of::<i32>()))
}

fn estimate_value(value: &Value) -> usize {
    match value {
        Value::Null => size_of::<Value>(),
        Value::Bool(_) => size_of::<Value>(),
        Value::Number(_) => size_of::<Value>(),
        Value::String(text) => size_of::<Value>().saturating_add(text.len()),
        Value::Array(values) => {
            size_of::<Value>().saturating_add(values.iter().map(estimate_value).sum::<usize>())
        }
        Value::Object(values) => size_of::<Value>().saturating_add(
            values
                .iter()
                .map(|(key, value)| key.len().saturating_add(estimate_value(value)))
                .sum::<usize>(),
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::schema::{ClassDesc, EnumDesc, EventDesc, PropertyDesc};

    fn test_schema(with_event: bool) -> Schema {
        Schema {
            version: 3,
            flags: 0,
            enums: Vec::<EnumDesc>::new(),
            classes: vec![
                ClassDesc {
                    id: 0,
                    name: "Part".into(),
                    properties: vec![0],
                    events: if with_event { vec![0] } else { vec![] },
                    isr_properties: vec![],
                    unknown_u16: 0,
                },
                ClassDesc {
                    id: 1,
                    name: "Folder".into(),
                    properties: vec![],
                    events: vec![],
                    isr_properties: vec![],
                    unknown_u16: 0,
                },
            ],
            properties: vec![PropertyDesc {
                id: 0,
                class: 0,
                name: "Transparency".into(),
                type_id: 11,
                enum_id: None,
                lr_type: None,
            }],
            events: if with_event {
                vec![EventDesc {
                    id: 0,
                    class: 0,
                    name: "OnClientEvent".into(),
                    arguments: vec![],
                }]
            } else {
                vec![]
            },
            strings: vec![],
            names: vec![],
            lr_types: vec![],
            parent_property_id: 1,
            decoded_bytes: 0,
        }
    }

    fn create_packet() -> Vec<u8> {
        let mut bytes = vec![131, 2, 1];
        bytes.extend_from_slice(&7_u32.to_le_bytes());
        bytes.extend_from_slice(&0_u16.to_le_bytes());
        bytes.push(0);
        bytes.extend_from_slice(&0.25_f32.to_le_bytes());
        bytes.extend_from_slice(&[255, 255, 0, 0]);
        bytes
    }

    fn append_varuint(mut value: u64, out: &mut Vec<u8>) {
        while value >= 0x80 {
            out.push((value as u8) | 0x80);
            value >>= 7;
        }
        out.push(value as u8);
    }

    fn shared_text_create_packet(text: &[u8]) -> Vec<u8> {
        let key = crate::shared_payload::digest_raw(text);
        let mut bytes = vec![131, 2, 1];
        bytes.extend_from_slice(&7u32.to_le_bytes());
        bytes.extend_from_slice(&0u16.to_le_bytes());
        bytes.push(0); // property order index
        bytes.extend_from_slice(&key);
        bytes.extend_from_slice(&[255, 255]);
        bytes.extend_from_slice(&key);
        bytes.push(0); // inline shared payload
        append_varuint(text.len() as u64, &mut bytes);
        bytes.extend_from_slice(text);
        bytes.push(0); // nil parent
        bytes.push(0); // packet terminator
        bytes
    }

    fn different_class_create_packet() -> Vec<u8> {
        vec![131, 2, 1, 7, 0, 0, 0, 1, 0, 255, 255, 0, 0]
    }

    fn remove_packet() -> Vec<u8> {
        vec![131, 1, 1, 7, 0, 0, 0, 0]
    }
    fn event_packet() -> Vec<u8> {
        vec![131, 7, 1, 7, 0, 0, 0, 0, 0, 0]
    }
    fn empty_globals() -> Vec<u8> {
        vec![129, 0, 0, 0, 0, 0, 0, 0, 1, 0]
    }

    #[test]
    fn globals_create_update_remove_and_event_receipts_are_journaled() {
        let mut session = SessionCore::with_schema(test_schema(true), 32, 64 * 1024).unwrap();
        session.ingest(&empty_globals()).unwrap();
        session.ingest(&create_packet()).unwrap();
        session.ingest(&event_packet()).unwrap();
        let view = session.world_view().unwrap();
        assert_eq!(view.get_property("1:7", "Transparency"), Some(json!(0.25)));
        let mut property_packet = vec![131, 3, 1];
        property_packet.extend_from_slice(&7_u32.to_le_bytes());
        property_packet.extend_from_slice(&0_u16.to_le_bytes());
        property_packet.push(0);
        property_packet.extend_from_slice(&0.75_f32.to_le_bytes());
        property_packet.push(0);
        session.ingest(&property_packet).unwrap();
        assert_eq!(
            session
                .world_view()
                .unwrap()
                .get_property("1:7", "Transparency"),
            Some(json!(0.75))
        );
        session.ingest(&remove_packet()).unwrap();
        assert!(!session.world_view().unwrap().contains("1:7"));
        let reader = session.retain(0).unwrap();
        let rows = session.read_after(reader, 0, 8).unwrap();
        assert_eq!(rows.len(), 5);
        assert_eq!(rows[1].value.world_delta["actions"][0]["kind"], "create");
        assert_eq!(rows[2].value.events[0]["name"], "OnClientEvent");
        assert_eq!(rows[3].value.world_delta["actions"][0]["kind"], "set");
        assert_eq!(rows[4].value.world_delta["actions"][0]["kind"], "remove");
        session.release(reader).unwrap();
    }

    #[test]
    fn storage_profile_reports_logical_shared_payload_bytes() {
        let session = SessionCore::with_schema(test_schema(false), 4, 4096).unwrap();
        let profile = session.storage_profile();
        assert_eq!(profile["shared_string_count"], 0);
        assert_eq!(profile["shared_string_payload_bytes"], 0);
        assert_eq!(profile["shared_string_stored_bytes"], 0);
    }

    #[test]
    fn malformed_unsupported_and_backpressured_packets_leave_world_unchanged() {
        let mut session = SessionCore::with_schema(test_schema(false), 2, 2048).unwrap();
        session.ingest(&empty_globals()).unwrap();
        session.ingest(&create_packet()).unwrap();
        let before = session.snapshot().unwrap();
        let reader = session.retain(0).unwrap();
        let error = session.ingest(&[131, 2]).unwrap_err();
        assert!(matches!(error, SessionError::InvalidPacket(_)));
        assert_eq!(session.snapshot().unwrap(), before);
        assert!(matches!(
            session.ingest(&[155, 0]),
            Err(SessionError::UnsupportedPacket { kind: 155 })
        ));
        assert_eq!(session.snapshot().unwrap(), before);
        let invalid_world = session
            .ingest(&different_class_create_packet())
            .unwrap_err();
        assert!(
            matches!(invalid_world, SessionError::InvalidWorld(_)),
            "expected invalid world transaction, got {invalid_world:?}"
        );
        assert_eq!(session.snapshot().unwrap(), before);
        let full = session.ingest(&[131, 0]).unwrap_err();
        assert!(matches!(
            full,
            SessionError::Journal(JournalError::Backpressure { .. })
        ));
        assert_eq!(session.snapshot().unwrap(), before);
        assert_eq!(session.world_sequence(), Some(1));
        session.acknowledge(reader, 2).unwrap();
        session.ingest(&remove_packet()).unwrap();
        assert!(!session.world_view().unwrap().contains("1:7"));
        session.release(reader).unwrap();
    }

    #[test]
    fn session_compact_path_publishes_exact_eager_receipt_after_world_apply() {
        let mut schema = test_schema(false);
        schema.properties[0].type_id = 46;
        let mut session = SessionCore::with_schema(schema, 8, 1024 * 1024).unwrap();
        session.ingest(&empty_globals()).unwrap();
        let reader = session.retain(0).unwrap();
        let text = "session compact shared text ".repeat(32).into_bytes();
        let previous = session.world_view().unwrap();
        session.ingest(&shared_text_create_packet(&text)).unwrap();

        let expected = serde_json::json!({
            "$bytes_hex": text.iter().map(|byte| format!("{byte:02x}")).collect::<String>()
        });
        assert_eq!(
            session
                .world_view()
                .unwrap()
                .read_only_node("1:7")
                .unwrap()
                .property("Transparency"),
            Some(expected.clone())
        );
        let rows = session.read_after(reader, 0, 8).unwrap();
        let receipt = rows
            .iter()
            .find(|row| row.value.packet_kind == PACKET_REPLICATION)
            .unwrap();
        assert!(!receipt.value.world_delta.shared.is_empty());
        assert!(receipt.value.world_delta.eager.get().is_none());
        let mut normalizer =
            crate::runtime_delta::RuntimeDeltaNormalizer::new(session.schema().unwrap()).unwrap();
        let normalized = normalizer
            .normalize_for_shared_view_worker(
                &previous,
                &session.world_view().unwrap(),
                &receipt.value,
                false,
            )
            .unwrap();
        assert_eq!(normalized.compact_create_ids, vec!["1:7"]);
        assert!(
            receipt.value.world_delta.eager.get().is_none(),
            "shared VM path must not inflate the journal"
        );
        assert!(receipt.value.world_delta.lifecycle_actions().is_some());
        assert!(
            receipt.value.world_delta.eager.get().is_none(),
            "lifecycle observation must stay lazy"
        );
        assert_eq!(
            receipt.value.world_delta["actions"][0]["node"]["properties"]["Transparency"],
            expected
        );
        let serialized = serde_json::to_value(&receipt.value.world_delta).unwrap();
        assert_eq!(
            serialized["actions"][0]["node"]["properties"]["Transparency"],
            expected
        );
        let mut copied = receipt.value.world_delta.clone();
        copied["actions"][0]["node"]["properties"]["Transparency"] = json!("local");
        assert_eq!(
            receipt.value.world_delta["actions"][0]["node"]["properties"]["Transparency"],
            expected
        );
        assert_eq!(
            copied["actions"][0]["node"]["properties"]["Transparency"],
            "local"
        );
        session.acknowledge(reader, receipt.sequence).unwrap();
        session.release(reader).unwrap();
    }

    #[test]
    fn compact_shared_journal_backpressure_leaves_session_and_decoder_unchanged() {
        let mut schema = test_schema(false);
        schema.properties[0].type_id = 46;
        let mut session = SessionCore::with_schema(schema, 1, 1024 * 1024).unwrap();
        let reader = session.retain(0).unwrap();
        session.ingest(&empty_globals()).unwrap();
        let snapshot_before = session.snapshot().unwrap();
        let profile_before = session.storage_profile();
        let world_sequence_before = session.world_sequence();
        let decoder_sequence_before = session.decoder.as_ref().unwrap().sequence();
        let committed_before = session.committed_packets();
        let journal_before = session.journal_stats();
        let text = "rejected compact shared payload ".repeat(32).into_bytes();

        let error = session
            .ingest(&shared_text_create_packet(&text))
            .unwrap_err();
        assert!(matches!(
            error,
            SessionError::Journal(JournalError::Backpressure { .. })
        ));
        assert_eq!(session.snapshot().unwrap(), snapshot_before);
        let profile_after = session.storage_profile();
        assert_eq!(
            profile_after["world_state_blake2b512"],
            profile_before["world_state_blake2b512"]
        );
        // Staging may intern node strings into the shared weak interner before
        // journal backpressure rejects the packet. Those expired Weak entries
        // affect this estimate until their bucket is next visited, but they do
        // not change the committed world, decoder, or journal state.
        let mut profile_after_state = profile_after;
        let mut profile_before_state = profile_before;
        profile_after_state
            .as_object_mut()
            .unwrap()
            .remove("estimated_world_bytes");
        profile_before_state
            .as_object_mut()
            .unwrap()
            .remove("estimated_world_bytes");
        assert_eq!(profile_after_state, profile_before_state);
        assert_eq!(session.world_sequence(), world_sequence_before);
        assert_eq!(
            session.decoder.as_ref().unwrap().sequence(),
            decoder_sequence_before
        );
        assert_eq!(session.committed_packets(), committed_before);
        assert_eq!(session.journal_stats(), journal_before);
        session.acknowledge(reader, 1).unwrap();
        session.release(reader).unwrap();
    }
}
