#include "world_bindings.hpp"

#include <fstream>
#include <iostream>
#include <stdexcept>
#include <thread>
#include <vector>

namespace {
using namespace world_bindings;
void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
}

int main() {
    using namespace world_bindings;
    try {
        require(constructor_class_cache_size() == 0,
                "constructor registry must start without parsed class DOMs");

        const Json* part = constructor_class("Part");
        require(part && part->value("creatable", false),
                "a known class must parse on first lookup");
        require(constructor_class_cache_size() == 1,
                "one lookup must materialize only its class object");
        require(constructor_class("NotARegistryClass") == nullptr,
                "unknown class lookup must return no descriptor");
        require(constructor_class_cache_size() == 1,
                "unknown names must not grow the lazy cache");

        std::ifstream input(CONSTRUCTOR_REGISTRY_PATH, std::ios::binary);
        require(static_cast<bool>(input), "constructor registry source must be readable in the fixture");
        const std::string source((std::istreambuf_iterator<char>(input)), {});
        const Json registry = Json::parse(source);
        const Json& classes = registry.at("classes");
        require(classes.size() == 658,
                "fixture source class count differs from the indexed registry");
        require(constructor_class_cache_size() == 1,
                "reading the fixture reference must not eagerly fill the lazy cache");
        require(classes.contains("MeshPart"), "concurrent lookup fixture class must exist");
        const Json* stable_part = part;
        const auto property_it = part->at("properties").begin();
        require(property_it != part->at("properties").end(), "Part must expose a descriptor for pointer-stability coverage");
        const Json* stable_property = &property_it.value();
        std::vector<const Json*> concurrent_results(12, nullptr);
        std::vector<std::thread> workers;
        for (std::size_t index = 0; index < concurrent_results.size(); ++index) {
            workers.emplace_back([&, index] {
                concurrent_results[index] = constructor_class("MeshPart");
            });
        }
        for (auto& worker : workers) worker.join();
        require(concurrent_results.front() != nullptr,
                "concurrent first lookup must parse the requested class");
        for (const Json* result : concurrent_results)
            require(result == concurrent_results.front(),
                    "concurrent same-class lookups must share one stable parsed object");
        require(constructor_class_cache_size() == 2,
                "concurrent lookups must materialize one additional class only");

        Json parent_index = Json::object();
        parent_index["Part"] = "SnapshotOverride";
        parent_index["ExternalClass"] = "ExternalBase";
        apply_constructor_class_parents(parent_index);
        std::size_t expected_parents = 0;
        for (auto it = classes.begin(); it != classes.end(); ++it) {
            if (it.value().contains("parent")) {
                ++expected_parents;
                require(parent_index.at(it.key()) == it.value().at("parent"),
                        "generated parent index must exactly match constructor parent metadata");
            }
        }
        require(expected_parents == 657,
                "constructor parent index count differs from the pinned registry");
        require(parent_index.at("ExternalClass") == "ExternalBase",
                "constructor parent merge must preserve unrelated snapshot entries");
        require(parent_index.at("Part") == classes.at("Part").at("parent"),
                "constructor ancestry must override the snapshot value as before");
        require(!classes.at("Object").contains("parent") && !parent_index.contains("Object"),
                "parent merge must not invent ancestry for the root Object class");

        for (auto it = classes.begin(); it != classes.end(); ++it) {
            const Json* parsed = constructor_class(it.key());
            require(parsed && *parsed == it.value(),
                    "lazy class slice must preserve exact constructor/default/descriptor JSON");
        }
        require(constructor_class_cache_size() == classes.size(),
                "full parity traversal must cache each class once");
        require(constructor_class("Part") == stable_part
                    && &constructor_class("Part")->at("properties").begin().value() == stable_property,
                "class and escaped property pointers must remain stable after unrelated class loads");
        std::cout << "lazy constructor registry parity passed for " << classes.size()
                  << " classes and " << expected_parents << " parent entries\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
