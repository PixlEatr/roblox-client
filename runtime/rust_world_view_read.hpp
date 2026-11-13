#pragma once

#include "json.hpp"

#include <cstddef>
#include <cstdint>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

// Shared two-pass decoder for Rust world-view callbacks. The probed length is
// carried into the payload read so optional callers never probe twice.
namespace rust_world_view_read {
using Json = nlohmann::json;
constexpr uint64_t MAX_JSON_BYTES = 64u * 1024u * 1024u;

template<class Reader>
std::vector<uint8_t> payload(Reader&& read, ptrdiff_t required, const char* what) {
    if (required < 0)
        throw std::runtime_error(std::string("Rust world view could not read ") + what);
    if (static_cast<uint64_t>(required) > MAX_JSON_BYTES)
        throw std::runtime_error(std::string("Rust world view ") + what + " exceeds limit");
    std::vector<uint8_t> bytes(static_cast<size_t>(required));
    const ptrdiff_t written = read(bytes.data(), bytes.size());
    if (written != required)
        throw std::runtime_error(std::string("Rust world view returned incomplete ") + what);
    return bytes;
}

inline Json parse(std::vector<uint8_t>& bytes, const char* what) {
    try { return Json::parse(bytes.begin(), bytes.end()); }
    catch (...) { throw std::runtime_error(std::string("Rust world view returned invalid ") + what); }
}

template<class Reader>
Json read_json(Reader&& read, const char* what) {
    const ptrdiff_t required = read(nullptr, 0);
    auto bytes = payload(read, required, what);
    return parse(bytes, what);
}

// `-1` means absent for property/metadata/section. Section callbacks also
// report JSON null with `-2`; the existing wrapper treats that as absent.
template<class Reader>
std::optional<Json> read_optional_json(Reader&& read, const char* what,
                                       bool section_null_is_absent = false) {
    const ptrdiff_t required = read(nullptr, 0);
    if (required == -1 || (section_null_is_absent && required == -2)) return {};
    if (required < 0)
        throw std::runtime_error(std::string("Rust world view ") + what + " read failed");
    auto bytes = payload(read, required, what);
    return parse(bytes, what);
}
} // namespace rust_world_view_read
