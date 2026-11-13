#pragma once

#include "json.hpp"
#include <stdexcept>
#include <string>

// The wire value starts with an ID byte. An omitted name means Default;
// arbitrary non-UTF-8 group names must retain their exact bytes.
inline nlohmann::json received_collision_group_value(const nlohmann::json& value) {
    using Json = nlohmann::json;
    std::string bytes;
    if (value.is_string()) bytes = value.get<std::string>();
    else if (value.is_object() && value.size() == 1 && value.contains("$bytes_hex")
             && value["$bytes_hex"].is_string()) {
        const auto& hex = value["$bytes_hex"].get_ref<const std::string&>();
        if (hex.size() % 2 || hex.size() > 2 * 1024 * 1024)
            throw std::runtime_error("invalid CollisionGroupReplicate byte-string length");
        auto nibble = [](char c) -> int {
            if (c >= '0' && c <= '9') return c - '0';
            if (c >= 'a' && c <= 'f') return c - 'a' + 10;
            if (c >= 'A' && c <= 'F') return c - 'A' + 10;
            return -1;
        };
        bytes.reserve(hex.size() / 2);
        for (size_t i = 0; i < hex.size(); i += 2) {
            int a = nibble(hex[i]), b = nibble(hex[i + 1]);
            if (a < 0 || b < 0)
                throw std::runtime_error("invalid CollisionGroupReplicate byte-string hex");
            bytes.push_back(char((a << 4) | b));
        }
    } else throw std::runtime_error("received CollisionGroupReplicate must be a string or tagged byte-string");
    if (bytes.empty()) throw std::runtime_error("received CollisionGroupReplicate is missing its group ID");
    if (bytes.size() == 1) return "Default";
    bytes.erase(0, 1);
    // JSON's strict serializer provides the same UTF-8 validity boundary as
    // the wire decoder. Invalid names stay tagged instead of being repaired.
    Json text(bytes);
    try { (void)text.dump(); return text; }
    catch (const Json::type_error&) {
        static constexpr char digits[] = "0123456789abcdef";
        std::string hex;
        hex.reserve(bytes.size() * 2);
        for (unsigned char byte : bytes) {
            hex.push_back(digits[byte >> 4]);
            hex.push_back(digits[byte & 15]);
        }
        return Json{{"$bytes_hex", std::move(hex)}};
    }
}
