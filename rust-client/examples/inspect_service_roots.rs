//! Read-only service-parent evidence, excluding identifiers and property data.
use headless_luau::capture_replay;
use rove_client_core::session::{IngestOutcome,SessionCore};
use serde_json::json;
use std::{collections::BTreeMap,fs::File,io::BufReader};
fn main()->Result<(),Box<dyn std::error::Error>> {
    let path=std::env::args_os().nth(1).ok_or("expected private capture")?;
    let mut session=SessionCore::with_limits(4,32*1024*1024)?;
    let reader=session.retain(0)?;let mut sequence=0;let mut roots=BTreeMap::new();
    let count=capture_replay::read(BufReader::new(File::open(path)?),|packet,_| {
        let outcome=session.ingest(packet).map_err(|e|e.to_string())?;
        let next=match outcome {IngestOutcome::SchemaInstalled{..}=>return Ok(()),
            IngestOutcome::GlobalsCommitted{packet_sequence,..}|IngestOutcome::ReplicationCommitted{packet_sequence,..}=>packet_sequence};
        if matches!(outcome,IngestOutcome::GlobalsCommitted{..}) {
            for node in session.snapshot().unwrap()["nodes"].as_array().unwrap() {
                if node.get("top_container_flags").is_some() {roots.insert(node["id"].as_str().unwrap().to_owned(),node["class_name"].as_str().unwrap().to_owned());}
            }
        }
        session.acknowledge(reader,next).map_err(|e|e.to_string())?;sequence=next;Ok(())
    })?;
    let view=session.world_view().ok_or("no world")?;
    let rows:Vec<_>=roots.iter().map(|(id,class)| {let parent=view.parent(id);
        json!({"class":class,"present":view.contains(id),"has_wire_parent":parent.is_some(),
            "wire_parent_is_received_node":parent.is_some_and(|p|view.contains(p)),
            "wire_parent_class":parent.and_then(|p|view.class_name(p))})}).collect();
    println!("{}",json!({"ok":true,"mode":"offline","packets":count,"nodes":view.node_count(),"services":rows}));
    session.release(reader)?;let _=sequence;Ok(())
}
