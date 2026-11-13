//! Compare per-view JSON world copies with the shared WorldStore overlays.
//!
//! This measures only the normalized world representation. It excludes the
//! Luau VM, network transport, renderer, and allocator-specific RSS effects.
//! Run each mode in a separate process when comparing resident memory.

use rove_client_core::world::{WorldStore, WorldView};
use serde_json::{Map, Value, json};
use std::{
    env,
    hint::black_box,
    process::ExitCode,
    thread,
    time::{Duration, Instant},
};

const MAX_NODES: usize = 20_000;
const MAX_VIEWS: usize = 128;
const MAX_TOTAL_VIEW_NODES: usize = 100_000;
const MAX_HOLD_MS: u64 = 60_000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Mode {
    DeepCopy,
    Shared,
}

#[derive(Debug)]
struct Options {
    mode: Mode,
    nodes: usize,
    views: usize,
    hold_ms: u64,
}

fn parse_options(args: impl IntoIterator<Item = String>) -> Result<Options, String> {
    let mut args = args.into_iter();
    let mut mode = None;
    let mut nodes = 1_000usize;
    let mut views = 8usize;
    let mut hold_ms = 3_000u64;
    let mut saw_nodes = false;
    let mut saw_views = false;
    let mut saw_hold = false;

    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--help" | "-h" => return Err(String::new()),
            "--mode" => {
                if mode.is_some() {
                    return Err("--mode may be supplied once".into());
                }
                mode = Some(match args.next().as_deref() {
                    Some("deep-copy") => Mode::DeepCopy,
                    Some("shared") => Mode::Shared,
                    Some(_) => return Err("--mode must be deep-copy or shared".into()),
                    None => return Err("--mode requires a value".into()),
                });
            }
            "--nodes" => {
                if saw_nodes {
                    return Err("--nodes may be supplied once".into());
                }
                nodes = parse_next(&mut args, "--nodes")?;
                saw_nodes = true;
            }
            "--views" => {
                if saw_views {
                    return Err("--views may be supplied once".into());
                }
                views = parse_next(&mut args, "--views")?;
                saw_views = true;
            }
            "--hold-ms" => {
                if saw_hold {
                    return Err("--hold-ms may be supplied once".into());
                }
                hold_ms = parse_next(&mut args, "--hold-ms")?;
                saw_hold = true;
            }
            _ => return Err(format!("unknown option: {arg}")),
        }
    }

    let mode = mode.ok_or("--mode is required (deep-copy or shared)")?;
    if !(1..=MAX_NODES).contains(&nodes) {
        return Err(format!("--nodes must be 1..{MAX_NODES}"));
    }
    if !(1..=MAX_VIEWS).contains(&views) {
        return Err(format!("--views must be 1..{MAX_VIEWS}"));
    }
    if hold_ms > MAX_HOLD_MS {
        return Err(format!("--hold-ms must be 0..{MAX_HOLD_MS}"));
    }
    let total_view_nodes = nodes.checked_mul(views).ok_or("node/view count overflow")?;
    if total_view_nodes > MAX_TOTAL_VIEW_NODES {
        return Err(format!(
            "nodes × views exceeds the safe benchmark cap of {MAX_TOTAL_VIEW_NODES}"
        ));
    }
    Ok(Options {
        mode,
        nodes,
        views,
        hold_ms,
    })
}

fn parse_next<T: std::str::FromStr>(
    args: &mut impl Iterator<Item = String>,
    flag: &str,
) -> Result<T, String> {
    args.next()
        .ok_or_else(|| format!("{flag} requires a value"))?
        .parse()
        .map_err(|_| format!("invalid value for {flag}"))
}

fn usage() -> &'static str {
    "Usage: cargo run --release --example world_memory -- --mode deep-copy|shared [--nodes N] [--views N] [--hold-ms M]\nDefaults: 1000 nodes, 8 views, 3000 ms. Caps: 20000 nodes, 128 views, 100000 node-view pairs, 60000 ms. Run each mode as a separate process for RSS comparison."
}

