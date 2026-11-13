//! let c++ read the packed rust world through a versioned c api.
//! views share the existing `WorldView`. a node handle pins just its own record,
//! so holding an instance does not keep an entire old world revision alive.
//! json exports stop at one property, metadata field or node section.

use crate::engine_world_projection::{ProjectedNode, ProjectedWorldView, ProjectionContext};
use rove_client_core::world::{ReadOnlyField, ReadOnlyNode, WorldView};
use serde_json::{Map, Value};
use std::{
    ffi::c_void,
    panic::{catch_unwind, AssertUnwindSafe},
    ptr, slice,
    sync::Arc,
};

const API_VERSION: u32 = 1;

/// Section IDs shared with the native consumer.
pub const SECTION_PROPERTIES: u32 = 0;
pub const SECTION_ORIGINS: u32 = 1;
pub const SECTION_ATTRIBUTES: u32 = 2;
pub const SECTION_PROPERTY_REVISIONS: u32 = 3;
pub const SECTION_COLLECTION_TAGS: u32 = 4;

/// Versioned callback table. Signed byte-return values use `-1` for missing,
/// `-2` for an explicit null section, and `-3` for invalid arguments or an
/// internal failure. A nonnegative result is the required or written byte
/// count. Passing a null output pointer performs a length query.
#[repr(C)]
pub struct RustWorldViewApi {
    pub version: u32,
    pub clone_view: unsafe extern "C" fn(*const c_void) -> *mut c_void,
    pub drop_view: unsafe extern "C" fn(*mut c_void),
    pub sequence: unsafe extern "C" fn(*const c_void) -> u64,
    pub node_count: unsafe extern "C" fn(*const c_void) -> usize,
    pub lookup_node: unsafe extern "C" fn(*const c_void, *const u8, usize) -> *mut c_void,
    pub clone_node: unsafe extern "C" fn(*const c_void) -> *mut c_void,
    pub drop_node: unsafe extern "C" fn(*mut c_void),
    /// Text field IDs: 0=id, 1=class_name, 2=name, 3=parent.
    /// Returns 0=missing, 1=null, 2=value, -1=invalid arguments.
    pub node_text: unsafe extern "C" fn(*const c_void, u32, *mut u8, usize, *mut usize) -> i32,
    /// Read one property as JSON. Missing and explicit JSON null differ.
    pub node_property:
        unsafe extern "C" fn(*const c_void, *const u8, usize, *mut u8, usize) -> isize,
    /// Section IDs are `SECTION_*`; each result is a single node's JSON map.
    pub node_section: unsafe extern "C" fn(*const c_void, u32, *mut u8, usize) -> isize,
    pub node_metadata_field:
        unsafe extern "C" fn(*const c_void, u32, *const u8, usize, *mut u8, usize) -> isize,
}

/// Version 2 projects normalized engine values lazily. The visitor passes a
/// borrowed node handle; clone_node is required to retain it after callback.
pub type VisitProjectedNode = unsafe extern "C" fn(*mut c_void, *const c_void) -> i32;

#[repr(C)]
pub struct RustProjectedWorldViewApi {
    pub version: u32,
    pub clone_view: unsafe extern "C" fn(*const c_void) -> *mut c_void,
    pub drop_view: unsafe extern "C" fn(*mut c_void),
    pub sequence: unsafe extern "C" fn(*const c_void) -> u64,
    pub node_count: unsafe extern "C" fn(*const c_void) -> usize,
    pub visit_nodes: unsafe extern "C" fn(*const c_void, VisitProjectedNode, *mut c_void) -> i32,
    pub lookup_node: unsafe extern "C" fn(*const c_void, *const u8, usize) -> *mut c_void,
    pub clone_node: unsafe extern "C" fn(*const c_void) -> *mut c_void,
    pub drop_node: unsafe extern "C" fn(*mut c_void),
    /// JSON `{id,class_name,name,parent,created_sequence?}`; missing header
    /// fields are omitted and explicit null name/parent remain null.
    pub node_header: unsafe extern "C" fn(*const c_void, *mut u8, usize) -> isize,
    pub node_text: unsafe extern "C" fn(*const c_void, u32, *mut u8, usize, *mut usize) -> i32,
    pub node_property:
        unsafe extern "C" fn(*const c_void, *const u8, usize, *mut u8, usize) -> isize,
    pub node_section: unsafe extern "C" fn(*const c_void, u32, *mut u8, usize) -> isize,
    pub node_metadata_field:
        unsafe extern "C" fn(*const c_void, u32, *const u8, usize, *mut u8, usize) -> isize,
    /// Object entry count, -1 missing, -2 null, -3 invalid.
    pub node_section_size: unsafe extern "C" fn(*const c_void, u32) -> isize,
}

