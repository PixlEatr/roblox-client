//! Bounded, provenance-preserving runtime metadata and pinned source loading.
//!
//! This module only loads checked-in metadata/builtin assets and admits an
//! already-prepared per-session script catalog. It does not authenticate,
//! normalize, or execute server bytecode.
use rove_client_core::{
    runtime_snapshot::{build_runtime_snapshot, RuntimeSnapshotMetadata},
    schema::Schema,
};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{HashMap, VecDeque},
    fs,
    path::{Component, Path, PathBuf},
};

const MAX_NODES: usize = 20_000;
const MAX_CATALOG_ENTRIES: usize = 4096;
const MAX_CATALOG_BYTES: usize = 3 * 1024 * 1024;
const MAX_SOURCE_BYTES: usize = 128 * 1024;
const MAX_BUILTIN_ENTRIES: usize = 256;
const MAX_BUILTIN_BYTES: usize = 2 * 1024 * 1024;
const BYTECODE_PROVENANCE: &str = "pinned_builtin_luau_0_705";
const BYTECODE_COMMIT: &str = "93c83a46d7c6c1b040e58e82ef167340a7db1aef";

#[derive(Debug)]
pub enum RuntimeSourceError {
    Io(std::io::Error),
    Invalid(String),
}
impl std::fmt::Display for RuntimeSourceError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(e) => write!(f, "{e}"),
            Self::Invalid(e) => f.write_str(e),
        }
    }
}
impl std::error::Error for RuntimeSourceError {}
impl From<std::io::Error> for RuntimeSourceError {
    fn from(e: std::io::Error) -> Self {
        Self::Io(e)
    }
}
type Result<T> = std::result::Result<T, RuntimeSourceError>;

/// Values supplied by one runtime owner. Optional fields remain omitted when unknown.
#[derive(Clone, Debug, Default)]
pub struct RuntimeSourceMetadata {
    pub job_id: Option<String>,
    pub local_player: Option<String>,
    pub initial_replication_complete: Option<bool>,
    pub class_parents: Option<Value>,
    pub defaults: Option<Value>,
    pub default_origins: Option<Value>,
    pub script_sources: Option<Value>,
    pub builtin_player_scripts: Option<Value>,
}

impl RuntimeSourceMetadata {
    /// Load facts that are pinned to this checkout and derive account identity only
    /// from an actual replicated Player.UserId property.
    pub fn load(
        repo_root: &Path,
        snapshot: &Value,
        user_id: Option<u64>,
        initial_replication_complete: Option<bool>,
        script_catalog: Option<&Value>,
        include_builtins: bool,
    ) -> Result<Self> {
        let mut metadata = Self {
            local_player: user_id
                .map(|id| resolve_local_player(snapshot, id))
                .transpose()?
                .flatten(),
            initial_replication_complete,
            class_parents: Some(load_class_parents(repo_root)?),
            // Python currently emits these as known-empty maps; do not invent
            // constructor values from incomplete packet state.
            defaults: Some(json!({})),
            default_origins: Some(json!({})),
            script_sources: script_catalog.map(validate_catalog).transpose()?,
            ..Self::default()
        };
        if include_builtins {
            metadata.builtin_player_scripts = Some(load_builtin_player_scripts(repo_root)?);
        }
        Ok(metadata)
    }

    pub fn build_snapshot(&self, schema: &Schema, data_snapshot: &Value) -> Result<Value> {
        let metadata = RuntimeSnapshotMetadata {
            job_id: self.job_id.as_deref(),
            local_player: self.local_player.as_deref(),
            initial_replication_complete: self.initial_replication_complete,
            class_parents: self.class_parents.as_ref(),
            defaults: self.defaults.as_ref(),
            default_origins: self.default_origins.as_ref(),
            script_sources: self.script_sources.as_ref(),
            builtin_player_scripts: self.builtin_player_scripts.as_ref(),
            ..RuntimeSnapshotMetadata::default()
        };
        build_runtime_snapshot(schema, data_snapshot, &metadata)
            .map_err(RuntimeSourceError::Invalid)
    }