fn make_snapshot(node_count: usize) -> Value {
    let mut nodes = Vec::with_capacity(node_count);
    nodes.push(json!({
        "id": "scope:workspace",
        "class_name": "Workspace",
        "name": "Workspace",
        "parent": null,
        "properties": {"Gravity": 196.2},
        "attributes": {"benchmark": true},
        "attributes_known": true,
        "collection_tags": [],
        "collection_tags_known": true,
        "origins": {"Gravity": "received"}
    }));

    for index in 1..node_count {
        let x = (index % 997) as f64 / 16.0;
        let y = (index % 127) as f64 / 8.0;
        let z = (index % 509) as f64 / 32.0;
        let material = match index % 4 {
            0 => "Plastic",
            1 => "Wood",
            2 => "Concrete",
            _ => "Metal",
        };
        nodes.push(json!({
            "id": format!("part:{index:05}"),
            "class_name": "Part",
            "name": format!("Part{index:05}"),
            "parent": "scope:workspace",
            "properties": {
                "Anchored": false,
                "Position": {"$vector3": [x, y, z]},
                "Size": {"$vector3": [2.0, 2.0, 1.0]},
                "Material": material,
                "Reflectance": 0.0
            },
            "attributes": {"batch": index % 16, "benchmark": true},
            "attributes_known": true,
            "collection_tags": ["benchmark", material],
            "collection_tags_known": true,
            "origins": {
                "Anchored": "received",
                "Position": "received",
                "Size": "received",
                "Material": "received"
            }
        }));
    }

    let mut snapshot = Map::new();
    snapshot.insert("version".into(), Value::from(1_u64));
    snapshot.insert("sequence".into(), Value::from(1_000_000_u64));
    snapshot.insert("place_id".into(), Value::from(0_u64));
    snapshot.insert(
        "class_schema".into(),
        json!({
            "version": 745,
            "classes": ["Workspace", "Part"],
            "properties": ["Anchored", "Position", "Size", "Material", "Gravity"]
        }),
    );
    snapshot.insert(
        "services".into(),
        json!({
            "Workspace": "scope:workspace",
            "Players": "scope:players"
        }),
    );
    snapshot.insert("nodes".into(), Value::Array(nodes));
    Value::Object(snapshot)
}

fn target_index(view_index: usize, node_count: usize) -> usize {
    if node_count <= 1 {
        0
    } else {
        1 + (view_index % (node_count - 1))
    }
}

fn target_id(view_index: usize, node_count: usize) -> String {
    if node_count <= 1 {
        "scope:workspace".to_owned()
    } else {
        format!("part:{:05}", target_index(view_index, node_count))
    }
}

fn fnv_bytes(checksum: &mut u64, bytes: &[u8]) {
    for byte in bytes {
        *checksum ^= u64::from(*byte);
        *checksum = checksum.wrapping_mul(0x100000001b3);
    }
}

fn checksum_value(value: &Value, checksum: &mut u64) {
    match value {
        Value::Null => fnv_bytes(checksum, b"null"),
        Value::Bool(value) => fnv_bytes(checksum, if *value { b"true" } else { b"false" }),
        Value::Number(value) => {
            let number = value.as_f64().unwrap_or_default();
            fnv_bytes(checksum, &number.to_bits().to_le_bytes());
        }
        Value::String(value) => fnv_bytes(checksum, value.as_bytes()),
        _ => fnv_bytes(checksum, value.to_string().as_bytes()),
    }
}

fn query_deep_copy(views: &[Value], ids: &[String], node_count: usize) -> Result<u64, String> {
    let mut checksum = 0xcbf29ce484222325_u64;
    let mut top_level_metadata: Option<(&Value, &Value)> = None;
    for (view_index, view) in views.iter().enumerate() {
        let nodes = view
            .get("nodes")
            .and_then(Value::as_array)
            .ok_or("deep-copy nodes missing")?;
        if nodes.len() != node_count {
            return Err("deep-copy node count changed".into());
        }
        let metadata = (
            view.get("services")
                .ok_or("deep-copy services metadata missing")?,
            view.get("class_schema")
                .ok_or("deep-copy schema metadata missing")?,
        );
        if let Some(expected) = top_level_metadata {
            if metadata != expected {
                return Err("deep-copy view metadata changed".into());
            }
        } else {
            top_level_metadata = Some(metadata);
        }
        for (node_index, id) in ids.iter().enumerate() {
            let node = &nodes[node_index];
            if node.get("id").and_then(Value::as_str) != Some(id.as_str()) {
                return Err("deep-copy node order or ID changed".into());
            }
            fnv_bytes(&mut checksum, id.as_bytes());
            let properties = node
                .get("properties")
                .and_then(Value::as_object)
                .ok_or("deep-copy properties missing")?;
            if let Some(anchored) = properties.get("Anchored") {
                checksum_value(anchored, &mut checksum);
                let position = properties
                    .get("Position")
                    .and_then(|v| v.get("$vector3"))
                    .and_then(Value::as_array)
                    .ok_or("deep-copy Position missing")?;
                for component in position {
                    checksum_value(component, &mut checksum);
                }
            } else {
                checksum_value(
                    properties
                        .get("Gravity")
                        .ok_or("workspace Gravity missing")?,
                    &mut checksum,
                );
            }
            if node_index == target_index(view_index, node_count) {
                let marker = properties
                    .get("BenchmarkMutation")
                    .and_then(Value::as_u64)
                    .ok_or("deep-copy view mutation missing")?;
                if marker != view_index as u64 {
                    return Err("deep-copy view mutation mismatch".into());
                }
                checksum_value(&Value::from(marker), &mut checksum);
            } else if properties.contains_key("BenchmarkMutation") {
                return Err("deep-copy local mutation escaped its view target".into());
            }
        }
    }
    Ok(checksum)
}