/// Public v2 name; v1 remains available for raw, unnormalized fixtures.
pub type RustWorldViewApiV2 = RustProjectedWorldViewApi;

/// Cheap Rust-side owner used while a view is queued or passed to the native
/// VM. `as_opaque` is valid while this owner is alive; a native consumer calls
/// `clone_view` to acquire its own reference before retaining the pointer.
#[derive(Clone)]
pub struct SharedWorldView(Arc<WorldView>);

impl SharedWorldView {
    pub fn new(view: WorldView) -> Self {
        Self(Arc::new(view))
    }

    pub fn as_opaque(&self) -> *const c_void {
        Arc::as_ptr(&self.0).cast()
    }

    /// Bind immutable schema/service projection context while continuing to
    /// share the canonical world and per-node records.
    pub fn projected(view: WorldView, context: Arc<ProjectionContext>) -> SharedProjectedWorldView {
        SharedProjectedWorldView(Arc::new(ProjectedWorldView::new(view, context)))
    }
}

#[derive(Clone)]
pub struct SharedProjectedWorldView(Arc<ProjectedWorldView>);

impl SharedProjectedWorldView {
    pub fn as_opaque(&self) -> *const c_void {
        Arc::as_ptr(&self.0).cast()
    }
    pub fn sequence(&self) -> u64 {
        self.0.sequence()
    }
    pub fn node_count(&self) -> usize {
        self.0.node_count()
    }
    pub fn lookup_node(&self, id: &str) -> Option<ProjectedNode> {
        self.0.lookup_node(id)
    }
    pub fn for_each_node(&self, visit: impl FnMut(ProjectedNode)) {
        self.0.for_each_node(visit)
    }
}

pub static RUST_WORLD_VIEW_API: RustWorldViewApi = RustWorldViewApi {
    version: API_VERSION,
    clone_view: clone_view,
    drop_view: drop_view,
    sequence: view_sequence,
    node_count: view_node_count,
    lookup_node: lookup_node,
    clone_node: clone_node,
    drop_node: drop_node,
    node_text: node_text,
    node_property: node_property,
    node_section: node_section,
    node_metadata_field: node_metadata_field,
};

pub static RUST_PROJECTED_WORLD_VIEW_API: RustProjectedWorldViewApi = RustProjectedWorldViewApi {
    version: 2,
    clone_view: projected_clone_view,
    drop_view: projected_drop_view,
    sequence: projected_sequence,
    node_count: projected_node_count,
    visit_nodes: projected_visit_nodes,
    lookup_node: projected_lookup_node,
    clone_node: projected_clone_node,
    drop_node: projected_drop_node,
    node_header: projected_node_header,
    node_text: projected_node_text,
    node_property: projected_node_property,
    node_section: projected_node_section,
    node_metadata_field: projected_node_metadata_field,
    node_section_size: projected_node_section_size,
};

pub use RUST_PROJECTED_WORLD_VIEW_API as RUST_WORLD_VIEW_API_V2;

unsafe extern "C" fn projected_clone_view(view: *const c_void) -> *mut c_void {
    if view.is_null() {
        return ptr::null_mut();
    }
    catch_unwind(AssertUnwindSafe(|| {
        let raw = view.cast::<ProjectedWorldView>();
        unsafe { Arc::increment_strong_count(raw) };
        raw.cast_mut().cast()
    }))
    .unwrap_or(ptr::null_mut())
}