    /// Refresh the pending initial-snapshot selection from a newer full world
    /// snapshot. This does not mutate an already-running VM; the owner should
    /// defer VM initialization until `local_player` is present. A missing
    /// match remains unknown.
    pub fn refresh_local_player(&mut self, snapshot: &Value, user_id: u64) -> Result<bool> {
        let current = resolve_local_player(snapshot, user_id)?;
        let changed = current != self.local_player;
        self.local_player = current;
        Ok(changed)
    }
}

/// Resolve only a unique, explicitly replicated Player.UserId match. No player
/// heuristic, display-name matching, or local-player fallback is performed.
pub fn resolve_local_player(snapshot: &Value, user_id: u64) -> Result<Option<String>> {
    let nodes = snapshot
        .get("nodes")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            RuntimeSourceError::Invalid("world snapshot nodes must be an array".into())
        })?;
    if nodes.len() > MAX_NODES {
        return invalid("world snapshot exceeds 20000 nodes");
    }
    let mut matches = Vec::new();
    for node in nodes {
        let Some(object) = node.as_object() else {
            return invalid("world node must be an object");
        };
        if object.get("class_name").and_then(Value::as_str) != Some("Player") {
            continue;
        }
        let Some(properties) = object.get("properties").and_then(Value::as_object) else {
            continue;
        };
        let Some(actual) = properties.get("UserId").and_then(Value::as_u64) else {
            continue;
        };
        if actual == user_id {
            let id = object
                .get("id")
                .and_then(Value::as_str)
                .filter(|v| !v.is_empty())
                .ok_or_else(|| {
                    RuntimeSourceError::Invalid("matching Player has no valid instance id".into())
                })?;
            matches.push(id.to_owned());
        }
    }
    match matches.len() {
        0 => Ok(None),
        1 => Ok(matches.pop()),
        _ => invalid("multiple replicated Players match the issued UserId"),
    }
}

pub fn load_class_parents(repo_root: &Path) -> Result<Value> {
    let path = repo_root.join("runtime/class-parents-745.json");
    let root: Value = read_json(&path)?;
    let parents = root
        .get("parents")
        .and_then(Value::as_object)
        .ok_or_else(|| {
            RuntimeSourceError::Invalid("class-parent file has no parents object".into())
        })?;
    let mut filtered = Map::new();
    for (class, parent) in parents {
        if let Some(parent) = parent.as_str() {
            filtered.insert(class.clone(), Value::String(parent.to_owned()));
        }
    }
    Ok(Value::Object(filtered))
}

/// Validate and clone an already-prepared catalog without changing any entry
/// fields. Missing catalog (`None`) remains unknown; explicit `{}` stays known-empty.
pub fn validate_catalog(value: &Value) -> Result<Value> {
    let entries = value
        .as_object()
        .ok_or_else(|| RuntimeSourceError::Invalid("script catalog must be an object".into()))?;
    if entries.len() > MAX_CATALOG_ENTRIES {
        return invalid("script catalog exceeds 4096 entries");
    }
    let mut bytes = 0usize;
    for (id, entry) in entries {
        if id.is_empty() || id.len() > 256 {
            return invalid("script catalog contains an invalid instance id");
        }
        let object = entry.as_object().ok_or_else(|| {
            RuntimeSourceError::Invalid("script catalog entry must be an object".into())
        })?;
        if object.get("source").is_some_and(|s| !s.is_string()) {
            return invalid("script source must be text when present");
        }
        if let Some(source) = object.get("source").and_then(Value::as_str) {
            if source.len() > MAX_SOURCE_BYTES {
                return invalid("script source exceeds 128 KiB");
            }
        }
        bytes = bytes.saturating_add(
            serde_json::to_vec(entry)
                .map_err(|e| RuntimeSourceError::Invalid(e.to_string()))?
                .len(),
        );
        if bytes > MAX_CATALOG_BYTES {
            return invalid("script catalog exceeds 3 MiB");
        }
    }
    Ok(value.clone())
}

