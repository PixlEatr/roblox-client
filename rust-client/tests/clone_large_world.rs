//! Exercise Instance:Clone against a large received world with the production
//! Rust runtime and Luau bridge. Elapsed time is reported for comparison but
//! is not used as a machine-sensitive pass/fail threshold.
use headless_luau::runtime_owner::{successful, Runtime};
use serde_json::{json, Value};
use std::time::Instant;

fn large_world(node_count: usize) -> Value {
    let mut world: Value = serde_json::from_str(include_str!(
        "../../tools/fixtures/rust-rewrite/compact/snapshot.json"
    ))
    .unwrap();
    let nodes = world["nodes"].as_array_mut().unwrap();
    for index in 0..node_count {
        nodes.push(json!({
            "id":format!("clone-bench-{index:05}"),
            "class_name":"Part",
            "name":format!("Passive_{index:05}"),
            "parent":"scope:workspace",
            "properties":{"Anchored":true}
        }));
    }
    world
}

#[test]
fn empty_and_nonempty_clones_preserve_behavior_in_a_40000_node_world() {
    let mut runtime = Runtime::new(large_world(40_000), 64).unwrap();

    let started = Instant::now();
    let empty = runtime
        .call(json!({
            "op":"execute",
            "timeout_ms":1000,
            "source":r#"
                local empty=Instance.new("Part",workspace)
                empty.Name="EmptyCloneProbe"
                local copy=empty:Clone()
                assert(copy~=empty and copy.ClassName=="Part")
                assert(copy.Parent==nil and #copy:GetChildren()==0)
                return true
            "#
        }))
        .unwrap();
    eprintln!(
        "40k-world empty clone request: {:.2} ms",
        started.elapsed().as_secs_f64() * 1000.0
    );
    successful(&empty)
        .unwrap_or_else(|error| panic!("empty clone failed: {error}; result={empty}"));
    assert_eq!(empty["values"], json!([true]));

    let started = Instant::now();
    let subtree = runtime
        .call(json!({
            "op":"execute",
            "timeout_ms":1000,
            "source":r#"
                local source=Instance.new("Folder",workspace)
                source.Name="SubtreeCloneProbe"
                local child=Instance.new("Folder",source)
                child.Name="Child"
                local leaf=Instance.new("Part",child)
                leaf.Name="Leaf"
                leaf:SetAttribute("Value",7)
                local clone=source:Clone()
                assert(clone.Parent==nil and clone.Child.Leaf.ClassName=="Part")
                assert(clone.Child.Leaf:GetAttribute("Value")==7)
                assert(clone.Child.Leaf~=leaf)
                return #clone:GetDescendants()
            "#
        }))
        .unwrap();
    eprintln!(
        "40k-world subtree clone request: {:.2} ms",
        started.elapsed().as_secs_f64() * 1000.0
    );
    successful(&subtree)
        .unwrap_or_else(|error| panic!("subtree clone failed: {error}; result={subtree}"));
    assert_eq!(subtree["values"], json!([2.0]));
}