unsafe extern "C" fn projected_drop_view(view: *mut c_void) {
    if !view.is_null() {
        let _ = catch_unwind(AssertUnwindSafe(|| unsafe {
            Arc::decrement_strong_count(view.cast::<ProjectedWorldView>())
        }));
    }
}

unsafe extern "C" fn projected_sequence(view: *const c_void) -> u64 {
    if view.is_null() {
        return 0;
    }
    catch_unwind(AssertUnwindSafe(|| {
        unsafe { &*view.cast::<ProjectedWorldView>() }.sequence()
    }))
    .unwrap_or(0)
}

unsafe extern "C" fn projected_node_count(view: *const c_void) -> usize {
    if view.is_null() {
        return 0;
    }
    catch_unwind(AssertUnwindSafe(|| {
        unsafe { &*view.cast::<ProjectedWorldView>() }.node_count()
    }))
    .unwrap_or(0)
}

unsafe extern "C" fn projected_visit_nodes(
    view: *const c_void,
    callback: VisitProjectedNode,
    userdata: *mut c_void,
) -> i32 {
    if view.is_null() {
        return -1;
    }
    let mut status = 0;
    let result = catch_unwind(AssertUnwindSafe(|| {
        unsafe { &*view.cast::<ProjectedWorldView>() }.for_each_node(|node| {
            if status != 0 {
                return;
            }
            let handle = Box::into_raw(Box::new(node)).cast::<c_void>();
            let code = unsafe { callback(userdata, handle.cast_const()) };
            unsafe { drop(Box::from_raw(handle.cast::<ProjectedNode>())) };
            if code != 0 {
                status = code;
            }
        });
    }));
    if result.is_err() {
        -1
    } else {
        status
    }
}

unsafe extern "C" fn projected_lookup_node(
    view: *const c_void,
    id: *const u8,
    len: usize,
) -> *mut c_void {
    if view.is_null() || (id.is_null() && len != 0) {
        return ptr::null_mut();
    }
    catch_unwind(AssertUnwindSafe(|| {
        let Some(bytes) = (unsafe { input_bytes(id, len) }) else {
            return ptr::null_mut();
        };
        let Ok(id) = std::str::from_utf8(bytes) else {
            return ptr::null_mut();
        };
        unsafe { &*view.cast::<ProjectedWorldView>() }
            .lookup_node(id)
            .map(|node| Box::into_raw(Box::new(node)).cast())
            .unwrap_or(ptr::null_mut())
    }))
    .unwrap_or(ptr::null_mut())
}

unsafe extern "C" fn projected_clone_node(node: *const c_void) -> *mut c_void {
    if node.is_null() {
        return ptr::null_mut();
    }
    catch_unwind(AssertUnwindSafe(|| {
        Box::into_raw(Box::new(unsafe { &*node.cast::<ProjectedNode>() }.clone())).cast()
    }))
    .unwrap_or(ptr::null_mut())
}

unsafe extern "C" fn projected_drop_node(node: *mut c_void) {
    if !node.is_null() {
        let _ = catch_unwind(AssertUnwindSafe(|| unsafe {
            drop(Box::from_raw(node.cast::<ProjectedNode>()))
        }));
    }
}

