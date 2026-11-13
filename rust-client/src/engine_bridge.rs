//! hook up the rust engine operations before starting the official luau vm.
//! c++ handles the lua stack and errors. callbacks must not call lua or unwind.
use crate::engine_attributes::RustAttributeStoreApi;
use crate::engine_classes::*;
use crate::engine_datatypes::*;
use crate::engine_world_view::RustProjectedWorldViewApi;
use std::{ffi::c_void, sync::Once};

#[repr(C)]
struct EngineApi {
    version: u32,
    narrow: extern "C" fn(f64) -> i32,
    vector_construct: unsafe extern "C" fn(i32, *const f64, *mut Int16Vector) -> i32,
    vector_binary:
        unsafe extern "C" fn(i32, *const Int16Vector, *const Int16Vector, *mut Int16Vector) -> i32,
    vector_scalar: unsafe extern "C" fn(i32, *const Int16Vector, f64, u8, *mut Int16Vector) -> i32,
    region_construct:
        unsafe extern "C" fn(*const Int16Vector, *const Int16Vector, *mut Region3int16) -> i32,
    region_equal: unsafe extern "C" fn(*const Region3int16, *const Region3int16) -> i32,
    class_graph_new: unsafe extern "C" fn(*const u8, usize) -> *mut c_void,
    class_graph_is_a: unsafe extern "C" fn(*mut c_void, *const u8, usize, *const u8, usize) -> i32,
    class_graph_free: unsafe extern "C" fn(*mut c_void),
}
static ENGINE_API: EngineApi = EngineApi {
    version: 1,
    narrow: rove_integer_narrow,
    vector_construct: rove_integer_vector_construct,
    vector_binary: rove_integer_vector_binary,
    vector_scalar: rove_integer_vector_scalar,
    region_construct: rove_region3int16_construct,
    region_equal: rove_region3int16_equal,
    class_graph_new: local_rust_class_graph_new,
    class_graph_is_a: local_rust_class_graph_is_a,
    class_graph_free: local_rust_class_graph_free,
};
extern "C" {
    fn local_luau_set_rust_attribute_store_api(api: *const RustAttributeStoreApi) -> i32;
    fn local_luau_set_rust_projected_world_view_api(api: *const RustProjectedWorldViewApi) -> i32;
}
extern "C" {
    fn local_luau_set_rust_engine_api(api: *const EngineApi) -> i32;
}
pub fn install() {
    static INIT: Once = Once::new();
    INIT.call_once(|| {
        crate::engine_datetime::install();
        crate::engine_vector2::install();
        crate::engine_color3::install();
        crate::engine_physical_properties::install();
        crate::engine_text_measure::install();
        assert_eq!(
            unsafe {
                local_luau_set_rust_projected_world_view_api(
                    &crate::engine_world_view::RUST_PROJECTED_WORLD_VIEW_API,
                )
            },
            1,
            "native projected world ABI registration failed"
        );
        assert_eq!(
            unsafe {
                local_luau_set_rust_attribute_store_api(
                    &crate::engine_attributes::RUST_ATTRIBUTE_STORE_API,
                )
            },
            1,
            "native attribute store ABI registration failed"
        );
        // SAFETY: immutable static callbacks outlive every native VM/World.
        assert_eq!(
            unsafe { local_luau_set_rust_engine_api(&ENGINE_API) },
            1,
            "native engine ABI registration failed"
        );
    });
}
