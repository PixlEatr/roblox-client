//! Physics-only reads of received values and pinned constructor defaults.
//! Defaults require a fully decoded creation record (ordinary or streamed),
//! or an identified global service. They never establish network ownership.

use rove_client_core::world::WorldView;
use serde_json::Value;
use std::sync::OnceLock;

fn constructors() -> &'static Value {
    static VALUES: OnceLock<Value> = OnceLock::new();
    VALUES.get_or_init(|| {
        serde_json::from_str(include_str!("../../runtime/physics-constructors-745.json"))
            .expect("pinned constructor catalog")
    })
}

pub fn constructor_known(view: &WorldView, id: &str) -> bool {
    let Some(node) = view.read_only_node(id) else {
        return false;
    };
    if node.record_field("received_creation_complete") == Some(Value::Bool(true)) {
        return true;
    }
    // Workspace is constructed before its validated globals announcement.
    node.class_name() == "Workspace"
        && node
            .record_field("top_container_flags")
            .is_some_and(|value| {
                value
                    .as_array()
                    .is_some_and(|flags| flags.len() == 2 && flags.iter().all(Value::is_boolean))
            })
}

pub fn property(view: &WorldView, id: &str, name: &str) -> Option<Value> {
    if let Some(value) = view.get_property(id, name) {
        return Some(value);
    }
    if !matches!(
        name,
        "Anchored"
            | "CanCollide"
            | "Gravity"
            | "WalkSpeed"
            | "HipHeight"
            | "RigType"
            | "Shape"
            | "Size"
    ) || !constructor_known(view, id)
    {
        return None;
    }
    constructors()
        .get("classes")?
        .get(view.class_name(id)?)?
        .get("properties")?
        .get(name)?
        .get("value")
        .cloned()
}

pub fn vector(value: Option<Value>) -> Option<[f64; 3]> {
    let value = value?;
    let values = value.get("$vector3").unwrap_or(&value).as_array()?;
    if values.len() != 3 {
        return None;
    }
    let mut result = [0.; 3];
    for (output, value) in result.iter_mut().zip(values) {
        *output = value.as_f64().filter(|n| n.is_finite())?;
    }
    Some(result)
}

pub fn frame(view: &WorldView, id: &str) -> Option<Value> {
    let node = view.read_only_node(id)?;
    let revision = |key| {
        node.metadata_field("property_revisions", key)
            .and_then(|v| v.as_u64())
            .unwrap_or(0)
    };
    let keys = if revision("CFrame") > revision("ReplicationPV") {
        ["CFrame", "ReplicationPV"]
    } else {
        ["ReplicationPV", "CFrame"]
    };
    keys.into_iter().find_map(|key| {
        let value = node.property(key)?;
        let frame = value.get("$cframe").cloned().unwrap_or(value);
        vector(frame.get("position").cloned())?;
        Some(frame)
    })
}

pub fn name_is(view: &WorldView, id: &str, expected: &str) -> bool {
    if let Some(name) = view.name(id) {
        return name == expected;
    }
    match view.get_property(id, "Name") {
        Some(Value::String(name)) => name == expected,
        Some(Value::Object(value)) if value.len() == 1 => value
            .get("$bytes_hex")
            .and_then(Value::as_str)
            .and_then(|hex| rove_client_core::runtime_snapshot::decode_name_hex(hex).ok())
            .is_some_and(|name| name == expected),
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rove_client_core::world::WorldStore;
    use serde_json::json;

    #[test]
    fn physics_defaults_equal_the_pinned_constructor_values() {
        let source: Value =
            serde_json::from_str(include_str!("../../runtime/class-constructors-745.json"))
                .unwrap();
        for (class, entry) in constructors()["classes"].as_object().unwrap() {
            for (name, property) in entry["properties"].as_object().unwrap() {
                assert_eq!(
                    property["value"],
                    source["classes"][class]["properties"][name]["value"]
                );
            }
        }
    }

    #[test]
    fn incomplete_creation_stays_unknown_and_received_false_is_preserved() {
        let source = json!({"version":1,"nodes":[
            {"id":"1:1","class_name":"Part","properties":{}},
            {"id":"1:2","class_name":"Part","received_creation_complete":true,"properties":{}},
            {"id":"1:3","class_name":"Part","received_creation_complete":true,
                "properties":{"Anchored":true,"CanCollide":false}}
        ]});
        let world = WorldStore::from_snapshot(&source).unwrap();
        let view = world.view();
        assert_eq!(property(&view, "1:1", "Anchored"), None);
        assert_eq!(property(&view, "1:2", "Anchored"), Some(json!(false)));
        assert_eq!(property(&view, "1:2", "CanCollide"), Some(json!(true)));
        assert_eq!(property(&view, "1:3", "Anchored"), Some(json!(true)));
        assert_eq!(property(&view, "1:3", "CanCollide"), Some(json!(false)));
        assert_eq!(property(&view, "1:2", "NetworkOwnerV3"), None);
    }
}