fn run_deep_copy(options: &Options, snapshot: Value, ids: &[String]) -> Result<RunStats, String> {
    let mut views = Vec::with_capacity(options.views);
    for view_index in 0..options.views {
        let mut view = snapshot.clone();
        let target = target_index(view_index, options.nodes);
        let properties = view["nodes"][target]["properties"]
            .as_object_mut()
            .ok_or("deep-copy target properties missing")?;
        properties.insert("BenchmarkMutation".into(), Value::from(view_index as u64));
        views.push(view);
    }
    let checksum = query_deep_copy(&views, ids, options.nodes)?;
    Ok(RunStats {
        checksum,
        store_estimated_bytes: None,
        shared_base_bytes: None,
        overlay_bytes: None,
        held: Held::DeepCopy {
            root: snapshot,
            views,
        },
    })
}

fn query_shared(
    store: &WorldStore,
    views: &[WorldView],
    ids: &[String],
    node_count: usize,
) -> Result<u64, String> {
    let mut checksum = 0xcbf29ce484222325_u64;
    for (view_index, view) in views.iter().enumerate() {
        if view.node_count() != node_count {
            return Err("shared view node count changed".into());
        }
        for (node_index, id) in ids.iter().enumerate() {
            if !view.contains(id) {
                return Err("shared view lost a node".into());
            }
            fnv_bytes(&mut checksum, id.as_bytes());
            if let Some(anchored) = view.get_property(id, "Anchored") {
                checksum_value(&anchored, &mut checksum);
                let position_value = view
                    .get_property(id, "Position")
                    .ok_or("shared Position missing")?;
                let position = position_value
                    .get("$vector3")
                    .and_then(Value::as_array)
                    .ok_or("shared Position missing")?;
                for component in position {
                    checksum_value(component, &mut checksum);
                }
            } else {
                checksum_value(
                    &view.get_property(id, "Gravity")
                        .ok_or("workspace Gravity missing")?,
                    &mut checksum,
                );
            }
            if node_index == target_index(view_index, node_count) {
                let marker = view
                    .get_property(id, "BenchmarkMutation")
                    .and_then(|value| value.as_u64())
                    .ok_or("shared view mutation missing")?;
                if marker != view_index as u64 {
                    return Err("shared view mutation mismatch".into());
                }
                checksum_value(&Value::from(marker), &mut checksum);
            } else if view.get_property(id, "BenchmarkMutation").is_some() {
                return Err("shared local mutation escaped its view target".into());
            }
        }
    }
    if store.len() != node_count {
        return Err("shared canonical world count changed".into());
    }
    Ok(checksum)
}

fn run_shared(options: &Options, snapshot: Value, ids: &[String]) -> Result<RunStats, String> {
    let store = WorldStore::from_snapshot(&snapshot)?;
    drop(snapshot); // The measured shared mode keeps only WorldStore + sparse views.
    let mut views: Vec<WorldView> = (0..options.views).map(|_| store.view()).collect();
    for (view_index, view) in views.iter_mut().enumerate() {
        let target = target_id(view_index, options.nodes);
        view.set_property(&target, "BenchmarkMutation", Value::from(view_index as u64))?;
    }
    for (view_index, view) in views.iter().enumerate() {
        let target = target_id(view_index, options.nodes);
        let marker = view
            .get_property(&target, "BenchmarkMutation")
            .and_then(|value| value.as_u64());
        if marker != Some(view_index as u64) {
            return Err("view isolation check failed".into());
        }
        if store.get_property(&target, "BenchmarkMutation").is_some() {
            return Err("local view mutation reached the canonical store".into());
        }
    }
    let checksum = query_shared(&store, &views, ids, options.nodes)?;
    let usage = store.shared_memory_usage(&views);
    Ok(RunStats {
        checksum,
        store_estimated_bytes: Some(store.retained_bytes()),
        shared_base_bytes: Some(usage.shared_base_bytes),
        overlay_bytes: Some(usage.overlay_bytes),
        held: Held::Shared { store, views },
    })
}

