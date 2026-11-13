//! Rust implementation of the immutable class-parent lookup used by native
//! engine bindings. This module deliberately does not interact with Luau.

use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::ffi::c_void;
use std::hash::{DefaultHasher, Hash, Hasher};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::slice;
use std::str;
use std::sync::{Arc, Mutex, OnceLock, Weak};

// The snapshot schema is bounded elsewhere, but the FFI boundary enforces its
// own limits because it may be called independently of snapshot parsing.
const MAX_CLASS_GRAPH_BYTES: usize = 16 * 1024 * 1024;
const MAX_CLASS_COUNT: usize = 100_000;
const MAX_CLASS_NAME_BYTES: usize = 1024;
const MAX_CACHED_CLOSURE_BYTES: usize = 2 * 1024 * 1024;
// Conservative allowance for hash-table buckets/control bytes, per-string
// allocator overhead, and each closure's map entry. This intentionally
// overestimates the retained representation instead of counting string bytes
// alone.
const CACHE_ENTRY_OVERHEAD_BYTES: usize = 256;
const CACHE_NAME_OVERHEAD_BYTES: usize = 128;
const MAX_SHARED_CLASS_GRAPHS: usize = 16;

struct AncestryIndex {
    // Only string parents are accepted. Array-valued parents retain only their
    // string elements, matching runtime/world_bindings.cpp::class_is_a.
    parents: HashMap<String, Vec<String>>,
    cache: Mutex<ClosureCache>,
}

struct RegistryEntry {
    digest: u64,
    index: Weak<AncestryIndex>,
}

static CLASS_GRAPH_REGISTRY: OnceLock<Mutex<Vec<RegistryEntry>>> = OnceLock::new();

#[derive(Default)]
struct ClosureCache {
    closures: HashMap<String, HashSet<String>>,
    approximate_bytes: usize,
}

impl AncestryIndex {
    fn from_json(bytes: &[u8]) -> Result<Self, ()> {
        if bytes.len() > MAX_CLASS_GRAPH_BYTES {
            return Err(());
        }
        let value: Value = serde_json::from_slice(bytes).map_err(|_| ())?;
        let object = value.as_object().ok_or(())?;
        if object.len() > MAX_CLASS_COUNT {
            return Err(());
        }

        let mut parents = HashMap::with_capacity(object.len());
        for (class, value) in object {
            validate_name(class)?;
            let values: Vec<&str> = match value {
                Value::String(parent) => vec![parent.as_str()],
                Value::Array(entries) => entries.iter().filter_map(Value::as_str).collect(),
                // Invalid parent values were ignored by the C++ traversal.
                _ => continue,
            };
            let mut names = Vec::with_capacity(values.len());
            for parent in values {
                validate_name(parent)?;
                names.push(parent.to_owned());
            }
            parents.insert(class.clone(), names);
        }
        Ok(Self {
            parents,
            cache: Mutex::new(ClosureCache::default()),
        })
    }

    fn is_a(&self, actual: &str, requested: &str) -> Result<bool, ()> {
        validate_name(actual)?;
        validate_name(requested)?;
        // Keep the exact fast-path semantics of the C++ helper.
        if actual == requested || requested == "Instance" || requested == "Object" {
            return Ok(true);
        }
        // An actual name absent from the graph has no outgoing edges. The
        // equality fast path above already covered its only possible positive
        // result, so do not retain arbitrary unknown caller-provided names.
        if !self.parents.contains_key(actual) {
            return Ok(false);
        }

        {
            let cache = self.cache.lock().map_err(|_| ())?;
            if let Some(closure) = cache.closures.get(actual) {
                return Ok(closure.contains(requested));
            }
        }

        // Match the C++ worklist traversal: include `actual`, visit each class
        // once (including on cycles), and follow only recognized parent values.
        let mut seen = HashSet::new();
        let mut todo = vec![actual.to_owned()];
        while let Some(class) = todo.pop() {
            if !seen.insert(class.clone()) {
                continue;
            }
            if let Some(class_parents) = self.parents.get(&class) {
                todo.extend(class_parents.iter().cloned());
            }
        }
        let result = seen.contains(requested);

        // Bound retained closures independently of the number of class names
        // queried by scripts. Large closures remain correct but uncached.
        if let Ok(mut cache) = self.cache.lock() {
            // Concurrent queries can compute the same closure before either
            // acquires this lock. Recheck the key while holding it so a race
            // neither replaces the entry nor charges its memory twice.
            if !cache.closures.contains_key(actual) {
                let cost = estimate_closure_cache_bytes(actual, &seen);
                if cost <= MAX_CACHED_CLOSURE_BYTES.saturating_sub(cache.approximate_bytes) {
                    cache.approximate_bytes += cost;
                    cache.closures.insert(actual.to_owned(), seen);
                }
            }
        }
        Ok(result)
    }
}

