//! make the small startup document the official luau vm needs.
//! copy the received world without guessing missing fields.
//! session metadata has to come from the owner, otherwise leave it out.
use crate::schema::Schema;
use crate::world::ReadOnlyNode;
use serde_json::{json, Map, Value};
use std::collections::{HashMap, HashSet};

const MAX_NODES: usize = 20_000;

/// Known session-owned facts that are not present in a network schema/world.
/// `None` means unknown and causes the field to be omitted from the VM input.
#[derive(Clone, Copy, Debug, Default)]
pub struct RuntimeSnapshotMetadata<'a> {
    pub job_id: Option<&'a str>,
    pub local_player: Option<&'a str>,
    pub initial_replication_complete: Option<bool>,
    pub class_parents: Option<&'a Value>,
    pub defaults: Option<&'a Value>,
    pub default_origins: Option<&'a Value>,
    pub script_sources: Option<&'a Value>,
    pub builtin_player_scripts: Option<&'a Value>,
    pub chat_enabled: Option<bool>,
    pub session_stats: Option<&'a Value>,
    pub input_capabilities: Option<&'a Value>,
    pub viewport: Option<&'a Value>,
    pub display: Option<&'a Value>,
    pub client_service_state: Option<&'a Value>,
}

/// Facts a network schema/world packet cannot establish on its own.
pub const UNSUPPORTED_DERIVATIONS: &[&str] = &[
    "class inheritance: the network schema has no superclass links",
    "class constructor defaults: they are not present in ordinary instance properties",
    "player identity: a replicated Player does not identify which account is local",
    "script source catalogs: source bytes are not part of the replication world snapshot",
    "script integrity/authentication values: this module does not generate them",
];

