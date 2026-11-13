#pragma once

#include "json.hpp"
#include "rust_engine_bridge.hpp"
#include "shared_json.hpp"

#include <memory>
#include <limits>
#include <optional>
#include <string>
#include <string_view>

// Cache common small JSON sizes inline. Counts at or above UINT32_MAX remain
// uncached so the complete size_t result is always returned without truncation.
class CompactJsonSizeCache final {
public:
    using Size = nlohmann::json::size_type;
    static constexpr uint32_t kUncached = std::numeric_limits<uint32_t>::max();

    bool has_value() const noexcept { return value_ != kUncached; }
    Size value() const noexcept { return static_cast<Size>(value_); }
    Size value_or(Size uncached) const noexcept {
        return has_value() ? value() : uncached;
    }
    void store(Size value) noexcept {
        value_ = value < static_cast<Size>(kUncached)
            ? static_cast<uint32_t>(value)
            : kUncached;
    }
    void reset() noexcept { value_ = kUncached; }

private:
    uint32_t value_ = kUncached;
};

// Owns one Arc<NodeRecord> clone. It deliberately does not keep a WorldView
// alive, so replacing a frame cannot pin every node from the previous frame.
class RustWorldNodeView {
public:
    RustWorldNodeView(const RustProjectedWorldViewApi* api, void* owned_node);
    ~RustWorldNodeView();
    RustWorldNodeView(const RustWorldNodeView&) = delete;
    RustWorldNodeView& operator=(const RustWorldNodeView&) = delete;
    RustWorldNodeView(RustWorldNodeView&& other) noexcept;
    RustWorldNodeView& operator=(RustWorldNodeView&& other) noexcept;

    const RustProjectedWorldViewApi* api() const noexcept { return api_; }
    const void* handle() const noexcept { return node_; }
    nlohmann::json header() const;
    std::optional<std::string> text(uint32_t field) const;
    std::optional<nlohmann::json> property(std::string_view key) const;
    std::optional<nlohmann::json> section(uint32_t section) const;
    std::optional<nlohmann::json> metadata(uint32_t section, std::string_view key) const;

private:
    const RustProjectedWorldViewApi* api_;
    void* node_;
};

std::shared_ptr<RustWorldNodeView> rust_world_lookup_node(const void* borrowed_view,
                                                          std::string_view id);

// One allocation owns the immutable Rust record wrapper and all three
// provider adapters. SharedJson receives aliasing shared_ptrs to the embedded
// adapters, so any copied provider keeps the complete bundle alive.
class RustNodeProviderBundle final {
public:
    explicit RustNodeProviderBundle(RustWorldNodeView&& owned_node);
    // Embedded adapters borrow node_'s address for the bundle's entire life.
    RustNodeProviderBundle(const RustNodeProviderBundle&) = delete;
    RustNodeProviderBundle& operator=(const RustNodeProviderBundle&) = delete;
    RustNodeProviderBundle(RustNodeProviderBundle&&) = delete;
    RustNodeProviderBundle& operator=(RustNodeProviderBundle&&) = delete;
    RustWorldNodeView& node() noexcept { return node_; }
    // Factories take the owner explicitly and derive the alias target from that
    // same owner, avoiding shared_from_this and owner/this mismatches.
    static std::shared_ptr<RustWorldNodeView> node_view(
        const std::shared_ptr<RustNodeProviderBundle>& owner);
    static std::shared_ptr<const SharedJsonProvider> properties_provider(
        const std::shared_ptr<RustNodeProviderBundle>& owner);
    static std::shared_ptr<const SharedJsonProvider> origins_provider(
        const std::shared_ptr<RustNodeProviderBundle>& owner);
    static std::shared_ptr<const SharedJsonProvider> revisions_provider(
        const std::shared_ptr<RustNodeProviderBundle>& owner);
    void set_color_alias(bool enabled) noexcept;
    void set_collision_group_alias(bool enabled) noexcept;

private:
    class SectionAdapter final : public SharedJsonProvider {
    public:
        SectionAdapter(RustWorldNodeView* node, uint32_t section);
        Json::value_t type() const override;
        Json::size_type size() const override;
        bool contains(std::string_view key) const override;
        std::optional<Json> lookup(std::string_view key) const override;
        Json materialize() const override;
        void set_color_alias(bool enabled) noexcept {
            if (color_alias_ != enabled) { color_alias_ = enabled; size_.reset(); }
        }
        void set_collision_group_alias(bool enabled) noexcept {
            if (collision_group_alias_ != enabled) { collision_group_alias_ = enabled; size_.reset(); }
        }
    private:
        RustWorldNodeView* node_;
        uint32_t section_;
        bool color_alias_ = false;
        bool collision_group_alias_ = false;
        mutable CompactJsonSizeCache size_;
    };
    class PropertiesAdapter final : public SharedJsonProvider {
    public:
        explicit PropertiesAdapter(RustWorldNodeView* node);
        Json::value_t type() const override;
        Json::size_type size() const override;
        bool contains(std::string_view key) const override;
        std::optional<Json> lookup(std::string_view key) const override;
        Json materialize() const override;
        void set_color_alias(bool enabled) noexcept {
            if (color_alias_ != enabled) { color_alias_ = enabled; size_.reset(); }
        }
        void set_collision_group_alias(bool enabled) noexcept {
            if (collision_group_alias_ != enabled) { collision_group_alias_ = enabled; size_.reset(); }
        }
    private:
        RustWorldNodeView* node_;
        bool color_alias_ = false;
        bool collision_group_alias_ = false;
        mutable CompactJsonSizeCache size_;
    };

    RustWorldNodeView node_;
    PropertiesAdapter properties_;
    SectionAdapter origins_;
    SectionAdapter revisions_;
};

std::shared_ptr<RustNodeProviderBundle> rust_world_lookup_bundle(
    const void* borrowed_view, std::string_view id);

class RustNodeSectionProvider final : public SharedJsonProvider {
public:
    // Enable only after descriptor/source validation for this immutable node.
    // A missing source revision leaves any existing Color revision intact.
    RustNodeSectionProvider(std::shared_ptr<RustWorldNodeView> node, uint32_t section,
                            bool color_alias = false);
    Json::value_t type() const override;
    Json::size_type size() const override;
    bool contains(std::string_view key) const override;
    std::optional<Json> lookup(std::string_view key) const override;
    Json materialize() const override;
private:
    std::shared_ptr<RustWorldNodeView> node_;
    uint32_t section_;
    bool color_alias_;
    // Section counts are asked for by the legacy JSON interface. The count is
    // retained, but the section's property tree is not.
    mutable std::optional<Json::size_type> size_;
};

class RustNodePropertiesProvider final : public SharedJsonProvider {
public:
    // The received-state binder validates an absent Color plus Color3uint8
    // before enabling this alias; no derived JSON value is retained here.
    explicit RustNodePropertiesProvider(std::shared_ptr<RustWorldNodeView> node,
                                        bool color_alias = false);
    Json::value_t type() const override;
    Json::size_type size() const override;
    bool contains(std::string_view key) const override;
    std::optional<Json> lookup(std::string_view key) const override;
    Json materialize() const override;
private:
    std::shared_ptr<RustWorldNodeView> node_;
    bool color_alias_;
    mutable std::optional<Json::size_type> size_;
};