fn parent_digest(parents: &HashMap<String, Vec<String>>) -> u64 {
    let mut class_names: Vec<_> = parents.keys().collect();
    class_names.sort_unstable();
    let mut hasher = DefaultHasher::new();
    class_names.len().hash(&mut hasher);
    for class in class_names {
        class.hash(&mut hasher);
        parents
            .get(class)
            .expect("class key came from this map")
            .hash(&mut hasher);
    }
    hasher.finish()
}

fn intern_index(index: AncestryIndex) -> Arc<AncestryIndex> {
    let digest = parent_digest(&index.parents);
    let registry = CLASS_GRAPH_REGISTRY.get_or_init(|| Mutex::new(Vec::new()));
    let Ok(mut entries) = registry.lock() else {
        // The registry is only an optimization; a poisoned lock must not make
        // a valid World unavailable.
        return Arc::new(index);
    };

    entries.retain(|entry| entry.index.strong_count() != 0);
    for entry in entries.iter().filter(|entry| entry.digest == digest) {
        if let Some(existing) = entry.index.upgrade() {
            // A digest collision cannot cause worlds with different metadata
            // to share their class graph.
            if existing.parents == index.parents {
                return existing;
            }
        }
    }

    let shared = Arc::new(index);
    if entries.len() < MAX_SHARED_CLASS_GRAPHS {
        entries.push(RegistryEntry {
            digest,
            index: Arc::downgrade(&shared),
        });
    }
    shared
}

fn estimate_closure_cache_bytes(actual: &str, closure: &HashSet<String>) -> usize {
    closure.iter().fold(
        CACHE_ENTRY_OVERHEAD_BYTES
            .saturating_add(actual.len())
            .saturating_add(CACHE_NAME_OVERHEAD_BYTES),
        |bytes, name| {
            bytes
                .saturating_add(CACHE_NAME_OVERHEAD_BYTES)
                .saturating_add(name.len())
        },
    )
}

fn validate_name(name: &str) -> Result<(), ()> {
    if name.len() > MAX_CLASS_NAME_BYTES || name.contains('\0') {
        Err(())
    } else {
        Ok(())
    }
}

/// Creates an opaque reference to a shared immutable class ancestry index from
/// a UTF-8 JSON object. Equivalent live World graphs may share the index.
///
/// # Safety
/// For nonzero `len`, `json` must point to `len` readable bytes for the duration
/// of this call. The returned non-null pointer must be freed exactly once by
/// `local_rust_class_graph_free` before process shutdown. No Lua pointer is
/// accepted or retained.
#[no_mangle]
pub unsafe extern "C" fn local_rust_class_graph_new(json: *const u8, len: usize) -> *mut c_void {
    catch_unwind(AssertUnwindSafe(|| {
        if json.is_null() || len == 0 || len > MAX_CLASS_GRAPH_BYTES {
            return std::ptr::null_mut();
        }
        // SAFETY: guaranteed by this function's FFI contract; length is bounded.
        let bytes = unsafe { slice::from_raw_parts(json, len) };
        let Ok(index) = AncestryIndex::from_json(bytes) else {
            return std::ptr::null_mut();
        };
        Box::into_raw(Box::new(intern_index(index))).cast::<c_void>()
    }))
    .unwrap_or(std::ptr::null_mut())
}