/// Assemble an engine-compatible snapshot from received data and known metadata.
///
/// The data snapshot must have a version-1 envelope and a node array. Nodes are
/// preserved byte-for-value at the JSON level, including omitted fields,
/// explicit nulls, origins, revisions, attributes, and tags. The two
/// transport-only `top_container_flags` are consumed to derive service IDs.
pub fn build_runtime_snapshot(
    schema: &Schema,
    data_snapshot: &Value,
    metadata: &RuntimeSnapshotMetadata<'_>,
) -> Result<Value, String> {
    let source = data_snapshot
        .as_object()
        .ok_or("world snapshot must be an object")?;
    if source.get("version").and_then(Value::as_u64) != Some(1) {
        return Err("unsupported or missing world snapshot version".into());
    }
    let source_nodes = source
        .get("nodes")
        .and_then(Value::as_array)
        .ok_or("world snapshot nodes must be an array")?;
    if source_nodes.len() > MAX_NODES {
        return Err("world snapshot exceeds 20000 nodes".into());
    }
    if let Some(sequence) = source.get("sequence") {
        if !sequence.is_u64() {
            return Err("world snapshot sequence must be an unsigned integer".into());
        }
    }

    let mut class_ids = HashMap::with_capacity(schema.classes.len());
    for (index, class) in schema.classes.iter().enumerate() {
        if class.id != index || class_ids.insert(class.name.as_str(), class.id).is_some() {
            return Err("schema class identifiers or names are ambiguous".into());
        }
    }
    validate_schema_tables(schema)?;

    let mut class_property_types: HashMap<String, HashMap<String, u8>> = HashMap::new();
    for class in &schema.classes {
        let fields = class_property_types.entry(class.name.clone()).or_default();
        for property_id in &class.properties {
            let property = &schema.properties[*property_id];
            fields.insert(property.name.clone(), property.type_id);
        }
    }

    let mut seen_nodes = HashSet::with_capacity(source_nodes.len());
    let mut node_classes = HashMap::with_capacity(source_nodes.len());
    let mut services = Map::new();
    let mut nodes = Vec::with_capacity(source_nodes.len());
    for source_node in source_nodes {
        let object = source_node
            .as_object()
            .ok_or("world node must be an object")?;
        let id = object
            .get("id")
            .and_then(Value::as_str)
            .filter(|id| !id.is_empty())
            .ok_or("world node id must be a non-empty string")?;
        let class_name = object
            .get("class_name")
            .and_then(Value::as_str)
            .filter(|name| !name.is_empty())
            .ok_or("world node class_name must be a non-empty string")?;
        if id.len() > 256 || class_name.len() > 256 {
            return Err("world node id or class_name exceeds 256 bytes".into());
        }
        if !seen_nodes.insert(id.to_owned()) {
            return Err("world snapshot contains duplicate node IDs".into());
        }

        let properties = match object.get("properties") {
            None => None,
            Some(Value::Object(properties)) => Some(properties),
            Some(_) => return Err("world node properties must be an object when present".into()),
        };
        if object
            .get("parent")
            .is_some_and(|parent| !parent.is_null() && !parent.is_string())
        {
            return Err("world node parent must be a string or null".into());
        }
        for field in ["origins", "attributes", "property_revisions"] {
            if object.get(field).is_some_and(|value| !value.is_object()) {
                return Err(format!("world node {field} must be an object when present"));
            }
        }
        let name = match object.get("name") {
            Some(Value::String(name)) => name.clone(),
            Some(_) => return Err("world node name must be a string when present".into()),
            None => match properties.and_then(|fields| fields.get("Name")) {
                None => class_name.to_owned(),
                Some(Value::String(name)) => name.clone(),
                Some(Value::Object(encoded))
                    if encoded.len() == 1 && encoded.contains_key("$bytes_hex") =>
                {
                    let hex = encoded["$bytes_hex"]
                        .as_str()
                        .ok_or("encoded Name bytes must be hexadecimal text")?;
                    decode_name_hex(hex)?
                }
                Some(_) => return Err("world Name property must be a string when present".into()),
            },
        };
        if name.len() > 256 {
            return Err("world node name exceeds 256 bytes".into());
        }
        node_classes.insert(id, class_name);

        if let Some(flags) = object.get("top_container_flags") {
            let pair = flags
                .as_array()
                .filter(|values| values.len() == 2)
                .ok_or("top-container flags must be a pair of booleans")?;
            if !pair.iter().all(Value::is_boolean) {
                return Err("top-container flags must be a pair of booleans".into());
            }
            if !class_ids.contains_key(class_name) {
                return Err("top container class is absent from the network schema".into());
            }
            if services
                .insert(class_name.to_owned(), Value::String(id.to_owned()))
                .is_some()
            {
                return Err("multiple top containers have the same service class".into());
            }
        }

        let mut node = source_node.clone();
        normalize_node_values(&mut node, class_property_types.get(class_name))?;
        // This is transport metadata used above, not an Instance field or
        // property. Keep the parsed WorldStore intact while producing the VM
        // representation expected by the existing native host.
        if let Some(node) = node.as_object_mut() {
            node.remove("top_container_flags");
            node.insert("name".into(), Value::String(name));
        }
        nodes.push(node);
    }

    if let Some(local_player) = metadata.local_player {
        if local_player.is_empty() {
            return Err("local_player must be a non-empty instance ID".into());
        }
        if node_classes
            .get(local_player)
            .is_some_and(|class_name| *class_name != "Player")
        {
            return Err("local_player references a non-Player instance".into());
        }
    }

    let mut output = Map::new();
    output.insert("version".into(), json!(1));
    if crate::lightweight::enabled() {
        output.insert("lightweight_assets".into(), json!(true));
    }
    if let Some(sequence) = source.get("sequence") {
        output.insert("sequence".into(), sequence.clone());
    }
    output.insert("nodes".into(), Value::Array(nodes));
    output.insert("services".into(), Value::Object(services));

    insert_optional_string(&mut output, "job_id", metadata.job_id)?;
    insert_optional_string(&mut output, "local_player", metadata.local_player)?;
    if let Some(value) = metadata.initial_replication_complete {
        output.insert("initial_replication_complete".into(), json!(value));
    }
    if let Some(value) = metadata.chat_enabled {
        output.insert("chat_enabled".into(), json!(value));
    }
    insert_optional_object(&mut output, "class_parents", metadata.class_parents)?;
    insert_optional_object(&mut output, "defaults", metadata.defaults)?;
    insert_optional_object(&mut output, "default_origins", metadata.default_origins)?;
    insert_optional_object(&mut output, "script_sources", metadata.script_sources)?;
    insert_optional_array(
        &mut output,
        "builtin_player_scripts",
        metadata.builtin_player_scripts,
    )?;
    insert_optional_object(&mut output, "session_stats", metadata.session_stats)?;
    insert_optional_object(
        &mut output,
        "input_capabilities",
        metadata.input_capabilities,
    )?;
    insert_optional_object(&mut output, "viewport", metadata.viewport)?;
    insert_optional_object(&mut output, "display", metadata.display)?;
    insert_optional_object(
        &mut output,
        "client_service_state",
        metadata.client_service_state,
    )?;

    let mut class_events = Map::new();
    let mut class_event_signatures = Map::new();
    let mut class_property_types = Map::new();
    for class in &schema.classes {
        let event_names = class
            .events
            .iter()
            .map(|event_id| Value::String(schema.events[*event_id].name.clone()))
            .collect::<Vec<_>>();
        let mut event_signatures = Map::new();
        for event_id in &class.events {
            let event = &schema.events[*event_id];
            event_signatures.insert(
                event.name.clone(),
                Value::Array(
                    event
                        .arguments
                        .iter()
                        .map(|argument| json!(argument.type_id))
                        .collect(),
                ),
            );
        }
        let mut property_types = Map::new();
        for property_id in &class.properties {
            let property = &schema.properties[*property_id];
            property_types.insert(property.name.clone(), json!(property.type_id));
        }
        class_events.insert(class.name.clone(), Value::Array(event_names));
        class_event_signatures.insert(class.name.clone(), Value::Object(event_signatures));
        class_property_types.insert(class.name.clone(), Value::Object(property_types));
    }
    output.insert("class_events".into(), Value::Object(class_events));
    output.insert(
        "class_event_signatures".into(),
        Value::Object(class_event_signatures),
    );
    output.insert(
        "class_property_types".into(),
        Value::Object(class_property_types),
    );
    Ok(Value::Object(output))
}

