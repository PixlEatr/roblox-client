#pragma once

#include "json.hpp"
#include "shared_json.hpp"
#include "rust_engine_bridge.hpp"

#include <optional>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

// Instance-owned attribute state. Rust owns the map in normal Rust-hosted
// clients; the JSON fallback exists only for standalone native fixtures which
// do not register the Rust callback table.
class RustAttributeStore {
public:
    using Json = nlohmann::json;

    explicit RustAttributeStore(bool known = false) {
        if (auto api = rust_attribute_store_api()) {
            rust_ = api->create_empty(known ? 1 : 0);
            if (!rust_) throw std::runtime_error("Rust attribute store allocation failed");
        } else {
            fallback_ = std::make_unique<Fallback>(known);
        }
    }
    RustAttributeStore(const Json& snapshot, bool known) { replace(snapshot, known); }
    RustAttributeStore(const RustAttributeStore& other) {
        if (other.rust_) {
            auto api = rust_attribute_store_api();
            if (!api || !(rust_ = api->clone_store(other.rust_))) throw std::runtime_error("Rust attribute store clone failed");
        } else if (other.fallback_) fallback_ = std::make_unique<Fallback>(*other.fallback_);
    }
    RustAttributeStore& operator=(const RustAttributeStore& other) {
        if (this == &other) return *this;
        RustAttributeStore copy(other);
        swap(copy);
        return *this;
    }
    RustAttributeStore(RustAttributeStore&& other) noexcept { swap(other); }
    RustAttributeStore& operator=(RustAttributeStore&& other) noexcept {
        if (this != &other) { reset(); fallback_.reset(); swap(other); }
        return *this;
    }
    ~RustAttributeStore() { reset(); }