/// Bounded per-instance catalog cache. A changed input digest invalidates the
/// cached value; eviction is least-recently-used and never grows unbounded.
#[derive(Default)]
pub struct ScriptCatalogCache {
    entries: HashMap<String, (String, Value, usize)>,
    order: VecDeque<String>,
    bytes: usize,
}
impl ScriptCatalogCache {
    pub fn get_or_insert(&mut self, id: &str, input: &[u8], entry: Value) -> Result<&Value> {
        if id.is_empty() || id.len() > 256 {
            return invalid("invalid script instance id");
        }
        let digest = hex(&Sha256::digest(input));
        if self
            .entries
            .get(id)
            .is_some_and(|(old, _, _)| old == &digest)
        {
            self.touch(id);
            return Ok(&self.entries[id].1);
        }
        let size = serde_json::to_vec(&entry)
            .map_err(|e| RuntimeSourceError::Invalid(e.to_string()))?
            .len();
        if size > MAX_SOURCE_BYTES {
            return invalid("cached script entry exceeds 128 KiB");
        }
        if let Some((_, _, old)) = self.entries.remove(id) {
            self.bytes = self.bytes.saturating_sub(old);
        }
        self.order.retain(|key| key != id);
        while self.entries.len() >= MAX_CATALOG_ENTRIES
            || self.bytes.saturating_add(size) > MAX_CATALOG_BYTES
        {
            let Some(oldest) = self.order.pop_front() else {
                return invalid("script catalog cache cannot fit entry");
            };
            if let Some((_, _, old_size)) = self.entries.remove(&oldest) {
                self.bytes = self.bytes.saturating_sub(old_size);
            }
        }
        self.bytes += size;
        self.order.push_back(id.to_owned());
        self.entries.insert(id.to_owned(), (digest, entry, size));
        Ok(&self.entries[id].1)
    }
    fn touch(&mut self, id: &str) {
        self.order.retain(|key| key != id);
        self.order.push_back(id.to_owned());
    }
    pub fn len(&self) -> usize {
        self.entries.len()
    }
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
    pub fn estimated_bytes(&self) -> usize {
        self.bytes
    }
}

