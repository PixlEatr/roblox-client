//! Lazy engine-compatible projection over canonical packed world records.
//!
//! A projected node owns one `ReadOnlyNode` plus shared schema/service context.
//! Normalization is performed on demand and temporary JSON is dropped at the
//! end of each read; no projected all-world property map is retained.

use rove_client_core::{
    runtime_delta::{ClassPropertyTypeIndex, RuntimeDeltaNormalizer},
    runtime_snapshot::{
        decode_name_hex, normalize_node_values, project_node_origin, project_node_property,
        project_node_revision, projected_frame_winner,
    },
    schema::Schema,
    world::{ReadOnlyField, ReadOnlyNode, WorldView},
};
use serde_json::{json, Map, Value};
use std::{collections::HashMap, sync::Arc};

#[derive(Clone)]
pub struct ProjectionContext {
    property_types: Arc<ClassPropertyTypeIndex>,
    /// service id -> (class name, projected DataModel parent id)
    service_roots: HashMap<String, (String, String)>,
}

impl ProjectionContext {
    pub fn new(
        schema: &Schema,
        service_roots: HashMap<String, (String, String)>,
    ) -> Result<Arc<Self>, String> {
        let mut property_types = HashMap::with_capacity(schema.classes.len());
        for class in &schema.classes {
            let mut types = HashMap::with_capacity(class.properties.len());
            for property_id in &class.properties {
                let property = schema
                    .properties
                    .get(*property_id)
                    .ok_or("schema class references missing property")?;
                if property.class != class.id {
                    return Err("schema class property belongs to another class".into());
                }
                types.insert(property.name.clone(), property.type_id);
            }
            property_types.insert(class.name.clone(), types);
        }
        Ok(Arc::new(Self {
            property_types: Arc::new(property_types),
            service_roots,
        }))
    }

    pub fn from_normalizer(normalizer: &RuntimeDeltaNormalizer) -> Arc<Self> {
        let (property_types, service_roots) = normalizer.projection_parts();
        Arc::new(Self {
            property_types,
            service_roots,
        })
    }
}

#[derive(Clone)]
pub struct ProjectedWorldView {
    pub view: Arc<WorldView>,
    pub context: Arc<ProjectionContext>,
}

impl ProjectedWorldView {
    pub fn new(view: WorldView, context: Arc<ProjectionContext>) -> Self {
        Self {
            view: Arc::new(view),
            context,
        }
    }

    pub fn sequence(&self) -> u64 {
        self.view.sequence()
    }
    pub fn node_count(&self) -> usize {
        self.view.node_count()
    }

    pub fn for_each_node(&self, mut visit: impl FnMut(ProjectedNode)) {
        let context = self.context.clone();
        self.view.for_each_node(|node| {
            visit(ProjectedNode {
                node,
                context: context.clone(),
            })
        });
    }

    pub fn lookup_node(&self, id: &str) -> Option<ProjectedNode> {
        self.view.read_only_node(id).map(|node| ProjectedNode {
            node,
            context: self.context.clone(),
        })
    }
}

#[derive(Clone)]
pub struct ProjectedNode {
    node: ReadOnlyNode,
    context: Arc<ProjectionContext>,
}

impl ProjectedNode {
    pub fn raw(&self) -> &ReadOnlyNode {
        &self.node
    }
    pub fn id(&self) -> &str {
        self.node.id()
    }
    pub fn class_name(&self) -> &str {
        self.node.class_name()
    }

    /// Projected name follows initial/create normalization: explicit sidecar,
    /// then string/encoded Name property, then class name.
    pub fn name(&self) -> Result<String, String> {
        match self.node.name() {
            ReadOnlyField::Value(name) => return Ok(name.to_owned()),
            ReadOnlyField::Null => return Err("node name is null".into()),
            ReadOnlyField::Missing => {}
        }
        match self.node.property("Name") {
            None => Ok(self.class_name().to_owned()),
            Some(Value::String(name)) => Ok(name),
            Some(Value::Object(value)) if value.len() == 1 && value.contains_key("$bytes_hex") => {
                let hex = value["$bytes_hex"]
                    .as_str()
                    .ok_or("encoded Name bytes must be hexadecimal text")?;
                decode_name_hex(hex)
            }
            Some(_) => Err("world Name property must be a string when present".into()),
        }
    }