unsafe extern "C" fn projected_node_header(
    node: *const c_void,
    out: *mut u8,
    capacity: usize,
) -> isize {
    if node.is_null() || (out.is_null() && capacity != 0) {
        return -3;
    }
    catch_unwind(AssertUnwindSafe(|| {
        let node = unsafe { &*node.cast::<ProjectedNode>() };
        let mut header = Map::new();
        header.insert("id".into(), Value::String(node.id().into()));
        header.insert("class_name".into(), Value::String(node.class_name().into()));
        match node.name() {
            Ok(name) => {
                header.insert("name".into(), Value::String(name));
            }
            Err(_) => return -3,
        }
        match node.parent() {
            ReadOnlyField::Missing => {}
            ReadOnlyField::Null => {
                header.insert("parent".into(), Value::Null);
            }
            ReadOnlyField::Value(parent) => {
                header.insert("parent".into(), Value::String(parent));
            }
        }
        if let Some(sequence) = node.raw().created_sequence() {
            header.insert("created_sequence".into(), Value::from(sequence));
        }
        for key in [
            "attributes_known",
            "destroyed",
            "collection_tags",
            "collection_tags_known",
        ] {
            if let Some(value) = node.raw().record_field(key) {
                header.insert(key.into(), value);
            }
        }
        header.insert("server_owned".into(), Value::Bool(true));
        header.insert("constructor_defaults_known".into(), Value::Bool(false));
        write_json(&Value::Object(header), out, capacity)
    }))
    .unwrap_or(-3)
}

unsafe extern "C" fn projected_node_text(
    node: *const c_void,
    field: u32,
    out: *mut u8,
    capacity: usize,
    out_len: *mut usize,
) -> i32 {
    if node.is_null() || out_len.is_null() || (out.is_null() && capacity != 0) {
        return -1;
    }
    catch_unwind(AssertUnwindSafe(|| {
        let node = unsafe { &*node.cast::<ProjectedNode>() };
        let text = match field {
            0 => Some(node.id().to_owned()),
            1 => Some(node.class_name().to_owned()),
            2 => match node.name() {
                Ok(v) => Some(v),
                Err(_) => return -1,
            },
            3 => match node.parent() {
                ReadOnlyField::Missing => return 0,
                ReadOnlyField::Null => return 1,
                ReadOnlyField::Value(v) => Some(v),
            },
            _ => return -1,
        };
        let bytes = text.unwrap().into_bytes();
        unsafe {
            *out_len = bytes.len();
        }
        if out.is_null() {
            return 2;
        }
        if capacity < bytes.len() {
            return -1;
        }
        if !bytes.is_empty() {
            unsafe { ptr::copy_nonoverlapping(bytes.as_ptr(), out, bytes.len()) };
        }
        2
    }))
    .unwrap_or(-1)
}

unsafe extern "C" fn projected_node_property(
    node: *const c_void,
    key: *const u8,
    len: usize,
    out: *mut u8,
    capacity: usize,
) -> isize {
    if node.is_null() || (key.is_null() && len != 0) || (out.is_null() && capacity != 0) {
        return -3;
    }
    catch_unwind(AssertUnwindSafe(|| {
        let Some(bytes) = (unsafe { input_bytes(key, len) }) else {
            return -3;
        };
        let Ok(key) = std::str::from_utf8(bytes) else {
            return -3;
        };
        match unsafe { &*node.cast::<ProjectedNode>() }.property(key) {
            Ok(Some(value)) => write_json(&value, out, capacity),
            Ok(None) => -1,
            Err(_) => -3,
        }
    }))
    .unwrap_or(-3)
}

unsafe extern "C" fn projected_node_section(
    node: *const c_void,
    section: u32,
    out: *mut u8,
    capacity: usize,
) -> isize {
    if node.is_null() || (out.is_null() && capacity != 0) {
        return -3;
    }
    catch_unwind(AssertUnwindSafe(|| {
        let section = match section {
            SECTION_PROPERTIES => "properties",
            SECTION_ORIGINS => "origins",
            SECTION_ATTRIBUTES => "attributes",
            SECTION_PROPERTY_REVISIONS => "property_revisions",
            SECTION_COLLECTION_TAGS => "collection_tags",
            _ => return -3,
        };
        match unsafe { &*node.cast::<ProjectedNode>() }.section(section) {
            Ok(ReadOnlyField::Missing) => -1,
            Ok(ReadOnlyField::Null) => -2,
            Ok(ReadOnlyField::Value(value)) => write_json(&value, out, capacity),
            Err(_) => -3,
        }
    }))
    .unwrap_or(-3)
}

