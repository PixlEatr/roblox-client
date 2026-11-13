#include "shared_json.hpp"

#include <iostream>
#include <stdexcept>

namespace {
using Json = nlohmann::json;
void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}

class MapProvider final : public SharedJsonProvider {
public:
    explicit MapProvider(Json value) : value_(std::move(value)) {}
    Json::value_t type() const override { return value_.type(); }
    Json::size_type size() const override { return value_.size(); }
    bool contains(std::string_view key) const override { return value_.contains(std::string(key)); }
    std::optional<Json> lookup(std::string_view key) const override {
        auto it=value_.find(std::string(key));
        return it==value_.end()?std::nullopt:std::optional<Json>(*it);
    }
    Json materialize() const override { return value_; }
private:
    Json value_;
};
}

int main() {
    try {
        std::cout << "SharedJson layout bytes: " << sizeof(SharedJson)
                  << ", grouped state variant bytes: " << SharedJson::state_layout_bytes()
                  << '\n';
#if defined(__APPLE__) && defined(__aarch64__) && defined(_LIBCPP_VERSION)
        require(sizeof(SharedJson)<=48,
                "SharedJson grouped-state layout exceeded 48 bytes on macOS arm64 libc++");
#endif

        auto base_lifetime=std::make_shared<const Json>(Json{{"value",1}});
        std::weak_ptr<const Json> base_weak=base_lifetime;
        SharedJson base_backed(base_lifetime);
        const Json& borrowed_base=base_backed.read();
        base_lifetime.reset();
        base_backed["value"]=2;
        require(!base_weak.expired()&&borrowed_base.at("value")==1&&base_backed.at("value")==2,
                "local JSON materialization must retain its immutable base for borrowed-read lifetime");

        auto provider=std::make_shared<MapProvider>(Json{{"a",1},{"b",2}});
        auto provider_for_reset=std::make_shared<MapProvider>(Json{{"a",1},{"b",2}});
        std::weak_ptr<const SharedJsonProvider> provider_weak=provider;
        std::weak_ptr<const SharedJsonProvider> reset_provider_weak=provider_for_reset;
        SharedJson provider_backed{std::shared_ptr<const SharedJsonProvider>(provider)};
        {
            SharedJsonScope scope;
            const Json* borrowed_key=provider_backed.lookup("a");
            require(borrowed_key&&*borrowed_key==1,"provider key lookup must return a cached borrowed value");
            const SharedJson& const_provider=provider_backed;
            const Json& first_whole=const_provider.read();
            require(first_whole==Json({{"a",1},{"b",2}}),
                    "provider-backed whole reads must materialize the original object");
            provider_backed["a"]=10;
            provider_backed["c"]=3;
            require(provider_backed.size()==3,
                    "provider sparse overlay must report replacements and additions");
            require(provider_backed.at("a")==10&&provider_backed.at("b")==2
                        &&provider_backed.at("c")==3,
                    "provider sparse overlay must retain base values and local edits");
            const SharedJson& updated_provider=provider_backed;
            const Json& second_whole=updated_provider.read();
            require(first_whole.at("a")==1&&second_whole.at("a")==10
                        &&second_whole.at("c")==3,
                    "new provider materialization must not invalidate an older whole-read reference");

            {
                SharedJson copied_provider=provider_backed;
                provider_backed["a"]=11;
                require(copied_provider.at("a")==10&&provider_backed.at("a")==11,
                        "copying sparse provider state must preserve copy-on-write isolation");
            }
            provider.reset();
            require(!provider_weak.expired(),"provider must stay alive while the holder references it");
            provider_backed.write()["dropped_provider"]=true;
            require(provider_weak.expired()&&*borrowed_key==1,
                    "provider key references must survive materialization after provider release");
        }
        auto reset_base=std::make_shared<const Json>(Json{{"reset",true}});
        provider_backed.reset_to(std::shared_ptr<const Json>(reset_base));
        require(provider_backed.read()==*reset_base&&!provider_backed.is_materialized(),
                "resetting a provider to a JSON base must discard local state and cache");
        provider_backed.reset_to(std::shared_ptr<const SharedJsonProvider>(provider_for_reset));
        provider_for_reset.reset();
        require(!reset_provider_weak.expired()&&provider_backed.at("b")==2,
                "provider backing must remain alive through its typed shared ownership");
        SharedJson moved_provider(std::move(provider_backed));
        require(provider_backed.is_null()&&moved_provider.at("b")==2,
                "moving a provider-backed value must transfer ownership and leave a readable null source");
        provider_backed.write()["usable"]=true;
        require(provider_backed.read().at("usable")==true&&moved_provider.at("b")==2,
                "a moved-from provider holder must remain independently writable");
        auto provider_again=reset_provider_weak.lock();
        require(static_cast<bool>(provider_again),"reset fixture must retain the provider it reuses");
        moved_provider.reset_to(std::shared_ptr<const Json>(reset_base));
        require(moved_provider.read()==*reset_base&&!moved_provider.is_materialized(),
                "provider-to-base reset must discard local state");
        moved_provider.reset_to(std::move(provider_again));
        require(moved_provider.at("a")==1,
                "base-to-provider reset must restore provider reads");
        moved_provider.erase("a");
        require(!moved_provider.contains("a"),"erase must remove a provider key after materialization");
        moved_provider["a"]=42;
        require(moved_provider.at("a")==42,"a deleted provider key must be reinsertable");

        SharedJson owned_source(Json{{"copy",1}}),owned_destination(Json{{"old",true}});
        owned_destination=owned_source;
        owned_source["copy"]=2;
        require(owned_destination.at("copy")==1&&owned_source.at("copy")==2,
                "copy assignment of owned JSON must create an isolated value");

        SharedJson stored;
        Json ordinary;
        stored = {{"State", 1}, {"Position", {{"$vector3", {1, 2, 3}}}}};
        ordinary = {{"State", 1}, {"Position", {{"$vector3", {1, 2, 3}}}}};
        require(stored.read() == ordinary && stored.is_object(),
                "braced property assignment must preserve object keys");
        stored["State"] = 2;
        ordinary["State"] = 2;
        require(stored.read() == ordinary, "assigned properties must remain writable");

        stored = {1, "two", {3, 4}, nullptr};
        ordinary = {1, "two", {3, 4}, nullptr};
        require(stored.read() == ordinary && stored.is_array(),
                "mixed arrays must preserve their native JSON shape");
        stored = {{"only", Json::array({1, 2})}};
        ordinary = {{"only", Json::array({1, 2})}};
        require(stored.read() == ordinary && stored.is_object(),
                "a single property must stay an object");

        stored = {};
        ordinary = {};
        require(stored.read() == ordinary, "empty assignment must match ordinary JSON");
        stored = {Json::object()};
        ordinary = {Json::object()};
        require(stored.read() == ordinary, "an explicit singleton array must stay an array");

        auto backing = std::make_shared<const Json>(Json{{"untouched", true}});
        stored.reset_to(backing);
        SharedJson other = stored;
        stored = {{"changed", true}};
        require(other.read() == *backing && !other.is_materialized(),
                "replacement must not mutate shared snapshot backing");
        Json& escaped = stored.write();
        SharedJson copied = stored;
        escaped["changed"] = false;
        require(copied.read().at("changed") == true,
                "a copy after assignment must retain mutable-borrow isolation");
        const Json exported = {{"properties", copied}};
        require(exported.at("properties").is_object()
                && exported.at("properties").at("changed") == true,
                "export must preserve assigned object shape");
        SharedJsonPool pool;
        const Json provenance={{"Name","received"},{"Value","received"}};
        std::vector<SharedJson> nodes;
        for(size_t index=0;index<10000;++index)nodes.push_back(pool.intern(provenance));
        for(const auto& node:nodes)
            require(&node.read()==&nodes.front().read(),
                    "equal origins must share one immutable map");
        nodes.front()["Value"]="local";
        require(nodes[1].read()==provenance&&nodes.front().read()!=provenance,
                "changing one instance origin must not affect its peers");
        std::vector<SharedJson> distinct;
        for(size_t index=0;index<1000;++index) {
            const Json value={{"field_"+std::to_string(index),"received"}};
            distinct.push_back(pool.intern(value));
            require(distinct.back().read()==value,
                    "pool capacity fallback must preserve distinct maps");
        }
        distinct.clear();
        const Json fresh={{"fresh","derived_received"}};
        auto one=pool.intern(fresh),two=pool.intern(fresh);
        require(&one.read()==&two.read(),"expired pool entries must be reusable");
        std::cout << "Shared JSON construction, assignment, and isolation passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