/// Load the pinned client-owned PlayerScripts, checking manifests and hashes
/// before exposing any source or bytecode. The caller should retain this bundle
/// once per runtime session (the Python host uses a one-entry process cache).
pub fn load_builtin_player_scripts(repo_root: &Path) -> Result<Value> {
    let bundle = repo_root.join("vendor/roblox-player-scripts-745");
    let provenance: Value = read_json(&bundle.join("provenance.json"))?;
    let bytecode_manifest: Value = read_json(&bundle.join("bytecode-v6-manifest.json"))?;
    let compiler = bytecode_manifest
        .get("compiler")
        .and_then(Value::as_object)
        .ok_or_else(|| {
            RuntimeSourceError::Invalid(
                "builtin bytecode manifest lacks compiler provenance".into(),
            )
        })?;
    if bytecode_manifest.get("format").and_then(Value::as_str) != Some("luau-bytecode-fixtures-v1")
        || compiler.get("name").and_then(Value::as_str) != Some("Luau")
        || compiler.get("tag").and_then(Value::as_str) != Some("0.705")
        || compiler.get("commit").and_then(Value::as_str) != Some(BYTECODE_COMMIT)
        || compiler.get("arguments") != Some(&json!(["--binary", "-O1", "-g1"]))
    {
        return invalid("builtin bytecode compiler provenance differs from its pin");
    }
    let file_rows = provenance
        .get("files")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            RuntimeSourceError::Invalid("builtin source manifest has no file table".into())
        })?;
    let bc_rows = bytecode_manifest
        .get("files")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            RuntimeSourceError::Invalid("builtin bytecode manifest has no file table".into())
        })?;
    let mut compiled = HashMap::new();
    for row in bc_rows {
        let path = row
            .get("path")
            .and_then(Value::as_str)
            .ok_or_else(|| RuntimeSourceError::Invalid("invalid bytecode manifest path".into()))?;
        if compiled.insert(path.to_owned(), row).is_some() {
            return invalid("builtin bytecode manifest contains duplicate paths");
        }
    }
    let mut files = HashMap::new();
    for row in file_rows {
        let relative = row
            .get("path")
            .and_then(Value::as_str)
            .ok_or_else(|| RuntimeSourceError::Invalid("invalid builtin source path".into()))?;
        let path = safe_bundle_path(&bundle, relative)?;
        let raw = fs::read(path)?;
        let expected = row.get("sha256").and_then(Value::as_str).ok_or_else(|| {
            RuntimeSourceError::Invalid("source manifest entry lacks hash".into())
        })?;
        if hex(&Sha256::digest(&raw)) != expected {
            return invalid("builtin source differs from its pinned source hash");
        }
        let text = String::from_utf8(raw)
            .map_err(|_| RuntimeSourceError::Invalid("builtin source is not UTF-8".into()))?;
        files.insert(relative.to_owned(), text);
    }
    let expected_bc: Vec<_> = files
        .keys()
        .filter(|p| p.ends_with(".lua"))
        .cloned()
        .collect();
    if expected_bc.len() != compiled.len() || expected_bc.iter().any(|p| !compiled.contains_key(p))
    {
        return invalid("builtin bytecode manifest does not exactly cover Lua sources");
    }
    let topology = topology_roots(&bundle)?;
    let mut nodes: HashMap<Vec<String>, Value> = HashMap::new();
    let mut total = 0usize;
    for (path, source) in &files {
        if !path.ends_with(".lua") {
            continue;
        }
        let parts: Vec<String> = Path::new(path)
            .components()
            .skip(1)
            .map(|part| part.as_os_str().to_string_lossy().to_string())
            .collect();
        let names: Vec<String> = parts
            .iter()
            .map(|part| {
                let stem = part.strip_suffix(".lua").unwrap_or(part);
                stem.strip_suffix(".module").unwrap_or(stem).to_owned()
            })
            .collect();
        if names.is_empty() || names.len() > 16 {
            return invalid("builtin script path depth is invalid");
        }
        for depth in 1..names.len() {
            nodes
                .entry(names[..depth].to_vec())
                .or_insert(json!({"class_name":"Folder"}));
        }
        let mut node = json!({"class_name": if names.len() == 1 { topology.get(&names[0]).map(String::as_str).unwrap_or("ModuleScript") } else { "ModuleScript" }, "source":source,
            "repository_path":format!("scripts/PlayerScripts/{path}"), "source_sha256":hex(&Sha256::digest(source.as_bytes()))});
        let bc = compiled[path].as_object().expect("validated manifest row");
        if bc.get("source_sha256").and_then(Value::as_str) != node["source_sha256"].as_str() {
            return invalid("builtin bytecode source hash mismatch");
        }
        let bc_path = bc
            .get("bytecode_path")
            .and_then(Value::as_str)
            .ok_or_else(|| RuntimeSourceError::Invalid("builtin bytecode path missing".into()))?;
        let bytecode = fs::read(safe_bundle_path(&bundle, bc_path)?)?;
        if hex(&Sha256::digest(&bytecode))
            != bc
                .get("bytecode_sha256")
                .and_then(Value::as_str)
                .unwrap_or("")
            || bc.get("bytecode_size").and_then(Value::as_u64) != Some(bytecode.len() as u64)
            || bytecode.len() < 2
            || bytecode[0] != 6
            || bytecode[1] != 3
            || bc.get("bytecode_version").and_then(Value::as_u64) != Some(6)
            || bc.get("type_version").and_then(Value::as_u64) != Some(3)
        {
            return invalid("builtin bytecode differs from its pinned compiler output");
        }
        node["bytecode_hex"] = Value::String(hex(&bytecode));
        node["bytecode_sha256"] = bc["bytecode_sha256"].clone();
        node["bytecode_version"] = json!(6);
        node["bytecode_type_version"] = json!(3);
        node["bytecode_provenance"] = json!(BYTECODE_PROVENANCE);
        node["bytecode_compiler_commit"] = json!(BYTECODE_COMMIT);
        total = total.saturating_add(
            serde_json::to_vec(&node)
                .map_err(|e| RuntimeSourceError::Invalid(e.to_string()))?
                .len(),
        );
        if nodes.len() >= MAX_BUILTIN_ENTRIES || total > MAX_BUILTIN_BYTES {
            return invalid("builtin PlayerScripts bundle exceeds native limits");
        }
        nodes.insert(names, node);
    }
    if nodes.len() > MAX_BUILTIN_ENTRIES {
        return invalid("builtin PlayerScripts bundle exceeds native entry limit");
    }
    let mut entries: Vec<_> = nodes.into_iter().collect();
    entries.sort_by(|a, b| a.0.len().cmp(&b.0.len()).then(a.0.cmp(&b.0)));
    Ok(Value::Array(
        entries
            .into_iter()
            .map(|(path, mut node)| {
                node["path"] = json!(path);
                node
            })
            .collect(),
    ))
}

