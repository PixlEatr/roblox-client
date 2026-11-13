//! the opt-in lightweight mode drops the listed visual/audio asset properties.
//! instance ids, parents, attributes, gameplay fields and physics inputs stay.
//! scripts can still use those; omitted asset properties report unavailable.
use std::{collections::HashSet, sync::OnceLock};
use serde_json::{Map, Value};

fn names() -> &'static HashSet<&'static str> {
    static NAMES: OnceLock<HashSet<&'static str>> = OnceLock::new();
    NAMES.get_or_init(|| include_str!("../../runtime/lightweight_assets.inc").lines()
        .filter_map(|line|line.strip_prefix("ROVE_ASSET_PROPERTY(\"")?.strip_suffix("\")"))
        .collect())
}
pub fn enabled() -> bool {
    static ENABLED: OnceLock<bool> = OnceLock::new();
    *ENABLED.get_or_init(||std::env::var("ROVE_LIGHTWEIGHT").as_deref()==Ok("1"))
}
pub(crate) fn omitted(name: &str) -> bool { enabled() && names().contains(name) }
pub(crate) fn filter_map(values: &Map<String,Value>) -> Map<String,Value> {
    values.iter().filter(|(key,_)|!omitted(key)).map(|(key,value)|(key.clone(),value.clone())).collect()
}
pub(crate) fn filter_action(action: &mut Value) {
    if !enabled() { return; }
    if action["kind"]=="create" {
        if let Some(node)=action.get_mut("node").and_then(Value::as_object_mut) {
            for key in ["properties","origins","property_revisions"] {
                if let Some(fields)=node.get_mut(key).and_then(Value::as_object_mut) {
                    fields.retain(|key,_|!names().contains(key.as_str()));
                }
            }
        }
    } else if action["kind"]=="set" {
        if let Some(fields)=action.get_mut("fields").and_then(Value::as_object_mut) {
            fields.retain(|key,_|!names().contains(key.as_str()));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn asset_policy_keeps_gameplay_physics_and_script_fields() {
        assert!(names().len()>=20);
        for key in ["ModelMeshData","TextureID","SoundId"] {assert!(names().contains(key));}
        for key in ["Name","Parent","CFrame","size","Size","Health","WalkSpeed","Anchored",
            "CanCollide","CanTouch","CanQuery","CollisionGroupReplicate","CustomPhysicalProperties",
            "PhysicalConfigData","Source","Visible","Text","Value"] {
            assert!(!names().contains(key),"gameplay property {key} must remain available");
        }
    }
}
