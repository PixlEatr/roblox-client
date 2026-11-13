//! turn accepted world updates into the shape the luau bridge understands.
//! the view is from just before this batch. we only use it to look up classes,
//! so this path never rebuilds or serializes the whole world.

use crate::{
    runtime_snapshot::{normalize_node_values, validate_normalized_node_values},
    schema::Schema,
    session::SessionRecord,
    world::WorldView,
};
use serde_json::{json, Map, Value};
use std::collections::{HashMap, HashSet};
use std::sync::Arc;

pub type ClassPropertyTypeIndex = HashMap<String, HashMap<String, u8>>;

/// A normalized worker update plus packet side channels that must remain
/// associated with this receipt batch for session-owned consumers.
#[derive(Clone, Debug, PartialEq)]
pub struct NormalizedRuntimeDelta {
    /// Value to pass as `delta` to the existing persistent Luau worker.
    pub worker_delta: Value,
    pub property_changes: Vec<Value>,
    pub pings: Vec<Value>,
    pub tags: Vec<i32>,
    pub regions: Vec<Value>,
    pub metadata: Vec<Value>,
    /// Create IDs whose heavy sections were omitted because the matching final
    /// immutable view owns the exact, unmodified create record.
    pub compact_create_ids: Vec<String>,
}

/// The worker-owned portion of a normalized receipt. VM callers that already
/// retain the original `SessionRecord` can use this to avoid cloning its
/// session-owned side-channel vectors.
#[derive(Clone, Debug, PartialEq)]
pub struct NormalizedWorkerDelta {
    /// Value to pass as `delta` to the existing persistent Luau worker.
    pub worker_delta: Value,
    /// Create IDs whose heavy sections were omitted because the matching final
    /// immutable view owns the exact, unmodified create record.
    pub compact_create_ids: Vec<String>,
}

/// Reusable schema-derived type index for an incremental session. Construct
/// once after the schema arrives, then normalize every accepted packet without
/// rescanning classes or rebuilding property maps.
#[derive(Clone, Debug)]
pub struct RuntimeDeltaNormalizer {
    class_property_types: Arc<ClassPropertyTypeIndex>,
    service_roots: HashMap<String, (String, String)>,
    datamodel_root: String,
}

impl RuntimeDeltaNormalizer {
    pub fn new(schema: &Schema) -> Result<Self, String> {
        validate_schema(schema)?;
        let mut class_property_types = HashMap::with_capacity(schema.classes.len());
        for class in &schema.classes {
            let mut types = HashMap::with_capacity(class.properties.len());
            for property_id in &class.properties {
                let property = &schema.properties[*property_id];
                types.insert(property.name.clone(), property.type_id);
            }
            class_property_types.insert(class.name.clone(), types);
        }
        Ok(Self {
            class_property_types: Arc::new(class_property_types),
            service_roots: HashMap::new(),
            datamodel_root: "__world_snapshot_datamodel__".into(),
        })
    }

    pub fn projection_parts(
        &self,
    ) -> (
        Arc<ClassPropertyTypeIndex>,
        HashMap<String, (String, String)>,
    ) {
        (
            self.class_property_types.clone(),
            self.service_roots.clone(),
        )
    }

    /// Retain the small, explicit service map from the initial VM snapshot.
    /// A wire top container can reference the server DataModel, which is not
    /// sent as an ordinary node. Repeated full records must keep the same
    /// local DataModel projection as initialization, regardless of wire ID.
    /// Ordinary unparented instances remain unparented in both stores.
    pub fn bind_snapshot_services(&mut self, snapshot: &Value) -> Result<(), String> {
        let nodes = snapshot
            .get("nodes")
            .and_then(Value::as_array)
            .ok_or("snapshot nodes missing")?;
        let services = snapshot
            .get("services")
            .and_then(Value::as_object)
            .ok_or("snapshot services missing")?;
        if services.len() > 128 {
            return Err("too many snapshot services".into());
        }
        let games: Vec<_> = nodes
            .iter()
            .filter(|n| n["class_name"] == "DataModel")
            .collect();
        if games.len() > 1 {
            return Err("multiple DataModel roots".into());
        }
        let root = match games.first() {
            Some(node) => node["id"].as_str().ok_or("invalid DataModel id")?,
            None => "__world_snapshot_datamodel__",
        };
        let mut roots = HashMap::new();
        for (class, value) in services {
            let id = value.as_str().ok_or("invalid service id")?;
            let node = nodes
                .iter()
                .find(|n| n["id"] == id)
                .ok_or("unknown service id")?;
            if node["class_name"] != *class || id == root || roots.contains_key(id) {
                return Err("ambiguous service root".into());
            }
            // Preserve an explicitly supplied parent. The absent wire parent
            // is the only case where the local DataModel relationship applies.
            let parent = node.get("parent").and_then(Value::as_str).unwrap_or(root);
            roots.insert(id.to_owned(), (class.clone(), parent.to_owned()));
        }
        self.datamodel_root = root.to_owned();
        self.service_roots = roots;
        Ok(())
    }

    pub fn normalize(
        &mut self,
        pre_receipt_view: &WorldView,
        record: &SessionRecord,
        initial_replication_complete: bool,
    ) -> Result<NormalizedRuntimeDelta, String> {
        self.normalize_record(pre_receipt_view, None, record, initial_replication_complete)
    }

    /// Normalize a receipt for a VM caller that consumes side channels from
    /// the original `SessionRecord` separately. This returns only the worker
    /// update and compact-create bindings, avoiding clones of pings, regions,
    /// metadata, tags, and property changes.
    pub fn normalize_worker(
        &mut self,
        pre_receipt_view: &WorldView,
        record: &SessionRecord,
        initial_replication_complete: bool,
    ) -> Result<NormalizedWorkerDelta, String> {
        self.normalize_worker_record(pre_receipt_view, None, record, initial_replication_complete)
    }

    /// Normalize against the matching post-receipt view. Unambiguous create
    /// records can then emit compact headers plus IDs that the caller must
    /// verify and bind to that same view.
    pub fn normalize_for_shared_view(
        &mut self,
        pre_receipt_view: &WorldView,
        post_receipt_view: &WorldView,
        record: &SessionRecord,
        initial_replication_complete: bool,
    ) -> Result<NormalizedRuntimeDelta, String> {
        self.normalize_record(
            pre_receipt_view,
            Some(post_receipt_view),
            record,
            initial_replication_complete,
        )
    }

    /// Shared-view variant of [`Self::normalize_worker`]. Eligible creates
    /// can emit compact headers while this result identifies the nodes that
    /// must be verified and bound to the supplied post-receipt view.
    pub fn normalize_for_shared_view_worker(
        &mut self,
        pre_receipt_view: &WorldView,
        post_receipt_view: &WorldView,
        record: &SessionRecord,
        initial_replication_complete: bool,
    ) -> Result<NormalizedWorkerDelta, String> {
        self.normalize_worker_record(
            pre_receipt_view,
            Some(post_receipt_view),
            record,
            initial_replication_complete,
        )
    }

