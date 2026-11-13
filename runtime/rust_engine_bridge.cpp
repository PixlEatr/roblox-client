#include "rust_engine_bridge.hpp"
#include <atomic>

namespace { std::atomic<const RustEngineApi*> active{nullptr}; }
namespace { std::atomic<const RustAttributeStoreApi*> active_attributes{nullptr}; }
namespace { std::atomic<const RustProjectedWorldViewApi*> active_projected_views{nullptr}; }
namespace { std::atomic<const RustTextMeasureApi*> active_text_measure{nullptr}; }
extern "C" int local_luau_set_rust_text_measure_api(const RustTextMeasureApi* api) {
    if (!api || api->version != 1 || !api->bounds) return 0;
    const RustTextMeasureApi* expected = nullptr;
    return active_text_measure.compare_exchange_strong(expected, api) || expected == api;
}
const RustTextMeasureApi* rust_text_measure_api() { return active_text_measure.load(); }
extern "C" int local_luau_set_rust_engine_api(const RustEngineApi* api) {
    if (!api || api->version != 1 || !api->narrow || !api->vector_construct ||
        !api->vector_binary || !api->vector_scalar || !api->region_construct ||
        !api->region_equal || !api->class_graph_new || !api->class_graph_is_a ||
        !api->class_graph_free) return 0;
    const RustEngineApi* expected = nullptr;
    return active.compare_exchange_strong(expected, api) || expected == api;
}
const RustEngineApi* rust_engine_api() { return active.load(); }
extern "C" int local_luau_set_rust_attribute_store_api(const RustAttributeStoreApi* api) {
    if (!api || api->version != 1 || !api->create_empty || !api->create_json || !api->clone_store ||
        !api->drop_store || !api->known || !api->set_known || !api->size || !api->contains ||
        !api->lookup || !api->snapshot || !api->replace || !api->set || !api->erase) return 0;
    const RustAttributeStoreApi* expected = nullptr;
    return active_attributes.compare_exchange_strong(expected, api) || expected == api;
}
const RustAttributeStoreApi* rust_attribute_store_api() { return active_attributes.load(); }
extern "C" int local_luau_set_rust_projected_world_view_api(const RustProjectedWorldViewApi* api) {
    if (!api || api->version != 2 || !api->clone_view || !api->drop_view || !api->sequence ||
        !api->node_count || !api->visit_nodes || !api->lookup_node || !api->clone_node ||
        !api->drop_node || !api->node_header || !api->node_text || !api->node_property ||
        !api->node_section || !api->node_metadata_field || !api->node_section_size) return 0;
    const RustProjectedWorldViewApi* expected = nullptr;
    return active_projected_views.compare_exchange_strong(expected, api) || expected == api;
}
const RustProjectedWorldViewApi* rust_projected_world_view_api() { return active_projected_views.load(); }