unsafe extern "C" fn projected_node_metadata_field(
    node: *const c_void,
    section: u32,
    key: *const u8,
    len: usize,
    out: *mut u8,
    capacity: usize,
) -> isize {
    if node.is_null() || (key.is_null() && len != 0) || (out.is_null() && capacity != 0) {
        return -3;
    }
    catch_unwind(AssertUnwindSafe(|| {
        let Some(bytes) = (unsafe { input_bytes(key, len) }) else {
            return -3;
        };
        let Ok(key) = std::str::from_utf8(bytes) else {
            return -3;
        };
        let node = unsafe { &*node.cast::<ProjectedNode>() };
        let value = match section {
            SECTION_ORIGINS => node.origin(key),
            SECTION_PROPERTY_REVISIONS => node.revision(key),
            SECTION_ATTRIBUTES => {
                return match node.raw().metadata_field("attributes", key) {
                    Some(v) => write_json(&v, out, capacity),
                    None => -1,
                }
            }
            _ => return -3,
        };
        match value {
            Ok(Some(value)) => write_json(&value, out, capacity),
            Ok(None) => -1,
            Err(_) => -3,
        }
    }))
    .unwrap_or(-3)
}

unsafe extern "C" fn projected_node_section_size(node: *const c_void, section: u32) -> isize {
    if node.is_null() {
        return -3;
    }
    catch_unwind(AssertUnwindSafe(|| {
        let section = match section {
            SECTION_PROPERTIES => "properties",
            SECTION_ORIGINS => "origins",
            SECTION_ATTRIBUTES => "attributes",
            SECTION_PROPERTY_REVISIONS => "property_revisions",
            SECTION_COLLECTION_TAGS => "collection_tags",
            _ => return -3,
        };
        let node = unsafe { &*node.cast::<ProjectedNode>() };
        if section == "properties" {
            return match node.raw().properties_len() {
                ReadOnlyField::Missing => -1,
                ReadOnlyField::Null => -2,
                ReadOnlyField::Value(_) => node
                    .section_len(section)
                    .ok()
                    .flatten()
                    .and_then(|n| isize::try_from(n).ok())
                    .unwrap_or(-3),
            };
        }
        match node.section(section) {
            Ok(ReadOnlyField::Missing) => -1,
            Ok(ReadOnlyField::Null) => -2,
            Ok(ReadOnlyField::Value(Value::Object(map))) => {
                isize::try_from(map.len()).unwrap_or(-3)
            }
            Ok(ReadOnlyField::Value(Value::Array(items))) => {
                isize::try_from(items.len()).unwrap_or(-3)
            }
            Ok(ReadOnlyField::Value(_)) => -3,
            Err(_) => -3,
        }
    }))
    .unwrap_or(-3)
}

unsafe extern "C" fn clone_view(view: *const c_void) -> *mut c_void {
    if view.is_null() {
        return ptr::null_mut();
    }
    catch_unwind(AssertUnwindSafe(|| {
        let ptr = view.cast::<WorldView>();
        // SAFETY: `SharedWorldView::as_opaque` yields this Arc allocation, and
        // the caller must keep its owner alive for the duration of this call.
        unsafe { Arc::increment_strong_count(ptr) };
        ptr.cast_mut().cast()
    }))
    .unwrap_or(ptr::null_mut())
}

unsafe extern "C" fn drop_view(view: *mut c_void) {
    if !view.is_null() {
        let _ = catch_unwind(AssertUnwindSafe(|| {
            // SAFETY: each native owned reference is paired with one clone_view.
            unsafe { Arc::decrement_strong_count(view.cast::<WorldView>()) };
        }));
    }
}

unsafe extern "C" fn view_sequence(view: *const c_void) -> u64 {
    if view.is_null() {
        return 0;
    }
    catch_unwind(AssertUnwindSafe(|| {
        // SAFETY: caller provides a live pointer owned by SharedWorldView.
        unsafe { &*view.cast::<WorldView>() }.sequence()
    }))
    .unwrap_or(0)
}