enum Held {
    DeepCopy {
        root: Value,
        views: Vec<Value>,
    },
    Shared {
        store: WorldStore,
        views: Vec<WorldView>,
    },
}

struct RunStats {
    checksum: u64,
    store_estimated_bytes: Option<usize>,
    shared_base_bytes: Option<usize>,
    overlay_bytes: Option<usize>,
    held: Held,
}

fn run(options: Options) -> Result<(), String> {
    let snapshot = make_snapshot(options.nodes);
    let ids = snapshot["nodes"]
        .as_array()
        .ok_or("generated nodes missing")?
        .iter()
        .map(|node| node["id"].as_str().unwrap_or_default().to_owned())
        .collect::<Vec<_>>();
    let start = Instant::now();
    let stats = match options.mode {
        Mode::DeepCopy => run_deep_copy(&options, snapshot, &ids)?,
        Mode::Shared => run_shared(&options, snapshot, &ids)?,
    };
    let build_and_query_ms = start.elapsed().as_secs_f64() * 1000.0;

    // Keep the complete measured representation live while an external RSS
    // sampler reads the process. black_box also makes the lifetime explicit.
    let retained_values = match &stats.held {
        Held::DeepCopy { root, views } => {
            black_box((root, views));
            views.len() + 1
        }
        Held::Shared { store, views } => {
            black_box((store, views));
            views.len() + 1
        }
    };
    if options.hold_ms > 0 {
        thread::sleep(Duration::from_millis(options.hold_ms));
    }
    let _ = black_box(&stats.held);

    let mut output = Map::new();
    output.insert(
        "mode".into(),
        Value::String(
            match options.mode {
                Mode::DeepCopy => "deep-copy",
                Mode::Shared => "shared",
            }
            .into(),
        ),
    );
    output.insert("world_store_only".into(), Value::Bool(true));
    output.insert("includes_luau_vm".into(), Value::Bool(false));
    output.insert("includes_transport".into(), Value::Bool(false));
    output.insert(
        "scope".into(),
        Value::String("world-store-only; excludes Luau, transport, and renderer".into()),
    );
    output.insert(
        "rss_measurement".into(),
        Value::String("sample process RSS externally while hold_ms is active".into()),
    );
    output.insert("nodes".into(), Value::from(options.nodes as u64));
    output.insert("views".into(), Value::from(options.views as u64));
    output.insert(
        "retained_values".into(),
        Value::from(retained_values as u64),
    );
    output.insert(
        "checksum".into(),
        Value::String(format!("{:016x}", stats.checksum)),
    );
    output.insert("isolation".into(), Value::String("passed".into()));
    output.insert("hold_ms".into(), Value::from(options.hold_ms));
    output.insert("build_and_query_ms".into(), Value::from(build_and_query_ms));
    if let Some(bytes) = stats.store_estimated_bytes {
        output.insert(
            "world_store_retained_estimate_bytes".into(),
            Value::from(bytes as u64),
        );
    }
    if let Some(bytes) = stats.shared_base_bytes {
        output.insert(
            "shared_base_estimate_bytes".into(),
            Value::from(bytes as u64),
        );
    }
    if let Some(bytes) = stats.overlay_bytes {
        output.insert(
            "view_overlay_estimate_bytes".into(),
            Value::from(bytes as u64),
        );
    }
    println!("{}", Value::Object(output));
    Ok(())
}

fn main() -> ExitCode {
    match parse_options(env::args().skip(1)) {
        Ok(options) => match run(options) {
            Ok(()) => ExitCode::SUCCESS,
            Err(error) => {
                eprintln!("world memory benchmark failed: {error}");
                ExitCode::FAILURE
            }
        },
        Err(error) if error.is_empty() => {
            println!("{}", usage());
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("{error}\n{}", usage());
            ExitCode::FAILURE
        }
    }
}
