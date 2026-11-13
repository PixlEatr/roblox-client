#include "rust_world_view.hpp"
#include "received_collision_group.hpp"

#include <cstring>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <type_traits>

static_assert(!std::is_move_constructible_v<RustNodeProviderBundle>,
              "provider adapters require the bundle node address to remain stable");
static_assert(!std::is_copy_constructible_v<RustNodeProviderBundle>,
              "a copied bundle must not retain adapters pointing at another node");

namespace {
using Json = nlohmann::json;
struct Stats {
    int drops = 0;
    int size_reads[4] = {};
};
struct MockNode {
    std::shared_ptr<Stats> stats;
    Json properties = {{"Color3uint8", {{"$color3", {0.1, 0.2, 0.3}}}}, {"Other", 7}};
    Json origins = {{"Color3uint8", "received"}};
    Json revisions = {{"Color3uint8", 42}};
};

void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
ptrdiff_t write_json(const Json& value, uint8_t* out, size_t capacity) {
    const std::string bytes = value.dump();
    if (!out) return static_cast<ptrdiff_t>(bytes.size());
    if (capacity < bytes.size()) return -3;
    std::memcpy(out, bytes.data(), bytes.size());
    return static_cast<ptrdiff_t>(bytes.size());
}
void* clone_view(const void*) { return nullptr; }
void drop_view(void*) {}
uint64_t sequence(const void*) { return 0; }
size_t node_count(const void*) { return 0; }
int32_t visit_nodes(const void*, RustProjectedNodeVisitor, void*) { return 0; }
void* lookup_node(const void*, const uint8_t*, size_t) { return nullptr; }
void* clone_node(const void* node) { return new MockNode(*static_cast<const MockNode*>(node)); }
void drop_node(void* node) {
    auto* value = static_cast<MockNode*>(node);
    ++value->stats->drops;
    delete value;
}
ptrdiff_t node_header(const void*, uint8_t*, size_t) { return -1; }
int32_t node_text(const void*, uint32_t, uint8_t*, size_t, size_t*) { return 0; }
const Json& section(const MockNode& node, uint32_t id) {
    if (id == kRustWorldSectionProperties) return node.properties;
    if (id == kRustWorldSectionOrigins) return node.origins;
    if (id == kRustWorldSectionPropertyRevisions) return node.revisions;
    static const Json empty = Json::object();
    return empty;
}
ptrdiff_t node_property(const void* raw, const uint8_t* key, size_t len, uint8_t* out, size_t cap) {
    const auto& values = static_cast<const MockNode*>(raw)->properties;
    auto it = values.find(std::string(reinterpret_cast<const char*>(key), len));
    return it == values.end() ? -1 : write_json(*it, out, cap);
}
ptrdiff_t node_section(const void* raw, uint32_t id, uint8_t* out, size_t cap) {
    return write_json(section(*static_cast<const MockNode*>(raw), id), out, cap);
}
ptrdiff_t node_metadata_field(const void* raw, uint32_t id, const uint8_t* key, size_t len,
                              uint8_t* out, size_t cap) {
    const auto& values = section(*static_cast<const MockNode*>(raw), id);
    auto it = values.find(std::string(reinterpret_cast<const char*>(key), len));
    return it == values.end() ? -1 : write_json(*it, out, cap);
}
ptrdiff_t node_section_size(const void* raw, uint32_t id) {
    auto* node = const_cast<MockNode*>(static_cast<const MockNode*>(raw));
    if (id < 4) ++node->stats->size_reads[id];
    return static_cast<ptrdiff_t>(section(*static_cast<const MockNode*>(raw), id).size());
}
const RustProjectedWorldViewApi api = {
    2, clone_view, drop_view, sequence, node_count, visit_nodes, lookup_node,
    clone_node, drop_node, node_header, node_text, node_property, node_section,
    node_metadata_field, node_section_size
};
std::shared_ptr<Stats> stats() { return std::make_shared<Stats>(); }
std::shared_ptr<RustWorldNodeView> standalone_node(const std::shared_ptr<Stats>& counts) {
    return std::make_shared<RustWorldNodeView>(&api, new MockNode{counts});
}

void collision_alias_contract() {
    const std::pair<Json,Json> cases[] = {
        {std::string(1, '\0'), "Default"},
        {Json{{"$bytes_hex", "80"}}, "Default"},
        {std::string("\1Ghost"), "Ghost"},
        {Json{{"$bytes_hex", "0141c3a9"}}, "Aé"},
        {Json{{"$bytes_hex", "01ff00"}}, Json{{"$bytes_hex", "ff00"}}},
    };
    for(const auto& [wire, expected]:cases) {
        auto counts=stats();
        auto* record=new MockNode{counts};
        record->properties={{"CollisionGroupReplicate",wire}};
        record->origins={{"CollisionGroupReplicate","received"},{"CollisionGroup","old"}};
        record->revisions={{"CollisionGroupReplicate",19},{"CollisionGroup",3}};
        auto bundle=std::make_shared<RustNodeProviderBundle>(RustWorldNodeView(&api,record));
        auto properties=RustNodeProviderBundle::properties_provider(bundle);
        auto origins=RustNodeProviderBundle::origins_provider(bundle);
        auto revisions=RustNodeProviderBundle::revisions_provider(bundle);
        require(properties->size()==1,"unaliased collision property count");
        bundle->set_collision_group_alias(true);
        require(properties->size()==2&&properties->contains("CollisionGroup")
                    &&properties->lookup("CollisionGroup")==std::optional<Json>(expected),
                "lazy collision property must decode exact wire value and update count");
        require(properties->materialize()==Json{{"CollisionGroupReplicate",wire},{"CollisionGroup",expected}},
                "collision materialization must agree with lookup");
        require(origins->size()==2&&origins->lookup("CollisionGroup")==std::optional<Json>("derived_received")
                    &&origins->materialize()["CollisionGroup"]=="derived_received",
                "collision alias must override provenance without growing existing key");
        require(revisions->size()==2&&revisions->lookup("CollisionGroup")==std::optional<Json>(19)
                    &&revisions->materialize()["CollisionGroup"]==19,
                "collision alias must use wire revision");
        SharedJson original(properties), copy=original;
        copy["CollisionGroup"]="local";
        require(*original.lookup("CollisionGroup")==expected&&*copy.lookup("CollisionGroup")=="local",
                "collision alias must preserve independent local overlay");
        bundle->set_collision_group_alias(false);
        require(properties->size()==1&&!properties->contains("CollisionGroup"),
                "disabling collision alias must invalidate size");
    }
    for(const auto& invalid: {Json(""),Json(false),Json{{"$bytes_hex","0"}},Json{{"$bytes_hex","zz"}}}) {
        bool rejected=false;
        try {(void)received_collision_group_value(invalid);}catch(const std::runtime_error&){rejected=true;}
        require(rejected,"invalid wire collision group must be rejected");
    }
    auto* record=new MockNode{stats()};
    record->properties={{"CollisionGroupReplicate",std::string(1,'\0')}};
    record->revisions={{"CollisionGroup",7}};
    auto bundle=std::make_shared<RustNodeProviderBundle>(RustWorldNodeView(&api,record));
    bundle->set_collision_group_alias(true);
    auto revisions=RustNodeProviderBundle::revisions_provider(bundle);
    require(revisions->size()==1&&revisions->lookup("CollisionGroup")==std::optional<Json>(7),
            "missing wire revision must preserve existing public revision");
}
}