/// Returns -1 for invalid arguments/errors, 0 when false, and 1 when true.
///
/// # Safety
/// `handle` must be a live pointer returned by `local_rust_class_graph_new` and
/// must not be freed concurrently. For nonzero name lengths, each name pointer
/// must reference that many readable UTF-8 bytes for the duration of this call.
#[no_mangle]
pub unsafe extern "C" fn local_rust_class_graph_is_a(
    handle: *mut c_void,
    actual: *const u8,
    actual_len: usize,
    requested: *const u8,
    requested_len: usize,
) -> i32 {
    catch_unwind(AssertUnwindSafe(|| {
        if handle.is_null()
            || actual.is_null()
            || requested.is_null()
            || actual_len > MAX_CLASS_NAME_BYTES
            || requested_len > MAX_CLASS_NAME_BYTES
        {
            return -1;
        }
        // SAFETY: guaranteed by this function's FFI contract and bounded above.
        let actual = match str::from_utf8(unsafe { slice::from_raw_parts(actual, actual_len) }) {
            Ok(value) => value,
            Err(_) => return -1,
        };
        // SAFETY: guaranteed by this function's FFI contract and bounded above.
        let requested =
            match str::from_utf8(unsafe { slice::from_raw_parts(requested, requested_len) }) {
                Ok(value) => value,
                Err(_) => return -1,
            };
        // SAFETY: caller promises a live handle returned by the constructor.
        let index = unsafe { &*handle.cast::<Arc<AncestryIndex>>() };
        match index.is_a(actual, requested) {
            Ok(true) => 1,
            Ok(false) => 0,
            Err(()) => -1,
        }
    }))
    .unwrap_or(-1)
}

/// Destroys an opaque index. Null is accepted as a no-op.
///
/// # Safety
/// A non-null pointer must be a live pointer returned by
/// `local_rust_class_graph_new`, and it must not be used concurrently.
#[no_mangle]
pub unsafe extern "C" fn local_rust_class_graph_free(handle: *mut c_void) {
    let _ = catch_unwind(AssertUnwindSafe(|| {
        if !handle.is_null() {
            // SAFETY: guaranteed by this function's FFI contract; free is once-only.
            drop(unsafe { Box::from_raw(handle.cast::<Arc<AncestryIndex>>()) });
        }
    }));
}

#[cfg(test)]
mod tests {
    use super::*;

    fn index(json: &str) -> AncestryIndex {
        AncestryIndex::from_json(json.as_bytes()).expect("valid class graph")
    }