unsafe extern "C" fn view_node_count(view: *const c_void) -> usize {
    if view.is_null() {
        return 0;
    }
    catch_unwind(AssertUnwindSafe(|| {
        // SAFETY: caller provides a live pointer owned by SharedWorldView.
        unsafe { &*view.cast::<WorldView>() }.node_count()
    }))
    .unwrap_or(0)
}

unsafe extern "C" fn lookup_node(view: *const c_void, id: *const u8, id_len: usize) -> *mut c_void {
    if view.is_null() || (id.is_null() && id_len != 0) {
        return ptr::null_mut();
    }
    catch_unwind(AssertUnwindSafe(|| {
        let id = match unsafe { input_bytes(id, id_len) } {
            Some(bytes) => match std::str::from_utf8(bytes) {
                Ok(id) => id,
                Err(_) => return ptr::null_mut(),
            },
            None => return ptr::null_mut(),
        };
        // SAFETY: caller provides a live pointer owned by SharedWorldView.
        let view = unsafe { &*view.cast::<WorldView>() };
        view.read_only_node(id)
            .map(|node| Box::into_raw(Box::new(node)).cast())
            .unwrap_or(ptr::null_mut())
    }))
    .unwrap_or(ptr::null_mut())
}

unsafe extern "C" fn clone_node(node: *const c_void) -> *mut c_void {
    if node.is_null() {
        return ptr::null_mut();
    }
    catch_unwind(AssertUnwindSafe(|| {
        // SAFETY: caller provides a live pointer returned by lookup_node.
        let node = unsafe { &*node.cast::<ReadOnlyNode>() };
        Box::into_raw(Box::new(node.clone())).cast()
    }))
    .unwrap_or(ptr::null_mut())
}

unsafe extern "C" fn drop_node(node: *mut c_void) {
    if !node.is_null() {
        let _ = catch_unwind(AssertUnwindSafe(|| {
            // SAFETY: each node pointer is a Box returned by lookup_node or clone_node.
            drop(unsafe { Box::from_raw(node.cast::<ReadOnlyNode>()) });
        }));
    }
}

unsafe extern "C" fn node_text(
    node: *const c_void,
    field: u32,
    out: *mut u8,
    capacity: usize,
    out_len: *mut usize,
) -> i32 {
    if node.is_null() || out_len.is_null() || (out.is_null() && capacity != 0) {
        return -1;
    }
    catch_unwind(AssertUnwindSafe(|| {
        // SAFETY: caller provides a live pointer returned by lookup_node.
        let node = unsafe { &*node.cast::<ReadOnlyNode>() };
        let text = match field {
            0 => Some(node.id()),
            1 => Some(node.class_name()),
            2 => match node.name() {
                ReadOnlyField::Missing => return 0,
                ReadOnlyField::Null => return 1,
                ReadOnlyField::Value(value) => Some(value),
            },
            3 => match node.parent() {
                ReadOnlyField::Missing => return 0,
                ReadOnlyField::Null => return 1,
                ReadOnlyField::Value(value) => Some(value),
            },
            _ => return -1,
        };
        let text = text.unwrap().as_bytes();
        unsafe { *out_len = text.len() };
        if out.is_null() {
            return 2;
        }
        if capacity < text.len() {
            return -1;
        }
        if !text.is_empty() {
            // SAFETY: validated output pointer and capacity above.
            unsafe { ptr::copy_nonoverlapping(text.as_ptr(), out, text.len()) };
        }
        2
    }))
    .unwrap_or(-1)
}

unsafe extern "C" fn node_property(
    node: *const c_void,
    key: *const u8,
    key_len: usize,
    out: *mut u8,
    capacity: usize,
) -> isize {
    if node.is_null() || (key.is_null() && key_len != 0) || (out.is_null() && capacity != 0) {
        return -3;
    }
    catch_unwind(AssertUnwindSafe(|| {
        let key = match unsafe { input_bytes(key, key_len) }
            .and_then(|bytes| std::str::from_utf8(bytes).ok())
        {
            Some(key) => key,
            None => return -3,
        };
        // SAFETY: caller provides a live pointer returned by lookup_node.
        let node = unsafe { &*node.cast::<ReadOnlyNode>() };
        let Some(value) = node.property(key) else {
            return -1;
        };
        write_json(&value, out, capacity)
    }))
    .unwrap_or(-3)
}

