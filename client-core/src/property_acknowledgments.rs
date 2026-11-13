//! Session-local CFrame receipt acknowledgements with instance-generation and
//! transport accounting. These receipts do not confirm server physics acceptance.
use crate::{
    replication_out::{guid, Guid},
    schema::Schema,
    wire::WireError,
    world::WorldView,
};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex},
};

const MAX_PACKET_BYTES: usize = 16_384;
const DEFAULT_MAX_ENTRIES: usize = 100_000;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct VersionEntry {
    identity: Option<u64>,
    version: u32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct PendingEntry {
    key: Guid,
    identity: Option<u64>,
    version: u32,
    local_step: u32,
    generation: u64,
}

#[derive(Debug, Default)]
struct State {
    versions: BTreeMap<(String, u16), VersionEntry>,
    pending: BTreeMap<(String, u16), PendingEntry>,
    in_flight: Option<(u64, Vec<((String, u16), u64)>)>,
    next_id: u64,
    next_generation: u64,
    observed: u64,
    resets: u64,
    sent: u64,
    packets_sent: u64,
    max_entries: usize,
    error: Option<&'static str>,
}

#[derive(Clone, Debug, Default)]
pub struct PropertyAcknowledgments(Arc<Mutex<State>>);

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PreparedPropertyAcks {
    id: u64,
    payload: Vec<u8>,
}

impl PreparedPropertyAcks {
    pub fn id(&self) -> u64 {
        self.id
    }
    pub fn payload(&self) -> &[u8] {
        &self.payload
    }
}

impl PropertyAcknowledgments {
    pub fn new() -> Self {
        Self::with_limit(DEFAULT_MAX_ENTRIES).expect("default receipt limit is valid")
    }

    pub fn with_limit(max_entries: usize) -> Result<Self, WireError> {
        if max_entries == 0 {
            return Err(WireError::new("Property receipt limit must be positive"));
        }
        let state = State {
            max_entries,
            next_id: 1,
            next_generation: 1,
            ..State::default()
        };
        Ok(Self(Arc::new(Mutex::new(state))))
    }

    pub fn observe(
        &self,
        schema: &Schema,
        view: &WorldView,
        changes: &[Value],
        local_step: u32,
    ) -> Result<(), WireError> {
        let mut state = self
            .0
            .lock()
            .map_err(|_| WireError::new("Property receipt queue lock unavailable"))?;
        if state.error.is_some() {
            return Ok(());
        }
        prune(&mut state, view);
        for change in changes {
            let Some((token, identity, key, reset)) = eligible(schema, view, change)? else {
                continue;
            };
            if !state.versions.contains_key(&token) && state.versions.len() >= state.max_entries {
                prune(&mut state, view);
                if state.versions.len() >= state.max_entries {
                    state.error = Some("too_many_property_receipt_counters");
                    return Err(WireError::new("Too many property receipt counters"));
                }
            }
            let previous = state.versions.get(&token).copied();
            let previous_version = previous
                .filter(|entry| entry.identity == identity)
                .map_or(0, |entry| entry.version);
            let version = if reset {
                0
            } else {
                previous_version.wrapping_add(1)
            };
            let generation = state.next_generation;
            state.next_generation = state
                .next_generation
                .checked_add(1)
                .ok_or_else(|| WireError::new("Property receipt generation exhausted"))?;
            state
                .versions
                .insert(token.clone(), VersionEntry { identity, version });
            state.pending.insert(
                token,
                PendingEntry {
                    key,
                    identity,
                    version,
                    local_step,
                    generation,
                },
            );
            state.observed = state.observed.saturating_add(1);
            state.resets = state.resets.saturating_add(u64::from(reset));
        }
        Ok(())
    }

    pub fn prepare(&self, view: &WorldView) -> Result<Option<PreparedPropertyAcks>, WireError> {
        let mut state = self
            .0
            .lock()
            .map_err(|_| WireError::new("Property receipt queue lock unavailable"))?;
        if state.error.is_some() || state.in_flight.is_some() || state.pending.is_empty() {
            return Ok(None);
        }
        prune(&mut state, view);
        if state.pending.is_empty() {
            return Ok(None);
        }
        let id = state.next_id;
        state.next_id = state
            .next_id
            .checked_add(1)
            .ok_or_else(|| WireError::new("Property receipt token exhausted"))?;
        let mut payload = vec![0x83];
        let mut tokens = Vec::new();
        for (token, pending) in &state.pending {
            let body = encode_property_ack_item(
                pending.key,
                token.1,
                pending.version,
                pending.local_step,
            )?;
            if !tokens.is_empty() && payload.len() + body.len() + 1 > MAX_PACKET_BYTES {
                break;
            }
            payload.extend_from_slice(&body);
            tokens.push((token.clone(), pending.generation));
        }
        payload.push(0);
        state.in_flight = Some((id, tokens));
        Ok(Some(PreparedPropertyAcks { id, payload }))
    }

    pub fn commit(&self, id: u64, written: bool) -> Result<(), WireError> {
        let mut state = self
            .0
            .lock()
            .map_err(|_| WireError::new("Property receipt queue lock unavailable"))?;
        let Some((expected, tokens)) = state.in_flight.take() else {
            return Err(WireError::new("No property receipt write in flight"));
        };
        if expected != id {
            state.in_flight = Some((expected, tokens));
            return Err(WireError::new("Stale property receipt write token"));
        }
        if !written {
            state.error = Some("transport_submission_ambiguous_no_retry");
            return Ok(());
        }
        let count = tokens.len() as u64;
        for (token, generation) in tokens {
            if state
                .pending
                .get(&token)
                .is_some_and(|entry| entry.generation == generation)
            {
                state.pending.remove(&token);
            }
        }
        state.sent = state.sent.saturating_add(count);
        state.packets_sent = state.packets_sent.saturating_add(1);
        Ok(())
    }

    pub fn report(&self) -> Value {
        let Ok(state) = self.0.lock() else {
            return json!({"error":"observer_lock_unavailable"});
        };
        json!({
            "received_cframe_changes":state.observed,
            "received_resets":state.resets,
            "replies_sent":state.sent,
            "packets_sent":state.packets_sent,
            "pending":state.pending.len(),
            "tracked_properties":state.versions.len(),
            "error":state.error,
            "source":"current_session_received_property_changes",
            "clock_source":"completed_local_simulation_intervals",
            "server_physics_acceptance_confirmed":false
        })
    }
}

fn eligible(
    schema: &Schema,
    view: &WorldView,
    change: &Value,
) -> Result<Option<((String, u16), Option<u64>, Guid, bool)>, WireError> {
    let id = change
        .get("id")
        .and_then(Value::as_str)
        .ok_or_else(|| WireError::new("Property receipt instance id is missing"))?;
    let property_index = change
        .get("property_index")
        .and_then(Value::as_u64)
        .ok_or_else(|| WireError::new("Property receipt property index is missing"))?;
    let property_index = u16::try_from(property_index)
        .map_err(|_| WireError::new("Property receipt property index is invalid"))?;
    let reset = change
        .get("reset_version")
        .and_then(Value::as_bool)
        .ok_or_else(|| WireError::new("Property receipt reset flag is missing"))?;
    let Some(node) = view.read_only_node(id) else {
        return Ok(None);
    };
    let class_name = node.class_name();
    let identity = node.created_sequence();
    let Some(class) = schema.classes.iter().find(|class| class.name == class_name) else {
        return Ok(None);
    };
    if class.unknown_u16 != property_index {
        return Ok(None);
    }
    let Some(property) = schema.properties.get(property_index as usize) else {
        return Ok(None);
    };
    if property.name != "CFrame" || property.type_id != 27 {
        return Ok(None);
    }
    Ok(Some((
        (id.to_owned(), property_index),
        identity,
        parse_guid(id)?,
        reset,
    )))
}

fn parse_guid(id: &str) -> Result<Guid, WireError> {
    let (scope, index) = id
        .split_once(':')
        .ok_or_else(|| WireError::new("Property receipt instance id is invalid"))?;
    let scope = scope
        .parse::<u32>()
        .map_err(|_| WireError::new("Property receipt scope is invalid"))?;
    let index = index
        .parse::<u32>()
        .map_err(|_| WireError::new("Property receipt instance id is invalid"))?;
    if scope == 0 {
        return Err(WireError::new("Property receipt scope is invalid"));
    }
    Ok((scope, index))
}

fn prune(state: &mut State, view: &WorldView) {
    state.versions.retain(|(id, _), entry| {
        view.read_only_node(id)
            .is_some_and(|node| node.created_sequence() == entry.identity)
    });
    state.pending.retain(|(id, _), entry| {
        view.read_only_node(id)
            .is_some_and(|node| node.created_sequence() == entry.identity)
    });
}

pub fn encode_property_ack(
    key: Guid,
    property_id: u16,
    version: u32,
    local_step: u32,
) -> Result<Vec<u8>, WireError> {
    let mut packet = vec![0x83];
    packet.extend_from_slice(&encode_property_ack_item(
        key,
        property_id,
        version,
        local_step,
    )?);
    packet.push(0);
    Ok(packet)
}

fn encode_property_ack_item(
    key: Guid,
    property_id: u16,
    version: u32,
    local_step: u32,
) -> Result<Vec<u8>, WireError> {
    if property_id == u16::MAX {
        return Err(WireError::new("Invalid property acknowledgment property"));
    }
    let mut item = vec![10];
    item.extend_from_slice(&guid(Some(key))?);
    item.extend_from_slice(&property_id.to_le_bytes());
    item.push(1);
    item.extend_from_slice(&encode_unsigned(version));
    item.extend_from_slice(&local_step.to_le_bytes());
    Ok(item)
}

fn encode_unsigned(mut value: u32) -> Vec<u8> {
    let mut bytes = Vec::with_capacity(5);
    while value >= 0x80 {
        bytes.push((value as u8 & 0x7f) | 0x80);
        value >>= 7;
    }
    bytes.push(value as u8);
    bytes
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        schema::{ClassDesc, PropertyDesc},
        world::WorldStore,
    };

    fn schema() -> Schema {
        Schema {
            version: 3,
            flags: 0,
            enums: vec![],
            classes: vec![ClassDesc {
                id: 0,
                name: "Part".into(),
                properties: vec![0, 1],
                events: vec![],
                isr_properties: vec![],
                unknown_u16: 0,
            }],
            properties: vec![
                PropertyDesc {
                    id: 0,
                    class: 0,
                    name: "CFrame".into(),
                    type_id: 27,
                    enum_id: None,
                    lr_type: None,
                },
                PropertyDesc {
                    id: 1,
                    class: 0,
                    name: "Anchored".into(),
                    type_id: 9,
                    enum_id: None,
                    lr_type: None,
                },
            ],
            events: vec![],
            strings: vec![],
            names: vec![],
            lr_types: vec![],
            parent_property_id: 2,
            decoded_bytes: 0,
        }
    }

    fn world() -> WorldStore {
        WorldStore::from_snapshot(&json!({"version":1,"sequence":0,"nodes":[
            {"id":"1:2","class_name":"Part","parent":null,"properties":{}},
            {"id":"123:456","class_name":"Part","parent":null,"properties":{}}
        ]}))
        .unwrap()
    }

    fn change(id: &str, reset: bool) -> Value {
        json!({"id":id,"property_index":0,"reset_version":reset})
    }

    #[test]
    fn native_layout_matches_reference_and_coalesces_versions() {
        assert_eq!(
            encode_property_ack((1, 2), 3, 128, 240)
                .unwrap()
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect::<String>(),
            "830a01020000000300018001f000000000"
        );
        let world = world();
        let queue = PropertyAcknowledgments::new();
        queue
            .observe(
                &schema(),
                &world.view(),
                &[change("1:2", false), change("1:2", false)],
                500,
            )
            .unwrap();
        queue
            .observe(&schema(), &world.view(), &[change("1:2", true)], 501)
            .unwrap();
        let prepared = queue.prepare(&world.view()).unwrap().unwrap();
        assert_eq!(
            prepared.payload(),
            encode_property_ack((1, 2), 0, 0, 501).unwrap().as_slice()
        );
        queue.commit(prepared.id(), true).unwrap();
        assert_eq!(queue.report()["received_cframe_changes"], 3);
        assert_eq!(queue.report()["received_resets"], 1);
        assert_eq!(queue.report()["replies_sent"], 1);
        assert!(queue.prepare(&world.view()).unwrap().is_none());
    }

    #[test]
    fn stale_instances_and_unrelated_properties_are_not_acknowledged() {
        let mut world = world();
        let queue = PropertyAcknowledgments::new();
        queue
            .observe(&schema(), &world.view(), &[change("1:2", false)], 17)
            .unwrap();
        world
            .apply_delta(&json!({"version":1,"delta":true,"sequence":1,"actions":[{"sequence":1,"kind":"remove","id":"1:2"}]}))
            .unwrap();
        assert!(queue.prepare(&world.view()).unwrap().is_none());
        queue
            .observe(&schema(), &world.view(), &[change("123:456", false)], 18)
            .unwrap();
        let prepared = queue.prepare(&world.view()).unwrap().unwrap();
        assert_eq!(
            prepared.payload(),
            encode_property_ack((123, 456), 0, 1, 18)
                .unwrap()
                .as_slice()
        );
        assert_eq!(queue.report()["received_cframe_changes"], 2);
        assert_eq!(
            encode_property_ack((1, 2), u16::MAX, 0, 0)
                .unwrap_err()
                .to_string(),
            "Invalid property acknowledgment property"
        );
    }

    #[test]
    fn failed_writes_are_terminal_and_success_commits_only_matching_generations() {
        let world = world();
        let queue = PropertyAcknowledgments::new();
        queue
            .observe(&schema(), &world.view(), &[change("1:2", false)], 5)
            .unwrap();
        let first = queue.prepare(&world.view()).unwrap().unwrap();
        queue
            .observe(&schema(), &world.view(), &[change("1:2", false)], 6)
            .unwrap();
        queue.commit(first.id(), true).unwrap();
        assert_eq!(queue.report()["pending"], 1);
        let second = queue.prepare(&world.view()).unwrap().unwrap();
        assert_eq!(
            second.payload(),
            encode_property_ack((1, 2), 0, 2, 6).unwrap().as_slice()
        );
        queue.commit(second.id(), false).unwrap();
        assert_eq!(
            queue.report()["error"],
            "transport_submission_ambiguous_no_retry"
        );
        assert!(queue.prepare(&world.view()).unwrap().is_none());
    }

    #[test]
    fn configured_counter_limit_is_enforced() {
        let world = world();
        let queue = PropertyAcknowledgments::with_limit(1).unwrap();
        queue
            .observe(&schema(), &world.view(), &[change("1:2", false)], 1)
            .unwrap();
        assert!(queue
            .observe(&schema(), &world.view(), &[change("123:456", false)], 2)
            .is_err());
        assert_eq!(
            queue.report()["error"],
            "too_many_property_receipt_counters"
        );
        assert!(PropertyAcknowledgments::with_limit(0).is_err());
    }
}