fn topology_roots(bundle: &Path) -> Result<HashMap<String, String>> {
    let mut result = HashMap::new();
    for file in ["PlayerScriptsCommon.rbxp", "PlayerScriptsLoader.rbxp"] {
        let tree: Value = read_json5_trailing_commas(&bundle.join(file))?;
        let current = &tree["instanceHierarchy"]["tree"]["StarterPlayer"]["StarterPlayerScripts"];
        if !current.is_object() {
            return invalid("builtin topology lacks StarterPlayerScripts");
        }
        for (name, value) in current.as_object().unwrap() {
            if let Some(class) = value.get("$className").and_then(Value::as_str) {
                result.insert(name.clone(), class.to_owned());
            }
        }
    }
    Ok(result)
}
fn read_json(path: &Path) -> Result<Value> {
    serde_json::from_slice(&fs::read(path)?)
        .map_err(|e| RuntimeSourceError::Invalid(format!("invalid JSON {}: {e}", path.display())))
}
fn read_json5_trailing_commas(path: &Path) -> Result<Value> {
    let bytes = fs::read(path)?;
    let text = std::str::from_utf8(&bytes)
        .map_err(|_| RuntimeSourceError::Invalid("builtin topology is not UTF-8".into()))?;
    let mut out = String::with_capacity(text.len());
    let mut chars = text.chars().peekable();
    let (mut quoted, mut escaped) = (false, false);
    while let Some(ch) = chars.next() {
        if quoted {
            out.push(ch);
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == '"' {
                quoted = false;
            }
        } else if ch == '"' {
            quoted = true;
            out.push(ch);
        } else if ch == ',' {
            let mut lookahead = chars.clone();
            while lookahead.peek().is_some_and(|c| c.is_whitespace()) {
                lookahead.next();
            }
            if !matches!(lookahead.peek(), Some('}') | Some(']')) {
                out.push(ch);
            }
        } else {
            out.push(ch);
        }
    }
    serde_json::from_str(&out)
        .map_err(|e| RuntimeSourceError::Invalid(format!("invalid JSON {}: {e}", path.display())))
}
fn safe_bundle_path(bundle: &Path, relative: &str) -> Result<PathBuf> {
    let rel = Path::new(relative);
    if rel.components().any(|c| !matches!(c, Component::Normal(_))) {
        return invalid("invalid builtin asset path");
    }
    let base = bundle.canonicalize()?;
    let path = bundle.join(rel).canonicalize()?;
    if !path.starts_with(base) {
        return invalid("builtin asset escapes its pinned bundle");
    }
    Ok(path)
}
fn hex(bytes: &[u8]) -> String {
    const H: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        out.push(H[(b >> 4) as usize] as char);
        out.push(H[(b & 15) as usize] as char);
    }
    out
}
fn invalid<T>(message: &str) -> Result<T> {
    Err(RuntimeSourceError::Invalid(message.to_owned()))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn root() -> PathBuf {
        std::env::var_os("RCR_TEST_PROJECT_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                Path::new(env!("CARGO_MANIFEST_DIR"))
                    .parent()
                    .unwrap()
                    .to_path_buf()
            })
    }
    #[test]
    fn local_player_uses_unique_userid_and_missing_is_unknown() {
        let snapshot = json!({"nodes":[{"id":"game","class_name":"DataModel"},{"id":"p:7","class_name":"Player","properties":{"UserId":42}}]});
        assert_eq!(
            resolve_local_player(&snapshot, 42).unwrap().as_deref(),
            Some("p:7")
        );
        assert_eq!(resolve_local_player(&snapshot, 12).unwrap(), None);
        let duplicate = json!({"nodes":[{"id":"a","class_name":"Player","properties":{"UserId":42}},{"id":"b","class_name":"Player","properties":{"UserId":42}}]});
        assert!(resolve_local_player(&duplicate, 42).is_err());
    }
    #[test]
    fn pending_metadata_refreshes_only_from_a_unique_owned_player() {
        let mut metadata = RuntimeSourceMetadata::default();
        let waiting = json!({"nodes":[]});
        assert!(!metadata.refresh_local_player(&waiting, 42).unwrap());
        let arrived =
            json!({"nodes":[{"id":"p:7","class_name":"Player","properties":{"UserId":42}}]});
        assert!(metadata.refresh_local_player(&arrived, 42).unwrap());
        assert_eq!(metadata.local_player.as_deref(), Some("p:7"));
    }
    #[test]
    fn catalog_preserves_unknown_known_empty_and_provenance() {
        assert!(validate_catalog(&json!({}))
            .unwrap()
            .as_object()
            .unwrap()
            .is_empty());
        let catalog =
            json!({"2:3":{"source":"print('ok')","provenance":"received_source","reason":"known"}});
        assert_eq!(validate_catalog(&catalog).unwrap(), catalog);
        assert!(
            validate_catalog(&json!({"id":{"source":"x".repeat(MAX_SOURCE_BYTES+1)}})).is_err()
        );
    }
    #[test]
    fn cache_invalidates_and_stays_bounded() {
        let mut cache = ScriptCatalogCache::default();
        assert_eq!(
            cache
                .get_or_insert("x", b"a", json!({"source":"a"}))
                .unwrap(),
            &json!({"source":"a"})
        );
        assert_eq!(
            cache
                .get_or_insert("x", b"b", json!({"source":"b"}))
                .unwrap(),
            &json!({"source":"b"})
        );
        assert_eq!(cache.len(), 1);
    }
    #[test]
    fn checked_in_metadata_and_builtin_bundle_load() {
        assert!(
            load_class_parents(&root())
                .unwrap()
                .as_object()
                .unwrap()
                .len()
                > 100
        );
        let bundle = load_builtin_player_scripts(&root()).unwrap();
        assert!(bundle.as_array().unwrap().len() > 0);
        for entry in bundle.as_array().unwrap() {
            if entry.get("bytecode_hex").is_some() {
                assert_eq!(entry["bytecode_provenance"], BYTECODE_PROVENANCE);
            }
        }
    }
}