int main() {
    try {
        collision_alias_contract();
        static_assert(sizeof(CompactJsonSizeCache) == sizeof(uint32_t));
        CompactJsonSizeCache count_cache;
        require(!count_cache.has_value(), "a new compact count cache must be empty");
        count_cache.store(0);
        require(count_cache.has_value() && count_cache.value() == 0
                    && count_cache.value_or(91) == 0,
                "zero counts must be cached and reused");
        count_cache.store(37);
        require(count_cache.value_or(91) == 37,
                "small counts must be cached and reused");
        count_cache.store(CompactJsonSizeCache::kUncached);
        require(!count_cache.has_value()
                    && count_cache.value_or(CompactJsonSizeCache::kUncached)
                        == CompactJsonSizeCache::kUncached,
                "the sentinel value must remain exact and uncached");
        const auto maximum_count = std::numeric_limits<Json::size_type>::max();
        count_cache.store(maximum_count);
        require(!count_cache.has_value() && count_cache.value_or(maximum_count) == maximum_count,
                "size_t maximum must remain exact and uncached");
        count_cache.store(12);
        count_cache.reset();
        require(!count_cache.has_value(), "reset must invalidate a small cached count");

        require(!RustNodeProviderBundle::node_view({})
                    && !RustNodeProviderBundle::properties_provider({})
                    && !RustNodeProviderBundle::origins_provider({})
                    && !RustNodeProviderBundle::revisions_provider({}),
                "provider factories must return empty aliases for a missing owner");
        auto counts = stats();
        auto bundle = std::make_shared<RustNodeProviderBundle>(
            RustWorldNodeView(&api, new MockNode{counts}));
        bundle->set_color_alias(false);
        auto properties = RustNodeProviderBundle::properties_provider(bundle);
        auto origins = RustNodeProviderBundle::origins_provider(bundle);
        auto revisions = RustNodeProviderBundle::revisions_provider(bundle);
        require(properties->size() == 2 && origins->size() == 1 && revisions->size() == 1,
                "unaliased provider sizes must match the backing record");
        require(counts->size_reads[kRustWorldSectionProperties] == 1
                    && counts->size_reads[kRustWorldSectionOrigins] == 1
                    && counts->size_reads[kRustWorldSectionPropertyRevisions] == 1,
                "first provider size reads must reach the Rust node");
        require(properties->size() == 2 && origins->size() == 1 && revisions->size() == 1
                    && counts->size_reads[kRustWorldSectionProperties] == 1
                    && counts->size_reads[kRustWorldSectionOrigins] == 1
                    && counts->size_reads[kRustWorldSectionPropertyRevisions] == 1,
                "cached provider sizes must be reused without another Rust read");
        bundle->set_color_alias(true);
        require(properties->size() == 3 && origins->size() == 2 && revisions->size() == 2,
                "changing Color alias must invalidate each cached size");
        require(counts->size_reads[kRustWorldSectionProperties] == 2
                    && counts->size_reads[kRustWorldSectionOrigins] == 2
                    && counts->size_reads[kRustWorldSectionPropertyRevisions] == 2,
                "changing Color alias must force one refreshed size read per provider");

        auto standalone = standalone_node(stats());
        RustNodePropertiesProvider standalone_properties(standalone, true);
        RustNodeSectionProvider standalone_origins(standalone, kRustWorldSectionOrigins, true);
        RustNodeSectionProvider standalone_revisions(standalone, kRustWorldSectionPropertyRevisions, true);
        require(properties->lookup("Color") == standalone_properties.lookup("Color")
                    && origins->lookup("Color") == standalone_origins.lookup("Color")
                    && revisions->lookup("Color") == standalone_revisions.lookup("Color"),
                "bundle Color projections must match standalone providers");
        require(properties->materialize() == standalone_properties.materialize()
                    && origins->materialize() == standalone_origins.materialize()
                    && revisions->materialize() == standalone_revisions.materialize(),
                "bundle materialization must match standalone providers");
        require(origins->lookup("Color") == std::optional<Json>("derived_received")
                    && revisions->lookup("Color") == std::optional<Json>(42),
                "Color origin and revision aliases must preserve their values");

        {
            SharedJson property_overlay(properties), origin_overlay(origins), revision_overlay(revisions);
            property_overlay["property-only"] = true;
            origin_overlay["origin-only"] = true;
            revision_overlay["revision-only"] = true;
            require(property_overlay.contains("property-only") && !property_overlay.contains("origin-only")
                        && origin_overlay.contains("origin-only") && !origin_overlay.contains("revision-only")
                        && revision_overlay.contains("revision-only") && !revision_overlay.contains("property-only")
                        && !properties->contains("property-only") && !origins->contains("origin-only")
                        && !revisions->contains("revision-only"),
                    "the three provider-backed SharedJson overlays must remain independent");
        }

        auto alternate_counts = stats();
        auto* alternate_record = new MockNode{alternate_counts};
        alternate_record->properties["Other"] = 88;
        auto alternate_bundle = std::make_shared<RustNodeProviderBundle>(
            RustWorldNodeView(&api, alternate_record));
        auto alternate_provider = RustNodeProviderBundle::properties_provider(alternate_bundle);
        require(alternate_provider->lookup("Other") == std::optional<Json>(88)
                    && properties->lookup("Other") == std::optional<Json>(7),
                "each explicit owner factory must alias that owner's embedded provider");
        alternate_bundle.reset();
        require(alternate_counts->drops == 0
                    && alternate_provider->lookup("Other") == std::optional<Json>(88),
                "an alias must pin the owner from which its embedded provider was selected");
        alternate_provider.reset();
        require(alternate_counts->drops == 1,
                "the supplied owner must be released after its final alias is dropped");

        SharedJson original(properties);
        SharedJson copied = original;
        original["Other"] = 9;
        require(original.at("Other") == 9 && copied.at("Other") == 7,
                "a sparse COW edit must not mutate an escaped provider copy");

        auto property_node = RustNodeProviderBundle::node_view(bundle);
        bundle.reset();
        require(properties->lookup("Other") == std::optional<Json>(7)
                    && origins->lookup("Color3uint8") == std::optional<Json>("received")
                    && revisions->lookup("Color3uint8") == std::optional<Json>(42),
                "each escaped provider must remain readable after bundle release");
        property_node.reset();
        require(counts->drops == 0, "provider aliases must keep the Rust node alive");
        properties.reset();
        original.reset_to(std::make_shared<const Json>(Json::object()));
        require(counts->drops == 0, "copied provider must keep the node until its own release");
        copied.reset_to(std::make_shared<const Json>(Json::object()));
        require(counts->drops == 0, "origin and revision providers must retain the shared node");
        origins.reset();
        require(counts->drops == 0, "revision provider must retain the shared node");
        revisions.reset();
        require(counts->drops == 1, "the shared node handle must be released exactly once");

        auto replacement_counts = stats();
        auto replacement_bundle = std::make_shared<RustNodeProviderBundle>(
            RustWorldNodeView(&api, new MockNode{replacement_counts}));
        SharedJson replaced(RustNodeProviderBundle::properties_provider(replacement_bundle));
        replacement_bundle.reset();
        replaced.reset_to(std::make_shared<const Json>(Json{{"safe", true}}));
        require(replacement_counts->drops == 1 && replaced.at("safe") == true,
                "replacing the final provider must release the handle once and retain replacement data");

        std::cout << "Rust world provider bundle ownership and parity passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