    fn normalize_record(
        &mut self,
        pre_receipt_view: &WorldView,
        post_receipt_view: Option<&WorldView>,
        record: &SessionRecord,
        initial_replication_complete: bool,
    ) -> Result<NormalizedRuntimeDelta, String> {
        let worker = self.normalize_worker_record(
            pre_receipt_view,
            post_receipt_view,
            record,
            initial_replication_complete,
        )?;
        Ok(NormalizedRuntimeDelta {
            worker_delta: worker.worker_delta,
            property_changes: record.property_changes.clone(),
            pings: record.pings.clone(),
            tags: record.tags.clone(),
            regions: record.regions.clone(),
            metadata: record.metadata.clone(),
            compact_create_ids: worker.compact_create_ids,
        })
    }

    fn normalize_worker_record(
        &mut self,
        pre_receipt_view: &WorldView,
        post_receipt_view: Option<&WorldView>,
        record: &SessionRecord,
        initial_replication_complete: bool,
    ) -> Result<NormalizedWorkerDelta, String> {
        let envelope = record
            .world_delta
            .compact()
            .as_object()
            .ok_or("session world delta must be an object")?;
        if envelope.get("version").and_then(Value::as_u64) != Some(1)
            || envelope.get("delta").and_then(Value::as_bool) != Some(true)
        {
            return Err("session record has an invalid world delta envelope".into());
        }
        let target_sequence = envelope
            .get("sequence")
            .and_then(Value::as_u64)
            .ok_or("session world delta sequence must be unsigned")?;
        let base_sequence = pre_receipt_view.sequence();
        if target_sequence < base_sequence {
            return Err("session world delta predates the supplied pre-receipt view".into());
        }
        if target_sequence != record.world_sequence {
            return Err("session record and world delta sequence disagree".into());
        }
        if post_receipt_view.is_some_and(|view| view.sequence() != target_sequence) {
            return Err("post-receipt view does not match the session record".into());
        }
        let actions = envelope
            .get("actions")
            .and_then(Value::as_array)
            .ok_or("session world delta actions must be an array")?;
        if actions
            .first()
            .and_then(|action| action.get("sequence"))
            .and_then(Value::as_u64)
            .is_some_and(|sequence| sequence <= base_sequence)
        {
            return Err("session world delta needs a view captured before its first action".into());
        }
        validate_event_records(actions, &record.events)?;

        let compact_candidates = post_receipt_view
            .map(|view| eligible_compact_creates(actions, view))
            .unwrap_or_default();
        let mut compact_create_ids = Vec::with_capacity(compact_candidates.len());

        let mut normalized_actions = Vec::with_capacity(actions.len());
        let mut created_classes = HashMap::<String, String>::new();
        let mut removed = std::collections::HashSet::<String>::new();
        let mut next_service_roots = self.service_roots.clone();
        let mut service_updates = Map::new();
        let mut last_sequence = None;
        for (action_index, source) in actions.iter().enumerate() {
            let object = source
                .as_object()
                .ok_or("session world action must be an object")?;
            let sequence = object
                .get("sequence")
                .and_then(Value::as_u64)
                .ok_or("session world action sequence must be unsigned")?;
            if sequence > target_sequence || last_sequence.is_some_and(|last| sequence <= last) {
                return Err("session world action ordering is invalid".into());
            }
            last_sequence = Some(sequence);

            let kind = object
                .get("kind")
                .and_then(Value::as_str)
                .ok_or("session world action kind must be a string")?;
            let id = object.get("id").and_then(Value::as_str).unwrap_or("");
            let compacted_create_id = if kind == "create" {
                source
                    .get("node")
                    .and_then(|node| node.get("id"))
                    .and_then(Value::as_str)
                    .filter(|id| compact_candidates.contains(*id))
                    .map(str::to_owned)
            } else {
                None
            };
            let service = if kind == "create" {
                top_container_service(source, &self.datamodel_root)?
            } else {
                None
            };
            let mut action = if let Some(compacted_id) = compacted_create_id.as_deref() {
                // Name participates in the compact header itself. Other shared
                // properties are read from the exact post-receipt Rust record.
                if record.world_delta.has_shared_field(action_index, "Name") {
                    let expanded = record.world_delta.action_owned(action_index)?;
                    compact_create_action(&expanded, sequence, next_service_roots.get(compacted_id))?
                } else {
                    compact_create_action(source, sequence, next_service_roots.get(compacted_id))?
                }
            } else {
                record.world_delta.action_owned(action_index)?
            };
            match kind {
                "create" => {
                    let node = action
                        .get_mut("node")
                        .ok_or("create action node is missing")?;
                    let class_name = node
                        .get("class_name")
                        .and_then(Value::as_str)
                        .filter(|value| !value.is_empty())
                        .ok_or("create node class_name must be a non-empty string")?
                        .to_owned();
                    let node_id = node
                        .get("id")
                        .and_then(Value::as_str)
                        .filter(|value| !value.is_empty())
                        .ok_or("create node id must be a non-empty string")?
                        .to_owned();
                    if !id.is_empty() && id != node_id {
                        return Err("create action id differs from node id".into());
                    }
                    if compacted_create_id.is_some() {
                        compact_create_ids.push(node_id.clone());
                    } else {
                        let types = self.class_property_types.get(&class_name);
                        normalize_create_node(node, types, sequence)?;
                        if let Some((service_class, parent)) = next_service_roots.get(&node_id) {
                            if service_class != &class_name {
                                return Err("received service changed class".into());
                            }
                            if node.get("destroyed").and_then(Value::as_bool) != Some(true) {
                                node["parent"] = json!(parent);
                            }
                        }
                    }
                    if let Some((service_class, parent)) = service {
                        if service_class != class_name {
                            return Err("top container class differs from its service name".into());
                        }
                        if next_service_roots.iter().any(|(existing_id, (class, _))| {
                            existing_id != &node_id && class == &service_class
                        }) {
                            return Err(
                                "multiple top containers have the same service class".into()
                            );
                        }
                        if node.get("destroyed").and_then(Value::as_bool) == Some(true) {
                            next_service_roots.remove(&node_id);
                            service_updates.insert(service_class, Value::Null);
                        } else {
                            node["parent"] = json!(parent);
                            next_service_roots
                                .insert(node_id.clone(), (service_class.clone(), parent));
                            service_updates.insert(service_class, json!(node_id));
                        }
                    }
                    created_classes.insert(node_id.clone(), class_name);
                    removed.remove(&node_id);
                    if id.is_empty() {
                        action["id"] = json!(node_id);
                    }
                }
                "set" => {
                    if id.is_empty() {
                        return Err("set action id must be a non-empty string".into());
                    }
                    let class_name = created_classes.get(id).map(String::as_str).or_else(|| {
                        (!removed.contains(id))
                            .then(|| pre_receipt_view.class_name(id))
                            .flatten()
                    });
                    if let Some(class_name) = class_name {
                        normalize_set_fields(
                            &mut action,
                            self.class_property_types.get(class_name),
                        )?;
                    }
                    if let Some((_, parent)) = next_service_roots.get(id) {
                        if action.get("fields").and_then(|f| f.get("Parent")).is_some() {
                            action["fields"]["Parent"] = json!({"$ref":parent});
                        }
                    }
                }
                "remove" => {
                    if !id.is_empty() {
                        removed.insert(id.to_owned());
                        created_classes.remove(id);
                        if let Some((class, _)) = next_service_roots.remove(id) {
                            service_updates.insert(class, Value::Null);
                        }
                    }
                }
                "event" => {
                    if id.is_empty() {
                        return Err("event action id must be a non-empty string".into());
                    }
                    let name = action.get("name").and_then(Value::as_str);
                    if name.is_none() || !action.get("arguments").is_some_and(Value::is_array) {
                        return Err("event action needs a name and argument array".into());
                    }
                    let remote_invoke = name == Some("RemoteOnInvokeClient");
                    let client_event = name == Some("OnClientEvent");
                    let arguments = action["arguments"].as_array_mut().unwrap();
                    if remote_invoke {
                        if arguments.len() == 2 {
                            if let Some(tuple) = arguments[1].as_array_mut() {
                                for argument in tuple {
                                    *argument = normalize_remote_variant(argument, 0)?;
                                }
                            }
                        }
                    } else if client_event {
                        for argument in arguments {
                            *argument = normalize_remote_variant(argument, 0)?;
                        }
                    }
                }
                "attribute" => {
                    if id.is_empty()
                        || !action.get("attribute_update").is_some_and(Value::is_object)
                    {
                        return Err("attribute action needs an id and update object".into());
                    }
                }
                other => return Err(format!("unsupported session world action {other:?}")),
            }
            normalized_actions.push(action);
        }

        self.service_roots = next_service_roots;
        let mut worker_delta = json!({
            "version": 1,
            "delta": true,
            "sequence": target_sequence,
            "actions": normalized_actions
        });
        if !service_updates.is_empty() {
            worker_delta["services"] = Value::Object(service_updates);
        }
        if initial_replication_complete || record.tags.contains(&13) {
            worker_delta["initial_replication_complete"] = json!(true);
        }
        Ok(NormalizedWorkerDelta {
            worker_delta,
            compact_create_ids,
        })
    }
}