pub fn decode_name_hex(hex: &str) -> Result<String, String> {
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

pub fn normalize_node_values(
    node: &mut Value,
    property_types: Option<&HashMap<String, u8>>,
) -> Result<(), String> {
    if let Some(properties) = node.get("properties").and_then(Value::as_object) {
        let revisions = node.get("property_revisions").and_then(Value::as_object);
        validate_normalized_node_values(properties, revisions, None)?;
    }
    if let Some(properties) = node.get_mut("properties").and_then(Value::as_object_mut) {
        if let Some(property_types) = property_types {
            for (name, value) in properties.iter_mut() {
                if let Some(type_id @ (27 | 51 | 57)) = property_types.get(name).copied() {
                    *value = normalize_cframe(value, type_id);
                }
            }
        }
    } else {
        return Ok(());
    }

    let derived_size = {
        let properties = node.get("properties").and_then(Value::as_object).unwrap();
        if !properties.contains_key("Size")
            && property_types.and_then(|types| types.get("size")) == Some(&22)
        {
            properties.get("size").cloned()
        } else {
            None
        }
    };
    if let Some(size) = derived_size {
        node["properties"]["Size"] = size;
        set_node_metadata(node, "origins", "Size", json!("derived_received"))?;
        let revision = node
            .get("property_revisions")
            .and_then(|v| v.get("size"))
            .cloned()
            .unwrap_or_else(|| json!(0));
        set_node_metadata(node, "property_revisions", "Size", revision)?;
    }

    let frame_name = {
        let properties = node.get("properties").and_then(Value::as_object).unwrap();
        let cframe = properties
            .get("CFrame")
            .filter(|frame| frame_position(frame).is_some());
        let replication_pv = properties
            .get("ReplicationPV")
            .filter(|frame| frame_position(frame).is_some());
        match (cframe, replication_pv) {
            (Some(_), Some(_)) => {
                let cframe_revision = node_revision(node, "CFrame")?;
                let replication_revision = node_revision(node, "ReplicationPV")?;
                Some(if replication_revision > cframe_revision {
                    "ReplicationPV"
                } else {
                    "CFrame"
                })
            }
            (Some(_), None) => Some("CFrame"),
            (None, Some(_)) => Some("ReplicationPV"),
            (None, None) => None,
        }
    };
    if let Some(frame_name) = frame_name {
        let (frame, position, revision) = {
            let properties = node.get("properties").and_then(Value::as_object).unwrap();
            let frame = properties[frame_name].clone();
            let position = frame_position(&frame)
                .ok_or("received CFrame position is malformed")?
                .clone();
            validate_vector3(&position, "received CFrame position")?;
            (frame, position, node_revision(node, frame_name)?)
        };
        if frame_name == "ReplicationPV" {
            let type_id = property_types
                .and_then(|types| types.get(frame_name))
                .copied()
                .unwrap_or(57);
            node["properties"]["CFrame"] = normalize_cframe(&frame, type_id);
            set_node_metadata(node, "origins", "CFrame", json!("derived_received"))?;
            set_node_metadata(node, "property_revisions", "CFrame", json!(revision))?;
        }
        if !node["properties"]
            .as_object()
            .unwrap()
            .contains_key("Position")
            || node_revision(node, "Position")? <= revision
        {
            node["properties"]["Position"] = json!({"$vector3":position});
            set_node_metadata(node, "origins", "Position", json!("derived_received"))?;
            set_node_metadata(node, "property_revisions", "Position", json!(revision))?;
        }
    }
    Ok(())
}

/// Validate the value-dependent parts of runtime normalization directly from
/// borrowed property/metadata maps. This lets shared-create compaction validate
/// without cloning the received property tree.
pub fn validate_normalized_node_values(
    properties: &Map<String, Value>,
    revisions: Option<&Map<String, Value>>,
    default_revision: Option<u64>,
) -> Result<(), String> {
    let cframe = properties
        .get("CFrame")
        .filter(|v| frame_position(v).is_some());
    let replication = properties
        .get("ReplicationPV")
        .filter(|v| frame_position(v).is_some());
    let revision = |key: &str| -> Result<u64, String> {
        match revisions.and_then(|map| map.get(key)) {
            None => Ok(if properties.contains_key(key) {
                default_revision.unwrap_or(0)
            } else {
                0
            }),
            Some(value) => value
                .as_u64()
                .ok_or_else(|| format!("property revision for {key} must be unsigned")),
        }
    };
    let winner = match (cframe, replication) {
        (Some(_), Some(_)) => Some(if revision("ReplicationPV")? > revision("CFrame")? {
            "ReplicationPV"
        } else {
            "CFrame"
        }),
        (Some(_), None) => Some("CFrame"),
        (None, Some(_)) => Some("ReplicationPV"),
        (None, None) => None,
    };
    if let Some(winner) = winner {
        let winner_revision = revision(winner)?;
        let frame = if winner == "ReplicationPV" {
            replication.unwrap()
        } else {
            cframe.unwrap()
        };
        let position = frame_position(frame).ok_or("received CFrame position is malformed")?;
        validate_vector3(position, "received CFrame position")?;
        if !properties.contains_key("Position") || revision("Position")? <= winner_revision {
            // Position alias construction cannot fail after vector validation.
        }
    }
    Ok(())
}

/// Read one engine-projected property without unpacking or retaining the
/// node's complete property map. The full snapshot normalizer above remains
/// the parity reference for explicit export paths.
pub fn project_node_property(
    node: &ReadOnlyNode,
    property_types: Option<&HashMap<String, u8>>,
    key: &str,
) -> Result<Option<Value>, String> {
    let raw = |name: &str| node.property(name);
    if key == "Size"
        && raw("Size").is_none()
        && property_types.and_then(|types| types.get("size")) == Some(&22)
    {
        return Ok(raw("size"));
    }
    if key != "Position" && key != "CFrame" {
        return Ok(raw(key).map(|value| {
            if let Some(type_id @ (27 | 51 | 57)) =
                property_types.and_then(|types| types.get(key)).copied()
            {
                normalize_cframe(&value, type_id)
            } else {
                value
            }
        }));
    }
    let cframe = raw("CFrame");
    let replication = raw("ReplicationPV");
    let cf_valid = cframe.as_ref().is_some_and(|v| frame_position(v).is_some());
    let rp_valid = replication
        .as_ref()
        .is_some_and(|v| frame_position(v).is_some());
    let cf_rev = projected_revision(node, "CFrame")?;
    let rp_rev = projected_revision(node, "ReplicationPV")?;
    let winner = match (cf_valid, rp_valid) {
        (true, true) => Some(if rp_rev > cf_rev {
            "ReplicationPV"
        } else {
            "CFrame"
        }),
        (true, false) => Some("CFrame"),
        (false, true) => Some("ReplicationPV"),
        _ => None,
    };
    if key == "Position" {
        if let Some(winner_name) = winner {
            let revision = if winner_name == "ReplicationPV" {
                rp_rev
            } else {
                cf_rev
            };
            if raw("Position").is_none() || projected_revision(node, "Position")? <= revision {
                let value = if winner_name == "ReplicationPV" {
                    replication.as_ref().unwrap()
                } else {
                    cframe.as_ref().unwrap()
                };
                let position =
                    frame_position(value).ok_or("received CFrame position is malformed")?;
                validate_vector3(position, "received CFrame position")?;
                return Ok(Some(json!({"$vector3": position})));
            }
        }
    }
    if key == "CFrame" && winner == Some("ReplicationPV") {
        let frame = replication.as_ref().unwrap();
        let type_id = property_types
            .and_then(|types| types.get("ReplicationPV"))
            .copied()
            .unwrap_or(57);
        return Ok(Some(normalize_cframe(frame, type_id)));
    }
    Ok(raw(key).map(|value| {
        if let Some(type_id @ (27 | 51 | 57)) =
            property_types.and_then(|types| types.get(key)).copied()
        {
            normalize_cframe(&value, type_id)
        } else {
            value
        }
    }))
}

pub fn project_node_origin(
    node: &ReadOnlyNode,
    property_types: Option<&HashMap<String, u8>>,
    key: &str,
) -> Result<Option<Value>, String> {
    if key == "Size"
        && node.property("Size").is_none()
        && property_types.and_then(|types| types.get("size")) == Some(&22)
        && node.property("size").is_some()
    {
        return Ok(Some(json!("derived_received")));
    }
    if key == "CFrame" || key == "Position" {
        let winner = projected_frame_winner(node)?;
        if key == "CFrame" && winner == Some("ReplicationPV") {
            return Ok(Some(json!("derived_received")));
        }
        if key == "Position" && winner.is_some() {
            let revision = projected_revision(node, winner.unwrap())?;
            if node.property("Position").is_none()
                || projected_revision(node, "Position")? <= revision
            {
                return Ok(Some(json!("derived_received")));
            }
        }
    }
    if let Some(value) = node.metadata_field("origins", key) {
        return Ok(Some(value));
    }
    if node.created_sequence().is_some() && node.property(key).is_some() {
        return Ok(Some(json!("received")));
    }
    Ok(None)
}

pub fn project_node_revision(
    node: &ReadOnlyNode,
    property_types: Option<&HashMap<String, u8>>,
    key: &str,
) -> Result<Option<Value>, String> {
    if key == "Size"
        && node.property("Size").is_none()
        && property_types.and_then(|types| types.get("size")) == Some(&22)
        && node.property("size").is_some()
    {
        return Ok(Some(json!(projected_revision(node, "size")?)));
    }
    if key == "CFrame" || key == "Position" {
        let winner = projected_frame_winner(node)?;
        if (key == "CFrame" && winner == Some("ReplicationPV")) || key == "Position" {
            if let Some(winner) = winner {
                let revision = projected_revision(node, winner)?;
                if key == "CFrame"
                    || node.property("Position").is_none()
                    || projected_revision(node, "Position")? <= revision
                {
                    return Ok(Some(json!(revision)));
                }
            }
        }
    }
    if let Some(value) = node.metadata_field("property_revisions", key) {
        return Ok(Some(value));
    }
    if node.created_sequence().is_some() && node.property(key).is_some() {
        return Ok(Some(json!(node.created_sequence().unwrap())));
    }
    if key == "Parent" {
        return Ok(node.created_sequence().map(|seq| json!(seq)));
    }
    Ok(None)
}

pub fn projected_frame_winner(node: &ReadOnlyNode) -> Result<Option<&'static str>, String> {
    let cframe = node.property("CFrame");
    let replication = node.property("ReplicationPV");
    let cf_valid = cframe.as_ref().is_some_and(|v| frame_position(v).is_some());
    let rp_valid = replication
        .as_ref()
        .is_some_and(|v| frame_position(v).is_some());
    let cf_rev = projected_revision(node, "CFrame")?;
    let rp_rev = projected_revision(node, "ReplicationPV")?;
    Ok(match (cf_valid, rp_valid) {
        (true, true) => Some(if rp_rev > cf_rev {
            "ReplicationPV"
        } else {
            "CFrame"
        }),
        (true, false) => Some("CFrame"),
        (false, true) => Some("ReplicationPV"),
        _ => None,
    })
}

