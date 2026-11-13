#include "world_bindings.hpp"
#include <lualib.h>
#include <iostream>
#include <memory>
#include <stdexcept>

namespace {
void require(bool passed, const char* message) {
    if (!passed) throw std::runtime_error(message);
}
}

int main() {
    using namespace world_bindings;
    try {
        Node empty;
        require(empty.script_state.code().is_null(), "a default node must not acquire script code");
        require(!empty.script_state.allocated() && empty.script_state.origin().empty()
                && empty.script_state.disabled_generation() == 0,
                "ordinary node script state must remain unallocated and empty");
        require(empty.properties.is_object(), "default properties must remain an object");
        Node other_empty;
        require(&empty.properties.read() == &other_empty.properties.read(),
                "default property maps must share immutable empty storage");
        require(&empty.origins.read() == &other_empty.origins.read()
                && &empty.revisions.read() == &other_empty.revisions.read(),
                "default metadata maps must share immutable empty storage");
        empty.attributes.set("Count", 1);
        empty.origins["Transparency"] = "local";
        empty.revisions["Transparency"] = 2;
        require(other_empty.attributes.size() == 0 && other_empty.origins.empty()
                && other_empty.revisions.empty(), "empty map writes leaked between nodes");
        RustAttributeStore unknown;
        require(unknown.set("Null", nullptr), "first unknown nil must be observable");
        require(!unknown.set("Null", nullptr), "repeated unknown nil must not emit another change");
        require(unknown.lookup("Null").has_value(), "unknown nil must retain an explicit entry");
        RustAttributeStore fork = unknown;
        require(fork.erase("Null"), "raw unknown erase must remove an explicit null");
        require(!fork.lookup("Null") && unknown.lookup("Null"), "raw erase leaked across a fork");
        unknown.set_known(true);
        require(!unknown.set("Null", nullptr), "known null removal must preserve value event semantics");
        require(!unknown.contains("Null"), "known nil must remove the key");
        require(!unknown.set("Missing", nullptr), "known missing nil is unchanged");
        require(unknown.size() == 0, "nil operations left unexpected values");
        const Json exported = {{"origins", empty.origins}, {"script_code", empty.script_state.code()}};
        require(exported.at("origins").is_object(), "storage serialization must preserve object keys");
        require(exported.at("script_code").is_null(), "storage serialization must preserve null");
        Node scripted;
        scripted.script_state.code_mut() = Json{{"source", "original"}, {"authored_override", true}};
        scripted.script_state.set_origin("source-node");
        scripted.script_state.bump_disabled_generation();
        Node actor_snapshot = scripted;
        actor_snapshot.script_state.code_mut()["source"] = "actor-copy";
        actor_snapshot.script_state.set_origin("actor-source");
        actor_snapshot.script_state.bump_disabled_generation();
        require(scripted.script_state.code().at("source") == "original"
                && scripted.script_state.origin() == "source-node"
                && scripted.script_state.disabled_generation() == 1,
                "Actor-style Node copy mutation leaked into its source");
        Node restored;
        restored = scripted;
        restored.script_state.code_mut()["source"] = "restored-copy";
        restored.script_state.set_disabled_generation(7);
        require(scripted.script_state.code().at("source") == "original"
                && scripted.script_state.disabled_generation() == 1,
                "copy-restore assignment shared mutable script state");
        Node ordinary;
        restored = ordinary;
        require(!restored.script_state.allocated() && restored.script_state.code().is_null()
                && restored.script_state.origin().empty()
                && restored.script_state.disabled_generation() == 0,
                "restoring an ordinary node did not release its lazy script state");
        SharedJson moved_source(Json{{"value", 9}});
        SharedJson moved_target(std::move(moved_source));
        require(moved_target.read().at("value") == 9, "move lost the stored value");
        require(moved_source.is_null(), "moved-from storage must remain readable");
        moved_source.write()["usable"] = true;
        require(moved_source.read().at("usable") == true, "moved-from storage must remain writable");
        std::weak_ptr<const Json> lifetime;
        {
            auto owner = std::make_shared<const Json>(Json{
                {"version", 1}, {"sequence", 0},
                {"services", {{"Workspace", "workspace"}}},
                {"nodes", Json::array({
                    {{"id", "game"}, {"class_name", "DataModel"}, {"name", "Game"},
                     {"parent", nullptr}, {"properties", Json::object()}},
                    {{"id", "workspace"}, {"class_name", "Workspace"}, {"name", "Workspace"},
                     {"parent", "game"}, {"properties", Json::object()}},
                    {{"id", "part"}, {"class_name", "Part"}, {"name", "Probe"},
                     {"parent", "workspace"}, {"properties", {{"Transparency", 0.25}}},
                     {"origins", {{"Transparency", "received"}}},
                     {"attributes", {{"Count", 3}}}, {"attributes_known", true},
                     {"property_revisions", {{"Transparency", 0}}}}
                })}
            });
            lifetime = owner;
            auto first = parse_snapshot(owner);
            auto second = parse_snapshot(owner);
            owner.reset();
            require(!lifetime.expired(), "worlds must retain their immutable backing");
            auto& a = first.nodes.at("part");
            auto& b = second.nodes.at("part");
            require(a.script_state.code().is_null() && b.script_state.code().is_null(), "Parts must not become scripts");
            require(&a.properties.read() == &b.properties.read(), "initial properties must share storage");
            require(a.properties.read().at("Transparency") == 0.25, "snapshot lifetime was lost");

            Json& escaped = a.properties.write();
            escaped["Transparency"] = 0.5;
            Node copy = a;
            escaped["Transparency"] = 0.6;
            require(copy.properties.read().at("Transparency") == 0.5,
                    "copy after a mutable borrow must not share the mutable value");
            require(b.properties.read().at("Transparency") == 0.25,
                    "a private property write leaked to another world");
            a.attributes.set("Count", 8);
            a.origins["Transparency"] = "local";
            a.revisions["Transparency"] = 7;
            require(*b.attributes.lookup("Count") == 3, "attributes leaked between worlds");
            require(b.origins.read().at("Transparency") == "received", "origins leaked between worlds");
            require(b.revisions.read().at("Transparency") == 0, "revisions leaked between worlds");

            std::unique_ptr<lua_State, decltype(&lua_close)> lua(luaL_newstate(), &lua_close);
            require(bool(lua), "could not allocate test Luau state");
            Json delta = {{"version", 1}, {"delta", true}, {"sequence", 1},
                {"actions", Json::array({{{"sequence", 1}, {"kind", "set"},
                    {"id", "part"}, {"fields", {{"Transparency", 0.75}}}}})}};
            ValidatedDeltaView view(delta);
            update_snapshot(lua.get(), first, view);
            require(a.properties.read().at("Transparency") == 0.75, "received update did not apply");
            require(b.properties.read().at("Transparency") == 0.25,
                    "received update affected a world before its own delivery");
            delta["actions"][0]["fields"]["Transparency"] = 0.9;
            require(a.properties.read().at("Transparency") == 0.75,
                    "world retained a mutable reference into an incoming delta");
            ValidatedDeltaView second_view(delta);
            update_snapshot(lua.get(), second, second_view);
            require(b.properties.read().at("Transparency") == 0.9, "second world missed its delivery");
            require(a.properties.read().at("Transparency") == 0.75,
                    "second world delivery affected the first world");
        }
        require(lifetime.expired(), "snapshot backing was retained after all worlds were destroyed");
        std::cout << "World storage ownership, copy, and delta isolation passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