fn top_container_service(
    action: &Value,
    default_parent: &str,
) -> Result<Option<(String, String)>, String> {
    let Some(node) = action.get("node") else {
        return Ok(None);
    };
    let Some(flags) = node.get("top_container_flags") else {
        return Ok(None);
    };
    let flags = flags
        .as_array()
        .filter(|values| values.len() == 2 && values.iter().all(Value::is_boolean))
        .ok_or("top-container flags must be a pair of booleans")?;
    let _ = flags;
    let class_name = node
        .get("class_name")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or("top container class_name must be a non-empty string")?
        .to_owned();
    let parent = match node.get("parent") {
        Some(Value::String(parent)) => parent.clone(),
        Some(Value::Object(reference))
            if reference.len() == 1 && reference.get("$ref").and_then(Value::as_str).is_some() =>
        {
            reference["$ref"].as_str().unwrap().to_owned()
        }
        None | Some(Value::Null) => default_parent.to_owned(),
        _ => return Err("top container parent must be null or an instance reference".into()),
    };
    Ok(Some((class_name, parent)))
}

/// Convert a single committed record, using a WorldView captured before the
/// record was ingested. Session loops should retain a
/// [`RuntimeDeltaNormalizer`] and call `.normalize()`.
pub fn normalize_session_record(
    schema: &Schema,
    pre_receipt_view: &WorldView,
    record: &SessionRecord,
    initial_replication_complete: bool,
) -> Result<NormalizedRuntimeDelta, String> {
    RuntimeDeltaNormalizer::new(schema)?.normalize(
        pre_receipt_view,
        record,
        initial_replication_complete,
    )
}

fn eligible_compact_creates(actions: &[Value], post_view: &WorldView) -> HashSet<String> {
    let mut mutation_counts = HashMap::<String, usize>::new();
    for action in actions {
        let Some(kind) = action.get("kind").and_then(Value::as_str) else {
            continue;
        };
        if !matches!(kind, "create" | "set" | "remove") {
            continue;
        }
        let id = action.get("id").and_then(Value::as_str).or_else(|| {
            (kind == "create")
                .then(|| action.get("node")?.get("id")?.as_str())
                .flatten()
        });
        if let Some(id) = id {
            *mutation_counts.entry(id.to_owned()).or_default() += 1;
        }
    }
    let mut eligible = HashSet::new();
    for action in actions {
        if action.get("kind").and_then(Value::as_str) != Some("create") {
            continue;
        }
        let Some(node) = action.get("node") else {
            continue;
        };
        let (Some(id), Some(class_name), Some(sequence)) = (
            node.get("id").and_then(Value::as_str),
            node.get("class_name").and_then(Value::as_str),
            action.get("sequence").and_then(Value::as_u64),
        ) else {
            continue;
        };
        if mutation_counts.get(id) != Some(&1) {
            continue;
        }
        if post_view.read_only_node(id).is_some_and(|record| {
            record.class_name() == class_name && record.created_sequence() == Some(sequence)
        }) {
            eligible.insert(id.to_owned());
        }
    }
    eligible
}