    #[test]
    fn handles_equality_special_bases_and_unknown_classes() {
        let graph = index(r#"{"Part":"BasePart","BasePart":"Instance"}"#);
        assert!(graph.is_a("Part", "Part").unwrap());
        assert!(graph.is_a("Part", "Instance").unwrap());
        assert!(graph.is_a("Unknown", "Object").unwrap());
        assert!(!graph.is_a("Unknown", "BasePart").unwrap());
        assert!(!graph.cache.lock().unwrap().closures.contains_key("Unknown"));
    }

    #[test]
    fn handles_multi_parent_and_ignores_non_string_parent_values() {
        let graph = index(
            r#"{"Widget":["GuiObject",7,"LayerCollector"],"GuiObject":"Instance","LayerCollector":"Instance","Bad":false}"#,
        );
        assert!(graph.is_a("Widget", "GuiObject").unwrap());
        assert!(graph.is_a("Widget", "LayerCollector").unwrap());
        assert!(!graph.is_a("Widget", "7").unwrap());
        assert!(!graph.is_a("Bad", "GuiObject").unwrap());
    }

    #[test]
    fn terminates_cycles_and_caches_the_full_closure() {
        let graph = index(r#"{"A":["B","C"],"B":"A","C":"D"}"#);
        assert!(graph.is_a("A", "D").unwrap());
        assert!(graph.is_a("B", "C").unwrap());
        assert!(graph.cache.lock().unwrap().closures.contains_key("A"));
    }

    #[test]
    fn distinct_indexes_do_not_share_metadata_or_cache() {
        let first = index(r#"{"Part":"BasePart"}"#);
        let second = index(r#"{"Part":"GuiObject"}"#);
        assert!(second.cache.lock().unwrap().closures.is_empty());
        assert!(first.is_a("Part", "BasePart").unwrap());
        assert!(!second.is_a("Part", "BasePart").unwrap());
        assert_eq!(first.cache.lock().unwrap().closures.len(), 1);
        assert!(!second
            .cache
            .lock()
            .unwrap()
            .closures
            .get("Part")
            .unwrap()
            .contains("BasePart"));
    }

    #[test]
    fn identical_parent_graphs_share_but_changed_graphs_do_not() {
        let first = intern_index(index(r#"{"A":"B","B":["C","D"]}"#));
        let reordered = intern_index(index(r#"{"B":["C","D"],"A":"B"}"#));
        let changed = intern_index(index(r#"{"A":"Other","B":["C","D"]}"#));

        assert!(Arc::ptr_eq(&first, &reordered));
        assert!(!Arc::ptr_eq(&first, &changed));
        assert!(first.is_a("A", "C").unwrap());
        assert!(!changed.is_a("A", "C").unwrap());
    }

    #[test]
    fn weak_registry_does_not_retain_indexes_and_stays_bounded() {
        let graph = intern_index(index(r#"{"WeakRegistryReleaseProbe":"Parent"}"#));
        let weak = Arc::downgrade(&graph);
        drop(graph);
        assert!(weak.upgrade().is_none());

        let mut live = Vec::new();
        for number in 0..24 {
            let json = format!(r#"{{"BoundedProbe{number}":"Parent{number}"}}"#);
            live.push(intern_index(index(&json)));
        }
        let registry = CLASS_GRAPH_REGISTRY.get().unwrap().lock().unwrap();
        assert!(registry.len() <= MAX_SHARED_CLASS_GRAPHS);
        drop(registry);
        drop(live);
    }

    #[test]
    fn rejects_unbounded_or_invalid_inputs() {
        assert!(AncestryIndex::from_json(&vec![b' '; MAX_CLASS_GRAPH_BYTES + 1]).is_err());
        assert!(AncestryIndex::from_json(b"[]").is_err());
        assert!(index(r#"{"X":"Y"}"#)
            .is_a(&"X".repeat(MAX_CLASS_NAME_BYTES + 1), "Y")
            .is_err());
    }

    #[test]
    fn cache_retention_stays_under_two_mib_and_skips_oversized_closures() {
        let mut parents = HashMap::new();
        let mut names = Vec::new();
        for index in 0..3_000 {
            names.push(format!("{index:04}{}", "x".repeat(896)));
        }
        for pair in names.windows(2) {
            parents.insert(pair[0].clone(), vec![pair[1].clone()]);
        }
        parents.insert(names.last().unwrap().clone(), vec!["Terminal".to_owned()]);
        let graph = AncestryIndex {
            parents,
            cache: Mutex::new(ClosureCache::default()),
        };

        assert!(!graph.is_a(&names[0], "NotPresent").unwrap());
        let cache = graph.cache.lock().unwrap();
        assert!(cache.approximate_bytes <= MAX_CACHED_CLOSURE_BYTES);
        assert!(
            cache.closures.is_empty(),
            "oversized closure must not be retained"
        );
    }

    #[test]
    fn concurrent_queries_charge_an_identical_closure_once() {
        let graph = std::sync::Arc::new(index(r#"{"A":"B","B":"C"}"#));
        let mut threads = Vec::new();
        for _ in 0..8 {
            let graph = graph.clone();
            threads.push(std::thread::spawn(move || {
                assert!(!graph.is_a("A", "Missing").unwrap());
            }));
        }
        for thread in threads {
            thread.join().unwrap();
        }

        let cache = graph.cache.lock().unwrap();
        assert_eq!(cache.closures.len(), 1);
        let closure = cache.closures.get("A").unwrap();
        assert_eq!(
            cache.approximate_bytes,
            estimate_closure_cache_bytes("A", closure)
        );
        assert!(cache.approximate_bytes <= MAX_CACHED_CLOSURE_BYTES);
    }

    #[test]
    fn ffi_handle_can_be_created_queried_and_freed() {
        let json = br#"{"Part":"BasePart"}"#;
        // SAFETY: pointers and lengths refer to live byte arrays for each call.
        let handle = unsafe { local_rust_class_graph_new(json.as_ptr(), json.len()) };
        assert!(!handle.is_null());
        let actual = b"Part";
        let requested = b"BasePart";
        // SAFETY: handle came from the constructor and both slices remain live.
        assert_eq!(
            unsafe {
                local_rust_class_graph_is_a(
                    handle,
                    actual.as_ptr(),
                    actual.len(),
                    requested.as_ptr(),
                    requested.len(),
                )
            },
            1
        );
        // SAFETY: this releases the one live handle created above.
        unsafe { local_rust_class_graph_free(handle) };
    }
}