fn projected_revision(node: &ReadOnlyNode, key: &str) -> Result<u64, String> {
    match node.metadata_field("property_revisions", key) {
        None => Ok(if node.property(key).is_some() {
            node.created_sequence().unwrap_or(0)
        } else {
            0
        }),
        Some(value) => value
            .as_u64()
            .ok_or_else(|| format!("property revision for {key} must be unsigned")),
    }
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

fn frame_position(value: &Value) -> Option<&Value> {
    value
        .get("$cframe")
        .and_then(|frame| frame.get("position"))
        .or_else(|| value.get("position"))
}

fn validate_vector3(value: &Value, context: &str) -> Result<(), String> {
    let Some(items) = value.as_array().filter(|items| items.len() == 3) else {
        return Err(format!("{context} must have three components"));
    };
    if items
        .iter()
        .any(|item| item.as_f64().is_none_or(|component| !component.is_finite()))
    {
        return Err(format!("{context} components must be finite numbers"));
    }
    Ok(())
}

fn node_revision(node: &Value, property: &str) -> Result<u64, String> {
    match node
        .get("property_revisions")
        .and_then(Value::as_object)
        .and_then(|revisions| revisions.get(property))
    {
        None => Ok(0),
        Some(value) => value
            .as_u64()
            .ok_or_else(|| format!("property revision for {property} must be unsigned")),
    }
}

fn set_node_metadata(
    node: &mut Value,
    map_name: &str,
    key: &str,
    value: Value,
) -> Result<(), String> {
    let object = node.as_object_mut().ok_or("world node must be an object")?;
    let metadata = object
        .entry(map_name)
        .or_insert_with(|| Value::Object(Map::new()))
        .as_object_mut()
        .ok_or_else(|| format!("world node {map_name} must be an object"))?;
    metadata.insert(key.to_owned(), value);
    Ok(())
}

fn validate_schema_tables(schema: &Schema) -> Result<(), String> {
    for (index, property) in schema.properties.iter().enumerate() {
        if property.id != index || property.class >= schema.classes.len() {
            return Err("schema property identifiers are invalid".into());
        }
    }
    for (index, event) in schema.events.iter().enumerate() {
        if event.id != index || event.class >= schema.classes.len() {
            return Err("schema event identifiers are invalid".into());
        }
    }
    for class in &schema.classes {
        for property_id in &class.properties {
            let property = schema
                .properties
                .get(*property_id)
                .ok_or("schema class references a missing property")?;
            if property.class != class.id {
                return Err("schema class property belongs to a different class".into());
            }
        }
        for event_id in &class.events {
            let event = schema
                .events
                .get(*event_id)
                .ok_or("schema class references a missing event")?;
            if event.class != class.id {
                return Err("schema class event belongs to a different class".into());
            }
        }
    }
    Ok(())
}

fn insert_optional_string(
    output: &mut Map<String, Value>,
    key: &str,
    value: Option<&str>,
) -> Result<(), String> {
    if let Some(value) = value {
        if value.is_empty() {
            return Err(format!("{key} must be a non-empty string"));
        }
        output.insert(key.into(), Value::String(value.into()));
    }
    Ok(())
}

fn insert_optional_object(
    output: &mut Map<String, Value>,
    key: &str,
    value: Option<&Value>,
) -> Result<(), String> {
    if let Some(value) = value {
        if !value.is_object() {
            return Err(format!("{key} metadata must be an object"));
        }
        output.insert(key.into(), value.clone());
    }
    Ok(())
}

fn insert_optional_array(
    output: &mut Map<String, Value>,
    key: &str,
    value: Option<&Value>,
) -> Result<(), String> {
    if let Some(value) = value {
        if !value.is_array() {
            return Err(format!("{key} metadata must be an array"));
        }
        output.insert(key.into(), value.clone());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::schema::{ClassDesc, EventArgument, EventDesc, PropertyDesc};

    fn fixture_schema() -> Schema {
        Schema {
            version: 3,
            flags: 0,
            enums: Vec::new(),
            classes: vec![
                ClassDesc {
                    id: 0,
                    name: "Workspace".into(),
                    properties: vec![0],
                    events: vec![],
                    isr_properties: vec![],
                    unknown_u16: 0,
                },
                ClassDesc {
                    id: 1,
                    name: "Players".into(),
                    properties: vec![1],
                    events: vec![0],
                    isr_properties: vec![],
                    unknown_u16: 0,
                },
                ClassDesc {
                    id: 2,
                    name: "RunService".into(),
                    properties: vec![],
                    events: vec![],
                    isr_properties: vec![],
                    unknown_u16: 0,
                },
                ClassDesc {
                    id: 3,
                    name: "Player".into(),
                    properties: vec![],
                    events: vec![],
                    isr_properties: vec![],
                    unknown_u16: 0,
                },
                ClassDesc {
                    id: 4,
                    name: "Part".into(),
                    properties: vec![2, 3, 4],
                    events: vec![],
                    isr_properties: vec![],
                    unknown_u16: 0,
                },
            ],
            properties: vec![
                PropertyDesc {
                    id: 0,
                    class: 0,
                    name: "Gravity".into(),
                    type_id: 3,
                    enum_id: None,
                    lr_type: None,
                },
                PropertyDesc {
                    id: 1,
                    class: 1,
                    name: "LocalPlayer".into(),
                    type_id: 28,
                    enum_id: None,
                    lr_type: None,
                },
                PropertyDesc {
                    id: 2,
                    class: 4,
                    name: "CFrame".into(),
                    type_id: 27,
                    enum_id: None,
                    lr_type: None,
                },
                PropertyDesc {
                    id: 3,
                    class: 4,
                    name: "ReplicationPV".into(),
                    type_id: 57,
                    enum_id: None,
                    lr_type: None,
                },
                PropertyDesc {
                    id: 4,
                    class: 4,
                    name: "size".into(),
                    type_id: 22,
                    enum_id: None,
                    lr_type: None,
                },
            ],
            events: vec![EventDesc {
                id: 0,
                class: 1,
                name: "PlayerAdded".into(),
                arguments: vec![EventArgument {
                    type_id: 28,
                    enum_id: u16::MAX,
                }],
            }],
            strings: vec![String::new()],
            names: Vec::new(),
            lr_types: Vec::new(),
            parent_property_id: 2,
            decoded_bytes: 0,
        }
    }

    fn flagged_fixture() -> Value {
        let mut snapshot: Value =
            serde_json::from_str(include_str!("../../tests/fixtures/world_snapshot.json")).unwrap();
        snapshot
            .as_object_mut()
            .unwrap()
            .retain(|key, _| key == "version" || key == "sequence" || key == "nodes");
        for node in snapshot["nodes"].as_array_mut().unwrap() {
            let id = node["id"].as_str().unwrap();
            if ["scope:workspace", "scope:players", "scope:run-service"].contains(&id) {
                node["top_container_flags"] = json!([true, true]);
            }
        }
        snapshot
    }

    #[test]
    fn matches_python_snapshot_metadata_for_received_fixture() {
        let raw = flagged_fixture();
        let python: Value =
            serde_json::from_str(include_str!("../../tests/fixtures/world_snapshot.json")).unwrap();
        let metadata = RuntimeSnapshotMetadata {
            job_id: python["job_id"].as_str(),
            local_player: python["local_player"].as_str(),
            class_parents: Some(&python["class_parents"]),
            defaults: Some(&python["defaults"]),
            default_origins: Some(&python["default_origins"]),
            ..Default::default()
        };
        let output = build_runtime_snapshot(&fixture_schema(), &raw, &metadata).unwrap();
        assert_eq!(output["sequence"], python["sequence"]);
        assert_eq!(output["services"]["Workspace"], "scope:workspace");
        assert_eq!(output["services"]["Players"], "scope:players");
        assert_eq!(output["services"]["RunService"], "scope:run-service");
        assert_eq!(output["job_id"], python["job_id"]);
        assert_eq!(output["local_player"], python["local_player"]);
        assert_eq!(output["class_parents"], python["class_parents"]);
        assert_eq!(output["defaults"], python["defaults"]);
        assert_eq!(output["class_events"]["Players"], json!(["PlayerAdded"]));
        assert_eq!(
            output["class_event_signatures"]["Players"]["PlayerAdded"],
            json!([28])
        );
        assert_eq!(output["class_property_types"]["Workspace"]["Gravity"], 3);
        assert!(output.get("script_sources").is_none());
        assert!(output.get("builtin_player_scripts").is_none());
        assert!(output.get("integrity").is_none());
        assert!(output["nodes"][0].get("top_container_flags").is_none());
        assert!(output["nodes"][0]["origins"].is_null());
        assert_eq!(output["nodes"][0]["properties"]["Gravity"], 196.2);
    }

    #[test]
    fn preserves_missing_and_explicit_null_node_fields() {
        let raw = json!({"version":1,"sequence":9,"nodes":[
            {"id":"p","class_name":"Player"},
            {"id":"n","class_name":"Player","properties":{"Health":null}},
            {"id":"v","class_name":"Player","properties":{"Name":"Actual","Health":null},
             "origins":{"Health":"received"},"property_revisions":{"Health":3}}
        ]});
        let output =
            build_runtime_snapshot(&fixture_schema(), &raw, &RuntimeSnapshotMetadata::default())
                .unwrap();
        assert_eq!(output["nodes"][0]["name"], "Player");
        assert!(output["nodes"][0].get("properties").is_none());
        assert_eq!(output["nodes"][1]["name"], "Player");
        assert_eq!(output["nodes"][1]["properties"]["Health"], Value::Null);
        assert_eq!(output["nodes"][2]["name"], "Actual");
        assert_eq!(output["nodes"][2]["properties"]["Health"], Value::Null);
        assert_eq!(output["nodes"][2]["origins"]["Health"], "received");
        assert_eq!(output["nodes"][2]["property_revisions"]["Health"], 3);
        assert!(output.get("local_player").is_none());
        assert!(output.get("job_id").is_none());
        assert!(output.get("defaults").is_none());
        assert!(output.get("script_sources").is_none());
    }

    #[test]
    fn rejects_invalid_explicit_name_and_name_property_metadata() {
        for node in [
            json!({"id":"n","class_name":"Player","name":null}),
            json!({"id":"n","class_name":"Player","properties":{"Name":null}}),
            json!({"id":"n","class_name":"Player","properties":{"Name":{"$bytes_hex":"0xz1"}}}),
        ] {
            let raw = json!({"version":1,"nodes":[node]});
            assert!(build_runtime_snapshot(
                &fixture_schema(),
                &raw,
                &RuntimeSnapshotMetadata::default()
            )
            .is_err());
        }
    }

    #[test]
    fn real_session_snapshot_has_native_required_node_fields() {
        use crate::session::SessionCore;

        let schema = fixture_schema();
        let mut session = SessionCore::with_schema(schema.clone(), 8, 4096).unwrap();
        let mut packet = vec![129];
        packet.extend_from_slice(&[0; 7]); // global flags
        packet.push(1); // client scope
        packet.push(1); // one top container
        packet.push(1); // instance reference scope
        packet.extend_from_slice(&42_u32.to_le_bytes());
        packet.extend_from_slice(&0_u16.to_le_bytes()); // Workspace class
        packet.extend_from_slice(&[1, 0]); // container flags
        session.ingest(&packet).unwrap();

        let data_snapshot = session.snapshot().unwrap();
        let output =
            build_runtime_snapshot(&schema, &data_snapshot, &RuntimeSnapshotMetadata::default())
                .unwrap();
        let nodes = output["nodes"].as_array().unwrap();
        assert_eq!(nodes.len(), 1);
        assert_eq!(nodes[0]["id"], "1:42");
        assert_eq!(nodes[0]["class_name"], "Workspace");
        assert_eq!(nodes[0]["name"], "Workspace");
        assert_eq!(output["services"]["Workspace"], "1:42");
        for node in nodes {
            for field in ["id", "class_name", "name"] {
                assert!(
                    node[field].as_str().is_some(),
                    "missing native field {field}"
                );
                assert!(node[field].as_str().unwrap().len() <= 256);
            }
        }
    }

    #[test]
    fn rejects_ambiguous_services_and_untrusted_metadata_shapes() {
        let mut raw = flagged_fixture();
        let mut duplicate = raw["nodes"][0].clone();
        duplicate["id"] = json!("another-workspace");
        raw["nodes"].as_array_mut().unwrap().push(duplicate);
        assert!(build_runtime_snapshot(
            &fixture_schema(),
            &raw,
            &RuntimeSnapshotMetadata::default()
        )
        .unwrap_err()
        .contains("same service class"));

        let raw = flagged_fixture();
        let bad_sources = Value::Null;
        let metadata = RuntimeSnapshotMetadata {
            script_sources: Some(&bad_sources),
            ..Default::default()
        };
        assert!(build_runtime_snapshot(&fixture_schema(), &raw, &metadata)
            .unwrap_err()
            .contains("script_sources metadata must be an object"));
    }

    #[test]
    fn cframe_encodings_match_python_reference_vectors() {
        let vectors: Value = serde_json::from_str(include_str!(
            "../../tests/fixtures/runtime_snapshot_cframe_python_vectors.json"
        ))
        .unwrap();
        for key in [
            "type27_identity",
            "type27_unknown",
            "type51_identity",
            "type57_identity",
            "type51_unknown",
            "type57_unknown",
        ] {
            let item = &vectors[key];
            assert_eq!(
                normalize_cframe(&item["input"], item["type"].as_u64().unwrap() as u8),
                item["expected"],
                "Python snapshot parity vector {key}"
            );
        }
    }

    #[test]
    fn normalizes_received_frames_derives_position_and_size_idempotently() {
        let schema = fixture_schema();
        let raw = json!({"version":1,"sequence":3,"nodes":[{
            "id":"part","class_name":"Part","properties":{
                "CFrame":{"position":[1,2,3],"rotation_id":17,"packed_rotation":null},
                "ReplicationPV":{"position":[4,5,6],"rotation":1,"decoded_velocities":null},
                "size":{"$vector3":[2,3,4]}
            },"origins":{"CFrame":"received","ReplicationPV":"received","size":"received"},
            "property_revisions":{"CFrame":3,"ReplicationPV":4,"size":2,"Position":1}
        }]});
        let output =
            build_runtime_snapshot(&schema, &raw, &RuntimeSnapshotMetadata::default()).unwrap();
        let node = &output["nodes"][0];
        assert_eq!(
            node["properties"]["CFrame"]["$cframe"]["position"],
            json!([4, 5, 6])
        );
        assert_eq!(
            node["properties"]["CFrame"]["$cframe"]["rotation_mapping"],
            "native_745_identity"
        );
        assert_eq!(node["origins"]["CFrame"], "derived_received");
        assert_eq!(node["origins"]["ReplicationPV"], "received");
        assert_eq!(node["property_revisions"]["CFrame"], 4);
        assert_eq!(node["properties"]["Position"], json!({"$vector3":[4,5,6]}));
        assert_eq!(node["origins"]["Position"], "derived_received");
        assert_eq!(node["property_revisions"]["Position"], 4);
        assert_eq!(node["properties"]["Size"], json!({"$vector3":[2,3,4]}));
        assert_eq!(node["origins"]["Size"], "derived_received");
        assert_eq!(node["property_revisions"]["Size"], 2);

        let newer_position = json!({"version":1,"nodes":[{
            "id":"part-new-position","class_name":"Part","properties":{
                "CFrame":{"position":[1,2,3],"rotation_id":2},
                "Position":{"$vector3":[9,8,7]}
            },"origins":{"CFrame":"received","Position":"received"},
            "property_revisions":{"CFrame":4,"Position":5}
        }]});
        let newer = build_runtime_snapshot(
            &schema,
            &newer_position,
            &RuntimeSnapshotMetadata::default(),
        )
        .unwrap();
        assert_eq!(
            newer["nodes"][0]["properties"]["Position"],
            json!({"$vector3":[9,8,7]})
        );
        assert_eq!(newer["nodes"][0]["origins"]["Position"], "received");

        let second = build_runtime_snapshot(
            &schema,
            &json!({"version":1,"nodes":[node]}),
            &RuntimeSnapshotMetadata::default(),
        )
        .unwrap();
        assert_eq!(
            second["nodes"][0]["properties"]["CFrame"],
            node["properties"]["CFrame"]
        );
    }
}