/// Build a small create action without cloning the property, origin, or
/// revision trees. The caller only uses this after final-view eligibility has
/// been proven and receives the ID separately for mandatory native binding.
fn compact_create_action(
    action: &Value,
    sequence: u64,
    service_root: Option<&(String, String)>,
) -> Result<Value, String> {
    let action_object = action
        .as_object()
        .ok_or("session world action must be an object")?;
    let node = action_object
        .get("node")
        .and_then(Value::as_object)
        .ok_or("create node must be an object")?;
    let class_name = node
        .get("class_name")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or("create node class_name must be a non-empty string")?;
    let node_id = node
        .get("id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or("create node id must be a non-empty string")?;
    if action_object
        .get("id")
        .and_then(Value::as_str)
        .is_some_and(|id| id != node_id)
    {
        return Err("create action id differs from node id".into());
    }

    let name = match node.get("name") {
        Some(Value::String(name)) => name.to_owned(),
        Some(_) => return Err("create node name must be text when present".into()),
        None => match node
            .get("properties")
            .and_then(|properties| properties.get("Name"))
        {
            Some(Value::String(name)) => name.to_owned(),
            Some(Value::Object(value)) if value.len() == 1 && value.contains_key("$bytes_hex") => {
                decode_name_hex(
                    value["$bytes_hex"]
                        .as_str()
                        .ok_or("encoded Name bytes must be hexadecimal text")?,
                )?
            }
            Some(_) => return Err("create node Name property must be text when present".into()),
            None => class_name.to_owned(),
        },
    };
    let mut parent = match node.get("parent") {
        None => None,
        Some(Value::Null) => Some(Value::Null),
        Some(Value::String(parent)) => Some(Value::String(parent.clone())),
        Some(Value::Object(reference))
            if reference.len() == 1 && reference.get("$ref").and_then(Value::as_str).is_some() =>
        {
            Some(json!(reference["$ref"].as_str().unwrap()))
        }
        Some(_) => return Err("create node parent must be null or an instance reference".into()),
    };
    let properties = match node.get("properties") {
        Some(Value::Object(properties)) => properties,
        _ => return Err("create node properties must be an object".into()),
    };
    match node.get("origins") {
        None | Some(Value::Object(_)) => {}
        Some(_) => return Err("create node origins must be an object".into()),
    }
    let revisions = match node.get("property_revisions") {
        None => None,
        Some(Value::Object(values)) => Some(values),
        Some(_) => return Err("create node property_revisions must be an object".into()),
    };
    validate_normalized_node_values(properties, revisions, Some(sequence))?;
    if let Some((service_class, service_parent)) = service_root {
        if service_class != class_name {
            return Err("received service changed class".into());
        }
        if node.get("destroyed").and_then(Value::as_bool) != Some(true) {
            parent = Some(json!(service_parent));
        }
    }

    let mut compact_node = Map::with_capacity(node.len().saturating_sub(3) + 2);
    for (key, value) in node {
        if matches!(
            key.as_str(),
            "properties" | "origins" | "property_revisions" | "top_container_flags"
        ) {
            continue;
        }
        if key != "name" && key != "parent" {
            compact_node.insert(key.clone(), value.clone());
        }
    }
    compact_node.insert("name".into(), json!(name));
    if let Some(parent) = parent {
        compact_node.insert("parent".into(), parent);
    }
    let mut compact_action = Map::with_capacity(action_object.len());
    for (key, value) in action_object {
        if key != "node" {
            compact_action.insert(key.clone(), value.clone());
        }
    }
    if !action_object
        .get("id")
        .and_then(Value::as_str)
        .is_some_and(|id| !id.is_empty())
    {
        compact_action.insert("id".into(), json!(node_id));
    }
    compact_action.insert("node".into(), Value::Object(compact_node));
    Ok(Value::Object(compact_action))
}

pub(crate) fn normalize_remote_variant(value: &Value, depth: usize) -> Result<Value, String> {
    if depth > 16 {
        return Err("remote value nesting exceeds limit".into());
    }
    let Some(typed) = value.get("$remote_variant") else {
        return Ok(value.clone());
    };
    let kind = typed
        .get("type")
        .and_then(Value::as_u64)
        .and_then(|kind| u8::try_from(kind).ok())
        .ok_or("remote variant type is invalid")?;
    let raw = typed
        .get("value")
        .ok_or("remote variant value is missing")?;
    match kind {
        30 => {
            let values = raw
                .as_array()
                .ok_or("remote array value is invalid")?
                .iter()
                .map(|item| normalize_remote_variant(item, depth + 1))
                .collect::<Result<Vec<_>, _>>()?;
            Ok(json!({"$remote_array":values}))
        }
        31 | 32 => {
            let entries = raw
                .as_array()
                .ok_or("remote dictionary value is invalid")?
                .iter()
                .map(|entry| {
                    let pair = entry
                        .as_array()
                        .filter(|pair| pair.len() == 2)
                        .ok_or("remote dictionary entry is invalid")?;
                    let key = pair[0].clone();
                    if !key.is_string() && key.get("$bytes_hex").and_then(Value::as_str).is_none() {
                        return Err("remote dictionary key is invalid".into());
                    }
                    Ok(json!([key, normalize_remote_variant(&pair[1], depth + 1)?]))
                })
                .collect::<Result<Vec<_>, String>>()?;
            Ok(json!({"$remote_map":entries}))
        }
        27 => crate::rpc::normalize_received_cframe(raw).map_err(|error| error.to_string()),
        40 => {
            let parts = raw
                .as_array()
                .filter(|parts| parts.len() == 2)
                .ok_or("remote Rect value is invalid")?;
            Ok(json!({"$rect":[{"$vector2":parts[0]},{"$vector2":parts[1]}]}))
        }
        0 | 2 | 3 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 18 | 19 | 20 | 21 | 22 | 28 | 37 | 48 | 56
        | 69 => Ok(raw.clone()),
        _ => Ok(json!({"$unsupported_remote_type":kind})),
    }
}

fn validate_event_records(actions: &[Value], events: &[Value]) -> Result<(), String> {
    let mut action_events = actions
        .iter()
        .filter(|action| action.get("kind").and_then(Value::as_str) == Some("event"));
    for event in events {
        let action = action_events
            .next()
            .ok_or("session event metadata has no ordered event action")?;
        if event.get("key").and_then(Value::as_str) != action.get("id").and_then(Value::as_str)
            || event.get("name") != action.get("name")
            || event.get("arguments") != action.get("arguments")
        {
            return Err("session event metadata differs from the ordered world action".into());
        }
    }
    if action_events.next().is_some() {
        return Err("world delta has an event without session event metadata".into());
    }
    Ok(())
}

fn validate_schema(schema: &Schema) -> Result<(), String> {
    for (index, property) in schema.properties.iter().enumerate() {
        if property.id != index || property.class >= schema.classes.len() {
            return Err("schema property identifiers are invalid".into());
        }
    }
    for (index, class) in schema.classes.iter().enumerate() {
        if class.id != index {
            return Err("schema class identifiers are invalid".into());
        }
        for property_id in &class.properties {
            let property = schema
                .properties
                .get(*property_id)
                .ok_or("schema class references a missing property")?;
            if property.class != class.id {
                return Err("schema class property belongs to another class".into());
            }
        }
    }
    Ok(())
}

fn normalize_create_node(
    node: &mut Value,
    types: Option<&HashMap<String, u8>>,
    sequence: u64,
) -> Result<(), String> {
    let object = node
        .as_object_mut()
        .ok_or("create node must be an object")?;
    object.remove("top_container_flags");
    let class_name = object
        .get("class_name")
        .and_then(Value::as_str)
        .ok_or("create node class_name must be text")?
        .to_owned();
    let id = object
        .get("id")
        .and_then(Value::as_str)
        .ok_or("create node id must be text")?
        .to_owned();
    let name = match object.get("name") {
        Some(Value::String(name)) => name.clone(),
        Some(_) => return Err("create node name must be text when present".into()),
        None => match object
            .get("properties")
            .and_then(|properties| properties.get("Name"))
        {
            Some(Value::String(name)) => name.clone(),
            Some(Value::Object(value)) if value.len() == 1 && value.contains_key("$bytes_hex") => {
                let hex = value["$bytes_hex"]
                    .as_str()
                    .ok_or("encoded Name bytes must be hexadecimal text")?;
                decode_name_hex(hex)?
            }
            Some(_) => return Err("create node Name property must be text".into()),
            None => class_name,
        },
    };
    object.insert("name".into(), Value::String(name));
    match object.get("parent") {
        None | Some(Value::Null | Value::String(_)) => {}
        Some(Value::Object(reference))
            if reference.len() == 1 && reference.get("$ref").and_then(Value::as_str).is_some() =>
        {
            let parent = reference["$ref"].as_str().unwrap().to_owned();
            object.insert("parent".into(), Value::String(parent));
        }
        Some(_) => return Err("create node parent must be null or an instance reference".into()),
    }

    let properties = object
        .get("properties")
        .and_then(Value::as_object)
        .ok_or("create node properties must be an object")?;
    let mut origins = match object.get("origins") {
        None => Map::new(),
        Some(Value::Object(values)) => values.clone(),
        Some(_) => return Err("create node origins must be an object".into()),
    };
    let mut revisions = match object.get("property_revisions") {
        None => Map::new(),
        Some(Value::Object(values)) => values.clone(),
        Some(_) => return Err("create node property_revisions must be an object".into()),
    };
    for key in properties.keys() {
        origins
            .entry(key.clone())
            .or_insert_with(|| json!("received"));
        revisions
            .entry(key.clone())
            .or_insert_with(|| json!(sequence));
    }
    revisions.entry("Parent").or_insert_with(|| json!(sequence));
    object.insert("origins".into(), Value::Object(origins));
    object.insert("property_revisions".into(), Value::Object(revisions));
    normalize_node_values(node, types)?;
    // `Name` can be encoded in the received property set. The normalized
    // engine record needs both the instance name and its received property.
    if !id.is_empty() {
        Ok(())
    } else {
        Err("create node id must be non-empty".into())
    }
}

fn normalize_set_fields(
    action: &mut Value,
    types: Option<&HashMap<String, u8>>,
) -> Result<(), String> {
    let fields = action
        .get_mut("fields")
        .and_then(Value::as_object_mut)
        .ok_or("set action fields must be an object")?;
    for (name, value) in fields.iter_mut() {
        if let Some(type_id @ (27 | 51 | 57)) = types.and_then(|types| types.get(name)).copied() {
            *value = normalize_cframe(value, type_id);
        }
    }
    if !fields.contains_key("Size") && types.and_then(|types| types.get("size")) == Some(&22) {
        if let Some(size) = fields.get("size").cloned() {
            fields.insert("Size".into(), size);
        }
    }
    Ok(())
}

fn normalize_cframe(value: &Value, type_id: u8) -> Value {
    if value
        .as_object()
        .is_some_and(|object| object.len() == 1 && object.contains_key("$cframe"))
    {
        return value.clone();
    }
    let Some(source) = value.as_object() else {
        return value.clone();
    };
    let mut frame = source.clone();
    let selector = match type_id {
        27 => frame.get("rotation_id").and_then(Value::as_u64),
        51 | 57 => frame.get("rotation").and_then(Value::as_u64),
        _ => None,
    };
    let identity_selector = match type_id {
        27 | 51 => Some(2),
        57 => Some(1),
        _ => None,
    };
    if selector == identity_selector {
        frame.insert("rotation_selector".into(), json!(selector));
        frame.insert(
            "rotation".into(),
            json!([1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]),
        );
        frame.insert("rotation_mapping".into(), json!("native_745_identity"));
    }
    json!({"$cframe":Value::Object(frame)})
}

fn decode_name_hex(hex: &str) -> Result<String, String> {
    if hex.len() % 2 != 0 || !hex.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err("encoded Name bytes are invalid hexadecimal text".into());
    }
    let mut bytes = Vec::with_capacity(hex.len() / 2);
    for pair in hex.as_bytes().chunks_exact(2) {
        let high = (pair[0] as char)
            .to_digit(16)
            .ok_or("encoded Name bytes are invalid hexadecimal text")? as u8;
        let low = (pair[1] as char)
            .to_digit(16)
            .ok_or("encoded Name bytes are invalid hexadecimal text")? as u8;
        bytes.push((high << 4) | low);
    }
    Ok(String::from_utf8_lossy(&bytes).into_owned())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::schema::{ClassDesc, EventArgument, EventDesc, PropertyDesc};
    use crate::world::WorldStore;

    fn schema() -> Schema {
        Schema {
            version: 3,
            flags: 0,
            enums: vec![],
            classes: vec![ClassDesc {
                id: 0,
                name: "Part".into(),
                properties: vec![0, 1],
                events: vec![0],
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
                    name: "size".into(),
                    type_id: 22,
                    enum_id: None,
                    lr_type: None,
                },
            ],
            events: vec![EventDesc {
                id: 0,
                class: 0,
                name: "Changed".into(),
                arguments: vec![EventArgument {
                    type_id: 10,
                    enum_id: u16::MAX,
                }],
            }],
            strings: vec![],
            names: vec![],
            lr_types: vec![],
            parent_property_id: 2,
            decoded_bytes: 0,
        }
    }

    fn record(world_delta: Value, world_sequence: u64) -> SessionRecord {
        SessionRecord {
            packet_kind: 131,
            world_sequence,
            world_delta: world_delta.into(),
            events: vec![],
            pings: vec![],
            tags: vec![],
            regions: vec![],
            metadata: vec![],
            property_changes: vec![],
        }
    }

    #[test]
    fn top_container_projection_is_stable_and_does_not_parent_ordinary_nodes() {
        let snapshot = json!({"version":1,"sequence":0,"services":{"Workspace":"ws"},"nodes":[
            {"id":"game","class_name":"DataModel","parent":null,"properties":{}},
            {"id":"ws","class_name":"Workspace","parent":null,"properties":{}}]});
        let world = WorldStore::from_snapshot(&snapshot).unwrap();
        let mut normalizer = RuntimeDeltaNormalizer::new(&schema()).unwrap();
        normalizer.bind_snapshot_services(&snapshot).unwrap();
        let input = json!({"version":1,"delta":true,"sequence":5,"actions":[
            {"sequence":1,"kind":"create","id":"ws","node":{"id":"ws","class_name":"Workspace","parent":null,"properties":{}}},
            {"sequence":2,"kind":"create","id":"ordinary","node":{"id":"ordinary","class_name":"Workspace","parent":null,"properties":{}}},
            {"sequence":3,"kind":"set","id":"ws","fields":{"Parent":null}},
            {"sequence":4,"kind":"create","id":"ws","node":{"id":"ws","class_name":"Workspace","parent":{"$ref":"server-datamodel"},"properties":{}}},
            {"sequence":5,"kind":"set","id":"ws","fields":{"Parent":{"$ref":"server-datamodel"}}}]});
        let receipt = record(input, 5);
        let output = normalizer
            .normalize(&world.view(), &receipt, false)
            .unwrap();
        assert_eq!(output.worker_delta["actions"][0]["node"]["parent"], "game");
        assert_eq!(
            output.worker_delta["actions"][1]["node"]["parent"],
            Value::Null
        );
        assert_eq!(
            output.worker_delta["actions"][2]["fields"]["Parent"],
            json!({"$ref":"game"})
        );
        assert_eq!(output.worker_delta["actions"][3]["node"]["parent"], "game");
        assert_eq!(
            output.worker_delta["actions"][4]["fields"]["Parent"],
            json!({"$ref":"game"})
        );
        assert_eq!(
            receipt.world_delta["actions"][0]["node"]["parent"],
            Value::Null
        );
        assert_eq!(world.view().parent("ws"), None);
        let mut bad = snapshot.clone();
        bad["services"]["Workspace"] = json!("unknown");
        assert!(normalizer.bind_snapshot_services(&bad).is_err());
        assert_eq!(
            normalizer
                .normalize(&world.view(), &receipt, false)
                .unwrap(),
            output
        );
    }

    #[test]
    fn later_globals_update_services_and_preserve_their_projection() {
        let snapshot = json!({"version":1,"sequence":0,"services":{"Workspace":"ws"},"nodes":[
            {"id":"game","class_name":"DataModel","parent":null,"properties":{}},
            {"id":"ws","class_name":"Workspace","parent":null,"properties":{}}]});
        let world = WorldStore::from_snapshot(&snapshot).unwrap();
        let mut normalizer = RuntimeDeltaNormalizer::new(&schema()).unwrap();
        normalizer.bind_snapshot_services(&snapshot).unwrap();
        let mut receipt = record(
            json!({"version":1,"delta":true,"sequence":2,"actions":[
                {"sequence":1,"kind":"create","id":"rs","node":{"id":"rs","class_name":"RunService","parent":null,"top_container_flags":[true,true],"properties":{}}},
                {"sequence":2,"kind":"set","id":"rs","fields":{"Parent":null}}
            ]}),
            2,
        );
        receipt.packet_kind = 129;
        let output = normalizer
            .normalize(&world.view(), &receipt, false)
            .unwrap();
        assert_eq!(output.worker_delta["services"]["RunService"], "rs");
        assert_eq!(output.worker_delta["actions"][0]["node"]["parent"], "game");
        assert!(output.worker_delta["actions"][0]["node"]
            .get("top_container_flags")
            .is_none());
        assert_eq!(
            output.worker_delta["actions"][1]["fields"]["Parent"],
            json!({"$ref":"game"})
        );

        let refresh = record(
            json!({"version":1,"delta":true,"sequence":3,"actions":[
                {"sequence":3,"kind":"create","id":"rs","node":{"id":"rs","class_name":"RunService","parent":null,"properties":{}}}
            ]}),
            3,
        );
        let output = normalizer
            .normalize(&world.view(), &refresh, false)
            .unwrap();
        assert_eq!(output.worker_delta["actions"][0]["node"]["parent"], "game");

        let mut duplicate = receipt.clone();
        duplicate.world_delta["actions"][0]["node"]["id"] = json!("other");
        duplicate.world_delta["actions"][0]["id"] = json!("other");
        duplicate.world_delta["actions"]
            .as_array_mut()
            .unwrap()
            .remove(1);
        assert!(normalizer
            .normalize(&world.view(), &duplicate, false)
            .is_err());

        let removed = record(
            json!({"version":1,"delta":true,"sequence":4,"actions":[
                {"sequence":4,"kind":"remove","id":"rs"}
            ]}),
            4,
        );
        let output = normalizer
            .normalize(&world.view(), &removed, false)
            .unwrap();
        assert_eq!(output.worker_delta["services"]["RunService"], Value::Null);
    }

    #[test]
    fn compact_service_create_matches_full_flag_removal_and_service_parenting() {
        let schema = schema();
        let pre_store =
            WorldStore::from_snapshot(&json!({"version":1,"sequence":0,"nodes":[]})).unwrap();
        let pre = pre_store.view();
        let receipt = record(
            json!({"version":1,"delta":true,"sequence":1,"actions":[{
                "sequence":1,"kind":"create","id":"rs","node":{
                    "id":"rs","class_name":"RunService","parent":null,
                    "top_container_flags":[true,true],"properties":{}
                }
            }]}),
            1,
        );
        let mut post_store = pre_store;
        post_store.apply_delta(&receipt.world_delta).unwrap();
        let post = post_store.view();

        let mut full_normalizer = RuntimeDeltaNormalizer::new(&schema).unwrap();
        let full = full_normalizer.normalize(&pre, &receipt, false).unwrap();
        let mut compact_normalizer = RuntimeDeltaNormalizer::new(&schema).unwrap();
        let compact = compact_normalizer
            .normalize_for_shared_view(&pre, &post, &receipt, false)
            .unwrap();

        assert_eq!(compact.compact_create_ids, ["rs"]);
        assert_eq!(compact.worker_delta["services"]["RunService"], "rs");
        assert_eq!(
            compact.worker_delta["actions"][0]["node"]["parent"],
            full.worker_delta["actions"][0]["node"]["parent"]
        );
        assert_eq!(compact.worker_delta["services"], full.worker_delta["services"]);
        assert!(full.worker_delta["actions"][0]["node"]
            .get("top_container_flags")
            .is_none());
        assert!(compact.worker_delta["actions"][0]["node"]
            .get("top_container_flags")
            .is_none());
    }

    #[test]
    fn create_projects_name_cframe_position_size_and_provenance() {
        let world =
            WorldStore::from_snapshot(&json!({"version":1,"sequence":0,"nodes":[]})).unwrap();
        let view = world.view();
        let input = json!({"version":1,"delta":true,"sequence":1,"actions":[{
            "sequence":1,"kind":"create","id":"1:7","node":{"id":"1:7","class_name":"Part","parent":{"$ref":"1:1"},
                "properties":{"CFrame":{"position":[1,2,3],"rotation_id":2},"size":{"$vector3":[4,5,6]}}}
        }]});
        let output = normalize_session_record(&schema(), &view, &record(input, 1), false).unwrap();
        let node = &output.worker_delta["actions"][0]["node"];
        assert_eq!(node["name"], "Part");
        assert_eq!(node["parent"], "1:1");
        assert_eq!(
            node["properties"]["CFrame"]["$cframe"]["rotation_mapping"],
            "native_745_identity"
        );
        assert_eq!(node["properties"]["Position"]["$vector3"], json!([1, 2, 3]));
        assert_eq!(node["properties"]["Size"], json!({"$vector3":[4,5,6]}));
        assert_eq!(node["origins"]["Position"], "derived_received");
        assert_eq!(node["property_revisions"]["CFrame"], 1);
    }

    #[test]
    fn shared_view_create_emits_header_and_preserves_projected_values_and_side_channels() {
        let schema = schema();
        let pre_store =
            WorldStore::from_snapshot(&json!({"version":1,"sequence":0,"nodes":[]})).unwrap();
        let pre = pre_store.view();
        let create = json!({"sequence":1,"kind":"create","id":"1:7","node":{
            "id":"1:7","class_name":"Part","parent":{"$ref":"1:1"},
            "properties":{"CFrame":{"position":[1,2,3],"rotation_id":2},"size":{"$vector3":[4,5,6]}}
        }});
        let event =
            json!({"sequence":2,"kind":"event","id":"1:7","name":"Changed","arguments":[1]});
        let receipt = SessionRecord {
            packet_kind: 131,
            world_sequence: 2,
            world_delta: json!({"version":1,"delta":true,"sequence":2,"actions":[create,event]}).into(),
            events: vec![json!({"key":"1:7","name":"Changed","arguments":[1]})],
            pings: vec![],
            tags: vec![],
            regions: vec![],
            metadata: vec![],
            property_changes: vec![json!({"id":"1:7","property_index":0,"reset_version":false})],
        };
        let mut post_store = pre_store.clone();
        post_store.apply_delta(&receipt.world_delta).unwrap();
        let post = post_store.view();
        let mut normalizer = RuntimeDeltaNormalizer::new(&schema).unwrap();
        let full = normalizer.normalize(&pre, &receipt, false).unwrap();
        let compact = normalizer
            .normalize_for_shared_view(&pre, &post, &receipt, false)
            .unwrap();
        assert_eq!(compact.compact_create_ids, ["1:7"]);
        assert_eq!(
            compact.worker_delta["actions"][0]["node"]["name"],
            full.worker_delta["actions"][0]["node"]["name"]
        );
        assert_eq!(
            compact.worker_delta["actions"][0]["node"]["parent"],
            full.worker_delta["actions"][0]["node"]["parent"]
        );
        assert!(compact.worker_delta["actions"][0]["node"]
            .get("properties")
            .is_none());
        assert!(compact.worker_delta["actions"][0]["node"]
            .get("origins")
            .is_none());
        assert!(compact.worker_delta["actions"][0]["node"]
            .get("property_revisions")
            .is_none());
        assert_eq!(
            compact.worker_delta["actions"][1],
            full.worker_delta["actions"][1]
        );
        assert_eq!(
            compact.worker_delta["actions"][1]["arguments"],
            receipt.events[0]["arguments"]
        );
        assert_eq!(compact.property_changes, receipt.property_changes);
        let handle = post.read_only_node("1:7").unwrap();
        let types = normalizer.class_property_types.get("Part");
        for key in ["CFrame", "size", "Size", "Position"] {
            assert_eq!(
                crate::runtime_snapshot::project_node_property(&handle, types, key).unwrap(),
                full.worker_delta["actions"][0]["node"]["properties"]
                    .get(key)
                    .cloned()
            );
            assert_eq!(
                crate::runtime_snapshot::project_node_origin(&handle, types, key).unwrap(),
                full.worker_delta["actions"][0]["node"]["origins"]
                    .get(key)
                    .cloned()
            );
            assert_eq!(
                crate::runtime_snapshot::project_node_revision(&handle, types, key).unwrap(),
                full.worker_delta["actions"][0]["node"]["property_revisions"]
                    .get(key)
                    .cloned()
            );
        }
    }

    #[test]
    fn shared_view_create_falls_back_for_create_set_and_create_remove() {
        let schema = schema();
        let mut normalizer = RuntimeDeltaNormalizer::new(&schema).unwrap();
        for second_action in [
            json!({"sequence":2,"kind":"set","id":"p","fields":{"size":{"$vector3":[2,2,2]}}}),
            json!({"sequence":2,"kind":"remove","id":"p"}),
        ] {
            let create = json!({"sequence":1,"kind":"create","id":"p","node":{"id":"p","class_name":"Part","properties":{"size":{"$vector3":[1,1,1]}}}});
            let delta =
                json!({"version":1,"delta":true,"sequence":2,"actions":[create,second_action]});
            let receipt = record(delta, 2);
            let pre_store =
                WorldStore::from_snapshot(&json!({"version":1,"sequence":0,"nodes":[]})).unwrap();
            let pre = pre_store.view();
            let mut post_store = pre_store;
            post_store.apply_delta(&receipt.world_delta).unwrap();
            let compact = normalizer
                .normalize_for_shared_view(&pre, &post_store.view(), &receipt, false)
                .unwrap();
            assert!(compact.compact_create_ids.is_empty());
            assert!(compact.worker_delta["actions"][0]["node"]
                .get("properties")
                .is_some());
            let worker = normalizer
                .normalize_for_shared_view_worker(&pre, &post_store.view(), &receipt, false)
                .unwrap();
            assert!(worker.compact_create_ids.is_empty());
            assert_eq!(worker.worker_delta, compact.worker_delta);
        }
    }

    #[test]
    fn vm_only_normalization_matches_worker_output_without_losing_full_sidechannels() {
        let schema = schema();
        let pre_store =
            WorldStore::from_snapshot(&json!({"version":1,"sequence":0,"nodes":[]})).unwrap();
        let pre = pre_store.view();
        let mut receipt = record(
            json!({"version":1,"delta":true,"sequence":1,"actions":[{
                "sequence":1,"kind":"create","id":"p","node":{
                    "id":"p","class_name":"Part","name":"Part",
                    "properties":{"size":{"$vector3":[1,2,3]}}
                }
            }]}),
            1,
        );
        receipt.tags = vec![13, 21];
        receipt.property_changes = vec![json!({"id":"p","property_index":2})];
        receipt.pings = vec![json!({"kind":1,"flags":2})];
        receipt.regions = vec![json!({"coordinate":[1,2,3],"radius":4})];
        receipt.metadata = vec![json!({"kind":"example","nested":[1,2]})];

        let mut post_store = pre_store;
        post_store.apply_delta(&receipt.world_delta).unwrap();
        let post = post_store.view();

        let mut full_normalizer = RuntimeDeltaNormalizer::new(&schema).unwrap();
        let full = full_normalizer
            .normalize_for_shared_view(&pre, &post, &receipt, false)
            .unwrap();
        assert_eq!(full.property_changes, receipt.property_changes);
        assert_eq!(full.pings, receipt.pings);
        assert_eq!(full.tags, receipt.tags);
        assert_eq!(full.regions, receipt.regions);
        assert_eq!(full.metadata, receipt.metadata);

        let mut worker_normalizer = RuntimeDeltaNormalizer::new(&schema).unwrap();
        let worker = worker_normalizer
            .normalize_for_shared_view_worker(&pre, &post, &receipt, false)
            .unwrap();
        assert_eq!(worker.worker_delta, full.worker_delta);
        assert_eq!(worker.compact_create_ids, full.compact_create_ids);
        assert_eq!(worker.compact_create_ids, ["p"]);
        assert_eq!(worker.worker_delta["initial_replication_complete"], true);
    }

    #[test]
    fn shared_view_create_runs_frame_validation_and_rejects_wrong_view() {
        let schema = schema();
        let mut normalizer = RuntimeDeltaNormalizer::new(&schema).unwrap();
        let pre_store =
            WorldStore::from_snapshot(&json!({"version":1,"sequence":0,"nodes":[]})).unwrap();
        let pre = pre_store.view();
        let receipt = record(
            json!({"version":1,"delta":true,"sequence":1,"actions":[{
                "sequence":1,"kind":"create","id":"p","node":{"id":"p","class_name":"Part","properties":{"CFrame":{"position":[1,2],"rotation_id":2}}}
            }]}),
            1,
        );
        let mut post_store = pre_store.clone();
        post_store.apply_delta(&receipt.world_delta).unwrap();
        assert!(normalizer.normalize(&pre, &receipt, false).is_err());
        assert!(normalizer
            .normalize_for_shared_view(&pre, &post_store.view(), &receipt, false)
            .is_err());
        assert!(normalizer
            .normalize_for_shared_view_worker(&pre, &post_store.view(), &receipt, false)
            .is_err());
        assert!(normalizer
            .normalize_for_shared_view(&pre, &pre, &receipt, false)
            .is_err());
    }

    #[test]
    fn shared_create_validates_winning_revision_even_without_position() {
        let schema = schema();
        let mut normalizer = RuntimeDeltaNormalizer::new(&schema).unwrap();
        let pre_store =
            WorldStore::from_snapshot(&json!({"version":1,"sequence":0,"nodes":[]})).unwrap();
        let pre = pre_store.view();
        let receipt = record(
            json!({"version":1,"delta":true,"sequence":1,"actions":[{
                "sequence":1,"kind":"create","id":"p","node":{"id":"p","class_name":"Part",
                    "properties":{"CFrame":{"position":[1,2,3],"rotation_id":2}},
                    "property_revisions":{"CFrame":"invalid"}}
            }]}),
            1,
        );
        let mut post_store = pre_store;
        post_store.apply_delta(&receipt.world_delta).unwrap();
        let post = post_store.view();
        assert!(normalizer.normalize(&pre, &receipt, false).is_err());
        assert!(normalizer
            .normalize_for_shared_view(&pre, &post, &receipt, false)
            .is_err());
    }

    #[test]
    fn ordered_actions_keep_event_and_set_receipt_order() {
        let world = WorldStore::from_snapshot(&json!({"version":1,"sequence":5,"nodes":[{
            "id":"1:7","class_name":"Part","name":"Part","parent":null,"properties":{}
        }]}))
        .unwrap();
        let input = json!({"version":1,"delta":true,"sequence":9,"actions":[
            {"sequence":6,"kind":"event","id":"1:7","name":"Changed","arguments":[1]},
            {"sequence":7,"kind":"set","id":"1:7","fields":{"CFrame":{"position":[4,5,6],"rotation_id":0},"size":{"$vector3":[1,2,3]}}},
            {"sequence":8,"kind":"event","id":"1:7","name":"Changed","arguments":[2]},
            {"sequence":9,"kind":"remove","id":"1:7"}
        ]});
        let mut receipt = record(input, 9);
        receipt.events = vec![
            json!({"key":"1:7","name":"Changed","arguments":[1]}),
            json!({"key":"1:7","name":"Changed","arguments":[2]}),
        ];
        receipt.property_changes =
            vec![json!({"id":"1:7","property_index":0,"reset_version":false})];
        let output = normalize_session_record(&schema(), &world.view(), &receipt, true).unwrap();
        let actions = output.worker_delta["actions"].as_array().unwrap();
        assert_eq!(
            actions
                .iter()
                .map(|a| a["kind"].as_str().unwrap())
                .collect::<Vec<_>>(),
            ["event", "set", "event", "remove"]
        );
        assert!(actions[1]["fields"]["CFrame"].get("$cframe").is_some());
        assert_eq!(actions[1]["fields"]["Size"], json!({"$vector3":[1,2,3]}));
        assert_eq!(output.worker_delta["initial_replication_complete"], true);
        assert_eq!(output.property_changes, receipt.property_changes);
    }

    #[test]
    fn pre_receipt_view_resolves_set_before_remove() {
        let schema = schema();
        let mut normalizer = RuntimeDeltaNormalizer::new(&schema).unwrap();
        let mut world = WorldStore::from_snapshot(&json!({"version":1,"sequence":5,"nodes":[{
            "id":"1:7","class_name":"Part","name":"Part","parent":null,"properties":{}
        }]}))
        .unwrap();
        let pre_receipt_view = world.view();
        let receipt = record(
            json!({"version":1,"delta":true,"sequence":7,"actions":[
                {"sequence":6,"kind":"set","id":"1:7","fields":{"CFrame":{"position":[1,2,3],"rotation_id":2}}},
                {"sequence":7,"kind":"remove","id":"1:7"}
            ]}),
            7,
        );
        world.apply_delta(&receipt.world_delta).unwrap();
        let post_receipt_view = world.view();
        assert_eq!(post_receipt_view.class_name("1:7"), None);

        let normalized = normalizer
            .normalize(&pre_receipt_view, &receipt, false)
            .unwrap();
        assert!(normalized.worker_delta["actions"][0]["fields"]["CFrame"]
            .get("$cframe")
            .is_some());
        assert!(normalizer
            .normalize(&post_receipt_view, &receipt, false)
            .unwrap_err()
            .contains("view captured before"));
    }

    #[test]
    fn reusable_normalizer_keeps_schema_types_across_packets() {
        let schema = schema();
        let mut normalizer = RuntimeDeltaNormalizer::new(&schema).unwrap();
        let world = WorldStore::from_snapshot(&json!({"version":1,"sequence":5,"nodes":[{
            "id":"1:7","class_name":"Part","name":"Part","parent":null,"properties":{}
        }]}))
        .unwrap();
        for (sequence, x) in [(6, 1), (7, 4)] {
            let receipt = record(
                json!({"version":1,"delta":true,"sequence":sequence,"actions":[
                    {"sequence":sequence,"kind":"set","id":"1:7","fields":{
                        "CFrame":{"position":[x,2,3],"rotation_id":2},"size":{"$vector3":[1,2,3]}
                    }}
                ]}),
                sequence,
            );
            let normalized = normalizer
                .normalize(&world.view(), &receipt, false)
                .unwrap();
            let fields = &normalized.worker_delta["actions"][0]["fields"];
            assert_eq!(
                fields["CFrame"]["$cframe"]["rotation_mapping"],
                "native_745_identity"
            );
            assert_eq!(fields["Size"], json!({"$vector3":[1,2,3]}));
        }
    }

    #[test]
    fn normalizer_rejects_invalid_schema_once_at_construction() {
        let mut bad_schema = schema();
        bad_schema.classes[0].id = 1;
        assert!(RuntimeDeltaNormalizer::new(&bad_schema)
            .unwrap_err()
            .contains("class identifiers"));
    }

    #[test]
    fn cframe_encodings_match_python_reference_vectors() {
        let vectors: Value = serde_json::from_str(include_str!(
            "../../tests/fixtures/runtime_snapshot_cframe_python_vectors.json"
        ))
        .unwrap();
        for vector in vectors.as_object().unwrap().values() {
            assert_eq!(
                normalize_cframe(&vector["input"], vector["type"].as_u64().unwrap() as u8),
                vector["expected"]
            );
        }
    }

    #[test]
    fn rejects_wrong_world_sequence_and_bad_order() {
        let world = WorldStore::from_snapshot(&json!({"version":1,"nodes":[]})).unwrap();
        let mismatch = record(
            json!({"version":1,"delta":true,"sequence":1,"actions":[]}),
            2,
        );
        assert!(normalize_session_record(&schema(), &world.view(), &mismatch, false).is_err());
        let unordered = record(
            json!({"version":1,"delta":true,"sequence":2,"actions":[
                {"sequence":2,"kind":"remove","id":"a"},{"sequence":1,"kind":"remove","id":"b"}
            ]}),
            2,
        );
        assert!(normalize_session_record(&schema(), &world.view(), &unordered, false).is_err());
    }

    #[test]
    fn remote_variants_preserve_nested_values_and_explicit_unsupported_types() {
        fn typed(kind: u8, value: impl Into<Value>) -> Value {
            json!({"$remote_variant":{"type":kind,"value":value.into()}})
        }
        assert_eq!(
            normalize_remote_variant(
                &typed(
                    30,
                    vec![
                        typed(12, json!(2.5)),
                        typed(30, vec![typed(2, json!("text"))]),
                        typed(31, vec![json!(["key", typed(9, json!(true))])]),
                    ],
                ),
                0,
            )
            .unwrap(),
            json!({"$remote_array":[2.5,{"$remote_array":["text"]},
                {"$remote_map":[["key",true]]}]})
        );
        assert_eq!(
            normalize_remote_variant(&typed(22, json!({"$vector3":[1,2,3]})), 0).unwrap(),
            json!({"$vector3":[1,2,3]})
        );
        assert_eq!(
            normalize_remote_variant(&typed(40, json!([[1, 2], [3, 4]])), 0).unwrap(),
            json!({"$rect":[{"$vector2":[1,2]},{"$vector2":[3,4]}]})
        );
        assert_eq!(
            normalize_remote_variant(&typed(7, json!(2)), 0).unwrap(),
            json!({"$unsupported_remote_type":7})
        );
        assert!(normalize_remote_variant(&typed(30, json!({})), 0).is_err());

        let world = WorldStore::from_snapshot(&json!({"version":1,"nodes":[]})).unwrap();
        let action = json!({"sequence":1,"kind":"event","id":"1:3",
            "name":"RemoteOnInvokeClient","arguments":[41,[typed(12,json!(2.0))]]});
        let mut receipt = record(
            json!({"version":1,"delta":true,"sequence":1,
            "actions":[action.clone()]}),
            1,
        );
        receipt.events = vec![json!({"key":"1:3","name":"RemoteOnInvokeClient",
            "arguments":action["arguments"].clone()})];
        let normalized =
            normalize_session_record(&schema(), &world.view(), &receipt, false).unwrap();
        assert_eq!(
            normalized.worker_delta["actions"][0]["arguments"],
            json!([41, [2.0]])
        );
    }
}