    void swap(RustAttributeStore& other) noexcept {
        std::swap(rust_, other.rust_); fallback_.swap(other.fallback_);
    }
    Json snapshot() const {
        if (!rust_) return fallback_ ? fallback_->attributes.read() : empty_object();
        auto api = require_api(); auto bytes = read_bytes([&](uint8_t* p, size_t n) { return api->snapshot(rust_, p, n); });
        return Json::parse(bytes.begin(), bytes.end());
    }
    std::optional<Json> lookup(const std::string& name) const {
        if (!rust_) { const auto& attrs = fallback_ ? fallback_->attributes.read() : empty_object(); auto it = attrs.find(name); if (it == attrs.end()) return {}; return *it; }
        auto api = require_api();
        const auto* key = reinterpret_cast<const uint8_t*>(name.data());
        auto length = api->lookup(rust_, key, name.size(), nullptr, 0);
        if (length == 0) return {};
        if (length < 0) throw std::runtime_error("Rust attribute lookup failed");
        std::vector<uint8_t> bytes(static_cast<size_t>(length));
        auto written = api->lookup(rust_, key, name.size(), bytes.data(), bytes.size());
        if (written != length) throw std::runtime_error("Rust attribute lookup serialization failed");
        return Json::parse(bytes.begin(), bytes.end());
    }
    void replace(const Json& value, bool known) {
        if (!value.is_object()) throw std::runtime_error("attribute snapshot must be an object");
        if (!rust_ && !rust_attribute_store_api()) { auto& fallback = ensure_fallback(); fallback.attributes = value; fallback.known = known; return; }
        if (!rust_) {
            auto api = require_api(); auto encoded = value.dump();
            rust_ = api->create_json(reinterpret_cast<const uint8_t*>(encoded.data()), encoded.size(), known ? 1 : 0);
            if (!rust_) throw std::runtime_error("Rust attribute store import failed");
            fallback_.reset();
            return;
        }
        auto api = require_api(); auto encoded = value.dump();
        if (api->replace(rust_, reinterpret_cast<const uint8_t*>(encoded.data()), encoded.size(), known ? 1 : 0) != 0)
            throw std::runtime_error("Rust attribute store replace failed");
    }
    bool set(const std::string& name, const Json& value) {
        restore_moved_store();
        if (!rust_) {
            auto& fallback = ensure_fallback(); auto& map = fallback.attributes.write(); auto it = map.find(name);
            bool changed = it == map.end() ? (!value.is_null() || !fallback.known) : it.value() != value;
            if (value.is_null() && fallback.known) map.erase(name); else map[name] = value;
            return changed;
        }
        auto api = require_api(); auto encoded = value.dump(); uint8_t changed = 0;
        if (api->set(rust_, reinterpret_cast<const uint8_t*>(name.data()), name.size(), reinterpret_cast<const uint8_t*>(encoded.data()), encoded.size(), &changed) != 0)
            throw std::runtime_error("Rust attribute store set failed");
        return changed != 0;
    }
    bool erase(const std::string& name) {
        restore_moved_store();
        if (!rust_) {
            auto& map = ensure_fallback().attributes.write(); return map.erase(name) != 0;
        }
        auto api = require_api(); uint8_t changed = 0;
        if (api->erase(rust_, reinterpret_cast<const uint8_t*>(name.data()), name.size(), &changed) != 0)
            throw std::runtime_error("Rust attribute store erase failed");
        return changed != 0;
    }
    bool known() const {
        if (!rust_) return fallback_ && fallback_->known;
        int32_t result = require_api()->known(rust_);
        if (result < 0) throw std::runtime_error("Rust attribute store known-state read failed");
        return result != 0;
    }
    void set_known(bool known) {
        restore_moved_store();
        if (!rust_) { ensure_fallback().known = known; return; }
        if (require_api()->set_known(rust_, known ? 1 : 0) != 0) throw std::runtime_error("Rust attribute store known-state update failed");
    }
    size_t size() const {
        if (!rust_) return fallback_ ? fallback_->attributes.size() : 0;
        auto n = require_api()->size(rust_); if (n < 0) throw std::runtime_error("Rust attribute store size failed"); return static_cast<size_t>(n);
    }
    bool contains(const std::string& name) const {
        if (!rust_) return fallback_ && fallback_->attributes.contains(name);
        int32_t result = require_api()->contains(rust_, reinterpret_cast<const uint8_t*>(name.data()), name.size());
        if (result < 0) throw std::runtime_error("Rust attribute store contains failed"); return result != 0;
    }

private:
    struct Fallback {
        explicit Fallback(bool is_known = false) : attributes(SharedJson::object()), known(is_known) {}
        SharedJson attributes;
        bool known;
    };
    static const Json& empty_object() { static const Json value = Json::object(); return value; }
    void restore_moved_store() {
        if(!rust_&&!fallback_)if(auto api=rust_attribute_store_api()) {
            rust_=api->create_empty(0);
            if(!rust_)throw std::runtime_error("Rust attribute store allocation failed");
        }
    }
    Fallback& ensure_fallback() {
        if (!fallback_) fallback_ = std::make_unique<Fallback>();
        return *fallback_;
    }
    template<class F> static std::vector<uint8_t> read_bytes(F&& reader) {
        auto n = reader(nullptr, 0); if (n < 0) throw std::runtime_error("Rust attribute store export failed");
        std::vector<uint8_t> bytes(static_cast<size_t>(n));
        auto written = reader(bytes.data(), bytes.size());
        if (written != n) throw std::runtime_error("Rust attribute store export changed length");
        return bytes;
    }
    static const RustAttributeStoreApi* require_api() {
        auto api = rust_attribute_store_api(); if (!api) throw std::runtime_error("Rust attribute store API is not registered"); return api;
    }
    void reset() noexcept { if (rust_) { if (auto api = rust_attribute_store_api()) api->drop_store(rust_); rust_ = nullptr; } }

    void* rust_ = nullptr;
    std::unique_ptr<Fallback> fallback_;
};

static_assert(sizeof(RustAttributeStore) <= 2 * sizeof(void*),
              "Rust-backed attribute store should occupy at most two pointers");