    pub fn parent(&self) -> ReadOnlyField<String> {
        if let Some((service_class, fallback)) = self.context.service_roots.get(self.id()) {
            let destroyed = self.node.record_field("destroyed") == Some(json!(true));
            if service_class == self.class_name()
                && !destroyed
                && (self.node.created_sequence().is_some()
                    || matches!(
                        self.node.parent(),
                        ReadOnlyField::Missing | ReadOnlyField::Null
                    ))
            {
                return ReadOnlyField::Value(fallback.clone());
            }
        }
        match self.node.parent() {
            ReadOnlyField::Missing => ReadOnlyField::Missing,
            ReadOnlyField::Null => ReadOnlyField::Null,
            ReadOnlyField::Value(parent) => ReadOnlyField::Value(parent.to_owned()),
        }
    }

    fn normalized_node(&self) -> Result<Value, String> {
        let mut object = Map::new();
        object.insert("id".into(), json!(self.id()));
        object.insert("class_name".into(), json!(self.class_name()));
        object.insert("name".into(), json!(self.name()?));
        match self.parent() {
            ReadOnlyField::Missing => {}
            ReadOnlyField::Null => {
                object.insert("parent".into(), Value::Null);
            }
            ReadOnlyField::Value(parent) => {
                object.insert("parent".into(), json!(parent));
            }
        }
        match self.node.properties() {
            ReadOnlyField::Missing => {}
            ReadOnlyField::Null => {
                object.insert("properties".into(), Value::Null);
            }
            ReadOnlyField::Value(properties) => {
                object.insert("properties".into(), Value::Object(properties));
            }
        }
        for section in [
            "origins",
            "attributes",
            "property_revisions",
            "tags",
            "collection_tags",
        ] {
            match self.node.metadata_section(section) {
                ReadOnlyField::Missing => {}
                ReadOnlyField::Null => {
                    object.insert(section.into(), Value::Null);
                }
                ReadOnlyField::Value(value) => {
                    object.insert(section.into(), value);
                }
            }
        }
        if let Some(sequence) = self.node.created_sequence() {
            let property_keys = object
                .get("properties")
                .and_then(Value::as_object)
                .map(|properties| properties.keys().cloned().collect::<Vec<_>>());
            if let Some(property_keys) = property_keys {
                let mut origins = object
                    .remove("origins")
                    .and_then(|v| v.as_object().cloned())
                    .unwrap_or_default();
                let mut revisions = object
                    .remove("property_revisions")
                    .and_then(|v| v.as_object().cloned())
                    .unwrap_or_default();
                for key in &property_keys {
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
            }
        }
        let mut value = Value::Object(object);
        normalize_node_values(
            &mut value,
            self.context.property_types.get(self.class_name()),
        )?;
        Ok(value)
    }

    /// A normalized engine property. Absent and explicit JSON null remain
    /// distinct (`None` versus `Some(Value::Null)`).
    pub fn property(&self, key: &str) -> Result<Option<Value>, String> {
        project_node_property(
            &self.node,
            self.context.property_types.get(self.class_name()),
            key,
        )
    }

    pub fn origin(&self, key: &str) -> Result<Option<Value>, String> {
        project_node_origin(
            &self.node,
            self.context.property_types.get(self.class_name()),
            key,
        )
    }

    pub fn revision(&self, key: &str) -> Result<Option<Value>, String> {
        project_node_revision(
            &self.node,
            self.context.property_types.get(self.class_name()),
            key,
        )
    }

    /// Explicit full-section operation, intended for clone/export only.
    pub fn section(&self, section: &str) -> Result<ReadOnlyField<Value>, String> {
        if matches!(section, "attributes" | "tags" | "collection_tags") {
            return Ok(self.node.metadata_section(section));
        }
        let node = self.normalized_node()?;
        Ok(match node.get(section) {
            None => ReadOnlyField::Missing,
            Some(Value::Null) => ReadOnlyField::Null,
            Some(value) => ReadOnlyField::Value(value.clone()),
        })
    }

    pub fn section_len(&self, section: &str) -> Result<Option<usize>, String> {
        if matches!(section, "attributes" | "tags" | "collection_tags") {
            return Ok(match self.node.metadata_section(section) {
                ReadOnlyField::Missing | ReadOnlyField::Null => None,
                ReadOnlyField::Value(Value::Object(map)) => Some(map.len()),
                ReadOnlyField::Value(Value::Array(items)) => Some(items.len()),
                ReadOnlyField::Value(_) => None,
            });
        }
        if section == "properties" {
            return match self.node.properties_len() {
                ReadOnlyField::Missing | ReadOnlyField::Null => Ok(None),
                ReadOnlyField::Value(mut count) => {
                    if self.node.property("Size").is_none()
                        && self.node.property("size").is_some()
                        && self
                            .context
                            .property_types
                            .get(self.class_name())
                            .and_then(|types| types.get("size"))
                            == Some(&22)
                    {
                        count += 1;
                    }
                    if let Some(winner) = projected_frame_winner(&self.node)? {
                        if winner == "ReplicationPV" && self.node.property("CFrame").is_none() {
                            count += 1;
                        }
                        if self.node.property("Position").is_none() {
                            count += 1;
                        }
                    }
                    Ok(Some(count))
                }
            };
        }
        let value = self.normalized_node()?;
        let Some(value) = value.get(section) else {
            return Ok(None);
        };
        if value.is_null() {
            return Ok(None);
        }
        Ok(value.as_object().map(|map| map.len()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine_world_view::SharedWorldView;
    use rove_client_core::{
        runtime_snapshot::{build_runtime_snapshot, RuntimeSnapshotMetadata},
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
                properties: vec![0, 1, 2],
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
                    name: "ReplicationPV".into(),
                    type_id: 57,
                    enum_id: None,
                    lr_type: None,
                },
                PropertyDesc {
                    id: 2,
                    class: 0,
                    name: "size".into(),
                    type_id: 22,
                    enum_id: None,
                    lr_type: None,
                },
            ],
            events: vec![],
            strings: vec![String::new()],
            names: vec![],
            lr_types: vec![],
            parent_property_id: 3,
            decoded_bytes: 0,
        }
    }

    #[test]
    fn projected_initial_fields_match_snapshot_normalizer() {
        let schema = schema();
        let raw = json!({"version":1,"sequence":4,"nodes":[{"id":"p","class_name":"Part","properties":{
            "CFrame":{"position":[1.0,2.0,3.0],"rotation_id":2},
            "ReplicationPV":{"position":[5.0,6.0,7.0],"rotation":1},
            "size":{"$vector3":[2.0,3.0,4.0]}
        },"property_revisions":{"CFrame":2,"ReplicationPV":3}}]});
        let expected =
            build_runtime_snapshot(&schema, &raw, &RuntimeSnapshotMetadata::default()).unwrap();
        let store = WorldStore::from_snapshot(&raw).unwrap();
        let context = ProjectionContext::new(&schema, HashMap::new()).unwrap();
        let view = SharedWorldView::projected(store.view(), context);
        let node = view.lookup_node("p").unwrap();
        let expected_node = &expected["nodes"][0];
        for key in ["CFrame", "ReplicationPV", "size", "Size", "Position"] {
            assert_eq!(
                node.property(key).unwrap(),
                expected_node["properties"].get(key).cloned()
            );
            assert_eq!(
                node.origin(key).unwrap(),
                expected_node["origins"].get(key).cloned()
            );
            assert_eq!(
                node.revision(key).unwrap(),
                expected_node["property_revisions"].get(key).cloned()
            );
        }
    }

    #[test]
    fn created_node_defaults_metadata_without_retaining_sequence_maps() {
        let schema = schema();
        let mut store =
            WorldStore::from_snapshot(&json!({"version":1,"sequence":0,"nodes":[]})).unwrap();
        store.apply_delta(&json!({"version":1,"delta":true,"sequence":7,"actions":[{"sequence":7,"kind":"create","node":{
            "id":"p","class_name":"Part","properties":{"size":{"$vector3":[1,1,1]}}
        }}]})).unwrap();
        let context = ProjectionContext::new(&schema, HashMap::new()).unwrap();
        let view = SharedWorldView::projected(store.view(), context);
        let node = view.lookup_node("p").unwrap();
        assert_eq!(node.origin("size").unwrap(), Some(json!("received")));
        assert_eq!(node.revision("size").unwrap(), Some(json!(7)));
        assert_eq!(
            node.origin("Size").unwrap(),
            Some(json!("derived_received"))
        );
        assert_eq!(node.revision("Size").unwrap(), Some(json!(7)));
        assert_eq!(node.revision("Parent").unwrap(), Some(json!(7)));
        assert_eq!(node.raw().created_sequence(), Some(7));
    }
}