unsafe extern "C" fn node_section(
    node: *const c_void,
    section: u32,
    out: *mut u8,
    capacity: usize,
) -> isize {
    if node.is_null() || (out.is_null() && capacity != 0) {
        return -3;
    }
    catch_unwind(AssertUnwindSafe(|| {
        // SAFETY: caller provides a live pointer returned by lookup_node.
        let node = unsafe { &*node.cast::<ReadOnlyNode>() };
        match section {
            SECTION_PROPERTIES => match node.properties() {
                ReadOnlyField::Missing => -1,
                ReadOnlyField::Null => -2,
                ReadOnlyField::Value(map) => write_json(&Value::Object(map), out, capacity),
            },
            SECTION_ORIGINS => metadata_section(node, "origins", out, capacity),
            SECTION_ATTRIBUTES => metadata_section(node, "attributes", out, capacity),
            SECTION_PROPERTY_REVISIONS => {
                metadata_section(node, "property_revisions", out, capacity)
            }
            _ => -3,
        }
    }))
    .unwrap_or(-3)
}

unsafe extern "C" fn node_metadata_field(
    node: *const c_void,
    section: u32,
    key: *const u8,
    key_len: usize,
    out: *mut u8,
    capacity: usize,
) -> isize {
    if node.is_null() || (key.is_null() && key_len != 0) || (out.is_null() && capacity != 0) {
        return -3;
    }
    catch_unwind(AssertUnwindSafe(|| {
        let key = match unsafe { input_bytes(key, key_len) }
            .and_then(|bytes| std::str::from_utf8(bytes).ok())
        {
            Some(key) => key,
            None => return -3,
        };
        let section = match section {
            SECTION_ORIGINS => "origins",
            SECTION_ATTRIBUTES => "attributes",
            SECTION_PROPERTY_REVISIONS => "property_revisions",
            _ => return -3,
        };
        // SAFETY: caller provides a live pointer returned by lookup_node.
        let node = unsafe { &*node.cast::<ReadOnlyNode>() };
        let Some(value) = node.metadata_field(section, key) else {
            return -1;
        };
        write_json(&value, out, capacity)
    }))
    .unwrap_or(-3)
}

fn metadata_section(node: &ReadOnlyNode, section: &str, out: *mut u8, capacity: usize) -> isize {
    match node.metadata_section(section) {
        ReadOnlyField::Missing => -1,
        ReadOnlyField::Null => -2,
        ReadOnlyField::Value(value) => write_json(&value, out, capacity),
    }
}

fn write_json(value: &Value, out: *mut u8, capacity: usize) -> isize {
    let Ok(bytes) = serde_json::to_vec(value) else {
        return -3;
    };
    write_bytes(&bytes, out, capacity)
}

fn write_bytes(bytes: &[u8], out: *mut u8, capacity: usize) -> isize {
    let Ok(length) = isize::try_from(bytes.len()) else {
        return -3;
    };
    if out.is_null() {
        return length;
    }
    if capacity < bytes.len() {
        return -3;
    }
    if !bytes.is_empty() {
        // SAFETY: caller supplied a writable buffer of at least this capacity.
        unsafe { ptr::copy_nonoverlapping(bytes.as_ptr(), out, bytes.len()) };
    }
    length
}

