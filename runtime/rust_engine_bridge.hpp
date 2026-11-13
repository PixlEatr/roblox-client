#pragma once
#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>

// This ABI contains values and opaque handles only. Every callback returns
// before the thin C++ Luau wrapper can raise a Lua error.
struct RustIntegerVector { int32_t dimension; int32_t values[3]; };
struct RustIntegerRegion { RustIntegerVector minimum, maximum; };
struct RustAttributeStoreApi {
    uint32_t version;
    void* (*create_empty)(uint8_t);
    void* (*create_json)(const uint8_t*, size_t, uint8_t);
    void* (*clone_store)(const void*);
    void (*drop_store)(void*);
    int32_t (*known)(const void*);
    int32_t (*set_known)(void*, uint8_t);
    ptrdiff_t (*size)(const void*);
    int32_t (*contains)(const void*, const uint8_t*, size_t);
    ptrdiff_t (*lookup)(const void*, const uint8_t*, size_t, uint8_t*, size_t);
    ptrdiff_t (*snapshot)(const void*, uint8_t*, size_t);
    int32_t (*replace)(void*, const uint8_t*, size_t, uint8_t);
    int32_t (*set)(void*, const uint8_t*, size_t, const uint8_t*, size_t, uint8_t*);
    int32_t (*erase)(void*, const uint8_t*, size_t, uint8_t*);
};
using RustProjectedNodeVisitor = int32_t (*)(void*, const void*);
constexpr uint32_t kRustWorldSectionProperties = 0;
constexpr uint32_t kRustWorldSectionOrigins = 1;
constexpr uint32_t kRustWorldSectionAttributes = 2;
constexpr uint32_t kRustWorldSectionPropertyRevisions = 3;
constexpr uint32_t kRustWorldSectionCollectionTags = 4;
struct RustProjectedWorldViewApi {
    uint32_t version;
    void* (*clone_view)(const void*);
    void (*drop_view)(void*);
    uint64_t (*sequence)(const void*);
    size_t (*node_count)(const void*);
    int32_t (*visit_nodes)(const void*, RustProjectedNodeVisitor, void*);
    void* (*lookup_node)(const void*, const uint8_t*, size_t);
    void* (*clone_node)(const void*);
    void (*drop_node)(void*);
    ptrdiff_t (*node_header)(const void*, uint8_t*, size_t);
    int32_t (*node_text)(const void*, uint32_t, uint8_t*, size_t, size_t*);
    ptrdiff_t (*node_property)(const void*, const uint8_t*, size_t, uint8_t*, size_t);
    ptrdiff_t (*node_section)(const void*, uint32_t, uint8_t*, size_t);
    ptrdiff_t (*node_metadata_field)(const void*, uint32_t, const uint8_t*, size_t, uint8_t*, size_t);
    ptrdiff_t (*node_section_size)(const void*, uint32_t);
};
struct RustEngineApi {
    uint32_t version;
    int32_t (*narrow)(double);
    int32_t (*vector_construct)(int32_t, const double*, RustIntegerVector*);
    int32_t (*vector_binary)(int32_t, const RustIntegerVector*, const RustIntegerVector*, RustIntegerVector*);
    int32_t (*vector_scalar)(int32_t, const RustIntegerVector*, double, uint8_t, RustIntegerVector*);
    int32_t (*region_construct)(const RustIntegerVector*, const RustIntegerVector*, RustIntegerRegion*);
    int32_t (*region_equal)(const RustIntegerRegion*, const RustIntegerRegion*);
    void* (*class_graph_new)(const uint8_t*, size_t);
    int32_t (*class_graph_is_a)(void*, const uint8_t*, size_t, const uint8_t*, size_t);
    void (*class_graph_free)(void*);
};

// Registered once by the Rust host before creating any VM. The C++ standalone
// executable and C++ fixtures keep their native fallback without a Rust linker.
extern "C" int local_luau_set_rust_engine_api(const RustEngineApi* api);
const RustEngineApi* rust_engine_api();
extern "C" int local_luau_set_rust_attribute_store_api(const RustAttributeStoreApi* api);
const RustAttributeStoreApi* rust_attribute_store_api();
extern "C" int local_luau_set_rust_projected_world_view_api(const RustProjectedWorldViewApi* api);
const RustProjectedWorldViewApi* rust_projected_world_view_api();

// Synchronous local font measurement for non-yielding GUI layout/property
// reads. The callback never enters Lua; errors are copied as UTF-8 text.
struct RustTextMeasureApi {
    uint32_t version;
    int32_t (*bounds)(const uint8_t*, size_t, double*, uint8_t*, size_t);
};
extern "C" int local_luau_set_rust_text_measure_api(const RustTextMeasureApi* api);
const RustTextMeasureApi* rust_text_measure_api();