unsafe fn input_bytes<'a>(input: *const u8, length: usize) -> Option<&'a [u8]> {
    if length == 0 {
        Some(&[])
    } else if input.is_null() {
        None
    } else {
        // SAFETY: caller guarantees a readable range for the duration of the ABI call.
        Some(unsafe { slice::from_raw_parts(input, length) })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rove_client_core::world::WorldStore;

    fn fixture() -> WorldStore {
        WorldStore::from_snapshot(
            &serde_json::json!({"version":1,"sequence":2,"services":{},"nodes":[
                {"id":"p","class_name":"Part","name":null,"parent":null,
                 "properties":{"Anchored":false,"ExplicitNull":null},
                 "origins":{"Anchored":"received"},"attributes":{"stage":null},
                 "property_revisions":{"Anchored":5}},
                {"id":"f","class_name":"Folder","properties":{}}
            ]}),
        )
        .unwrap()
    }

    #[test]
    fn abi_node_records_survive_source_world_updates_and_drop() {
        let mut store = fixture();
        let view = SharedWorldView::new(store.view());
        let api = &RUST_WORLD_VIEW_API;
        let raw_view = view.as_opaque();
        let owned_view = unsafe { (api.clone_view)(raw_view) };
        assert!(!owned_view.is_null());
        assert_eq!(unsafe { (api.sequence)(owned_view) }, 2);
        let node = unsafe { (api.lookup_node)(owned_view, b"p".as_ptr(), 1) };
        assert!(!node.is_null());
        unsafe { (api.drop_view)(owned_view) };

        let mut next = store.sequence() + 1;
        store
            .apply_delta(&serde_json::json!({"version":1,"delta":true,
            "sequence":next,"actions":[{"sequence":next,"kind":"set","id":"p",
            "fields":{"Anchored":true}}]}))
            .unwrap();
        next += 1;
        store
            .apply_delta(&serde_json::json!({"version":1,"delta":true,
            "sequence":next,"actions":[{"sequence":next,"kind":"remove","id":"f"}]}))
            .unwrap();
        drop(store);
        drop(view);

        let mut buf = [0u8; 16];
        assert_eq!(
            unsafe { (api.node_property)(node, b"Anchored".as_ptr(), 8, ptr::null_mut(), 0) },
            5
        );
        assert_eq!(
            unsafe {
                (api.node_property)(node, b"Anchored".as_ptr(), 8, buf.as_mut_ptr(), buf.len())
            },
            5
        );
        assert_eq!(&buf[..5], b"false");
        assert_eq!(
            unsafe { (api.node_property)(node, b"ExplicitNull".as_ptr(), 12, ptr::null_mut(), 0) },
            4
        );
        assert_eq!(
            unsafe { (api.node_property)(node, b"absent".as_ptr(), 6, ptr::null_mut(), 0) },
            -1
        );
        assert_eq!(
            unsafe {
                (api.node_metadata_field)(
                    node,
                    SECTION_ATTRIBUTES,
                    b"stage".as_ptr(),
                    5,
                    ptr::null_mut(),
                    0,
                )
            },
            4
        );
        assert_eq!(
            unsafe {
                (api.node_metadata_field)(
                    node,
                    SECTION_ATTRIBUTES,
                    b"missing".as_ptr(),
                    7,
                    ptr::null_mut(),
                    0,
                )
            },
            -1
        );

        let mut text_len = 0;
        assert_eq!(
            unsafe { (api.node_text)(node, 2, ptr::null_mut(), 0, &mut text_len) },
            1
        );
        assert_eq!(
            unsafe { (api.node_text)(node, 3, ptr::null_mut(), 0, &mut text_len) },
            1
        );
        unsafe { (api.drop_node)(node) };
    }

    #[test]
    fn abi_view_lookup_and_node_handles_share_only_the_record() {
        let store = fixture();
        let view = SharedWorldView::new(store.view());
        let raw = view.as_opaque();
        let api = &RUST_WORLD_VIEW_API;
        let first = unsafe { (api.lookup_node)(raw, b"p".as_ptr(), 1) };
        let second = unsafe { (api.clone_node)(first) };
        assert!(!first.is_null() && !second.is_null());
        unsafe {
            (api.drop_node)(first);
            (api.drop_node)(second);
        }
        let missing = view.0.read_only_node("missing");
        assert!(missing.is_none());
        let state = view.0.read_only_node("f").unwrap().properties();
        assert_eq!(state, ReadOnlyField::Value(Map::new()));
        assert_eq!(
            unsafe { (api.lookup_node)(raw, b"missing".as_ptr(), 7) },
            ptr::null_mut()
        );
    }
}
