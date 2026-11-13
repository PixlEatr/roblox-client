#include "rust_world_view.hpp"
#include "rust_world_view_read.hpp"
#include "received_collision_group.hpp"

#include <stdexcept>
#include <utility>

namespace {
using Json = nlohmann::json;
}

RustWorldNodeView::RustWorldNodeView(const RustProjectedWorldViewApi* api, void* node)
    : api_(api), node_(node) {
    if (!api_ || !node_) throw std::runtime_error("invalid Rust world node handle");
}
RustWorldNodeView::~RustWorldNodeView() { if (node_) api_->drop_node(node_); }
RustWorldNodeView::RustWorldNodeView(RustWorldNodeView&& other) noexcept
    : api_(other.api_), node_(std::exchange(other.node_, nullptr)) {
    other.api_ = nullptr;
}
RustWorldNodeView& RustWorldNodeView::operator=(RustWorldNodeView&& other) noexcept {
    if (this == &other) return *this;
    if (node_) api_->drop_node(node_);
    api_ = other.api_;
    node_ = std::exchange(other.node_, nullptr);
    other.api_ = nullptr;
    return *this;
}
Json RustWorldNodeView::header() const {
    return rust_world_view_read::read_json([&](uint8_t* out,size_t cap){return api_->node_header(node_,out,cap);},"node header");
}
std::optional<std::string> RustWorldNodeView::text(uint32_t field) const {
    size_t len=0; int32_t status=api_->node_text(node_,field,nullptr,0,&len);
    if(status<0)throw std::runtime_error("Rust world view rejected node text field");
    if(status==0||status==1)return {};
    if(len>1024*1024)throw std::runtime_error("Rust world node text exceeds limit");
    std::string out(len,'\0');
    status=api_->node_text(node_,field,reinterpret_cast<uint8_t*>(out.data()),out.size(),&len);
    if(status!=2)throw std::runtime_error("Rust world view returned incomplete node text");
    out.resize(len);return out;
}
std::optional<Json> RustWorldNodeView::property(std::string_view key) const {
    auto read=[&](uint8_t* out,size_t cap){return api_->node_property(node_,reinterpret_cast<const uint8_t*>(key.data()),key.size(),out,cap);};
    return rust_world_view_read::read_optional_json(read,"property");
}
std::optional<Json> RustWorldNodeView::section(uint32_t section_id) const {
    auto read=[&](uint8_t* out,size_t cap){return api_->node_section(node_,section_id,out,cap);};
    auto value=rust_world_view_read::read_optional_json(read,"node section",true);
    if(!value)return {};
    const bool valid_collection_tags=section_id==kRustWorldSectionCollectionTags&&value->is_array();
    if(!valid_collection_tags&&!value->is_object())throw std::runtime_error("Rust world view section has an invalid JSON type");
    return value;
}
std::optional<Json> RustWorldNodeView::metadata(uint32_t section_id,std::string_view key) const {
    auto read=[&](uint8_t* out,size_t cap){return api_->node_metadata_field(node_,section_id,reinterpret_cast<const uint8_t*>(key.data()),key.size(),out,cap);};
    return rust_world_view_read::read_optional_json(read,"node metadata");
}

std::shared_ptr<RustWorldNodeView> rust_world_lookup_node(const void* borrowed_view,std::string_view id) {
    if(!borrowed_view)return {};
    auto* api=rust_projected_world_view_api();if(!api)throw std::runtime_error("Rust projected world view API is not registered");
    void* node=api->lookup_node(borrowed_view,reinterpret_cast<const uint8_t*>(id.data()),id.size());
    if(!node)return {};
    RustWorldNodeView owned(api,node);
    return std::make_shared<RustWorldNodeView>(std::move(owned));
}

RustNodeSectionProvider::RustNodeSectionProvider(std::shared_ptr<RustWorldNodeView> node,uint32_t section,bool color_alias)
    :node_(std::move(node)),section_(section),color_alias_(color_alias){}
Json::value_t RustNodeSectionProvider::type() const {return Json::value_t::object;}
Json::size_type RustNodeSectionProvider::size() const {
    if(size_)return *size_;
    auto* api=node_->api();ptrdiff_t count=api->node_section_size(node_->handle(),section_);
    if(count<0) { if(count==-1||count==-2)count=0;else throw std::runtime_error("Rust world section size failed"); }
    auto result=static_cast<Json::size_type>(count);
    if(color_alias_&&section_==kRustWorldSectionOrigins) {
        if(!node_->metadata(section_,"Color"))++result;
    } else if(color_alias_&&section_==kRustWorldSectionPropertyRevisions) {
        if(auto source=node_->metadata(section_,"Color3uint8")) {
            if(!node_->metadata(section_,"Color"))++result;
        }
    }
    return *(size_=result);
}
bool RustNodeSectionProvider::contains(std::string_view key) const {
    if(color_alias_&&key=="Color") {
        if(section_==kRustWorldSectionOrigins)return true;
        if(section_==kRustWorldSectionPropertyRevisions&&node_->metadata(section_,"Color3uint8"))return true;
    }
    return node_->metadata(section_,key).has_value();
}
std::optional<Json> RustNodeSectionProvider::lookup(std::string_view key) const {
    if(color_alias_&&key=="Color") {
        if(section_==kRustWorldSectionOrigins)return Json("derived_received");
        if(section_==kRustWorldSectionPropertyRevisions) {
            if(auto source=node_->metadata(section_,"Color3uint8"))return source;
        }
    }
    return node_->metadata(section_,key);
}
Json RustNodeSectionProvider::materialize() const {
    auto value=node_->section(section_);
    Json result=value?std::move(*value):Json::object();
    if(color_alias_&&section_==kRustWorldSectionOrigins)result["Color"]="derived_received";
    else if(color_alias_&&section_==kRustWorldSectionPropertyRevisions) {
        if(auto source=node_->metadata(section_,"Color3uint8"))result["Color"]=std::move(*source);
    }
    return result;
}

RustNodePropertiesProvider::RustNodePropertiesProvider(std::shared_ptr<RustWorldNodeView> node,bool color_alias)
    :node_(std::move(node)),color_alias_(color_alias){}
Json::value_t RustNodePropertiesProvider::type() const {return Json::value_t::object;}
Json::size_type RustNodePropertiesProvider::size() const {
    if(size_)return *size_;ptrdiff_t count=node_->api()->node_section_size(node_->handle(),0);
    if(count<0){if(count==-1||count==-2)count=0;else throw std::runtime_error("Rust world property count failed");}
    auto result=static_cast<Json::size_type>(count);
    if(color_alias_&&!node_->property("Color")&&node_->property("Color3uint8"))++result;
    return *(size_=result);
}
bool RustNodePropertiesProvider::contains(std::string_view key) const {
    return node_->property(key).has_value()||(color_alias_&&key=="Color"&&node_->property("Color3uint8").has_value());
}
std::optional<Json> RustNodePropertiesProvider::lookup(std::string_view key) const {
    if(auto value=node_->property(key))return value;
    if(color_alias_&&key=="Color")return node_->property("Color3uint8");
    return {};
}
Json RustNodePropertiesProvider::materialize() const {
    auto value=node_->section(0);
    Json result=value?std::move(*value):Json::object();
    if(color_alias_&&!result.contains("Color"))if(auto color=node_->property("Color3uint8"))result["Color"]=std::move(*color);
    return result;
}

RustNodeProviderBundle::SectionAdapter::SectionAdapter(
    RustWorldNodeView* node, uint32_t section)
    : node_(node), section_(section) {}
Json::value_t RustNodeProviderBundle::SectionAdapter::type() const {
    return Json::value_t::object;
}
Json::size_type RustNodeProviderBundle::SectionAdapter::size() const {
    if(size_.has_value())return size_.value();
    auto* api=node_->api();ptrdiff_t count=api->node_section_size(node_->handle(),section_);
    if(count<0) { if(count==-1||count==-2)count=0;else throw std::runtime_error("Rust world section size failed"); }
    auto result=static_cast<Json::size_type>(count);
    if(color_alias_&&section_==kRustWorldSectionOrigins) {
        if(!node_->metadata(section_,"Color"))++result;
    } else if(color_alias_&&section_==kRustWorldSectionPropertyRevisions) {
        if(auto source=node_->metadata(section_,"Color3uint8")) {
            if(!node_->metadata(section_,"Color"))++result;
        }
    }
    if(collision_group_alias_&&section_==kRustWorldSectionOrigins) {
        if(!node_->metadata(section_,"CollisionGroup"))++result;
    } else if(collision_group_alias_&&section_==kRustWorldSectionPropertyRevisions) {
        if(node_->metadata(section_,"CollisionGroupReplicate")&&!node_->metadata(section_,"CollisionGroup"))++result;
    }
    size_.store(result);
    return size_.value_or(result);
}
bool RustNodeProviderBundle::SectionAdapter::contains(std::string_view key) const {
    if(color_alias_&&key=="Color") {
        if(section_==kRustWorldSectionOrigins)return true;
        if(section_==kRustWorldSectionPropertyRevisions&&node_->metadata(section_,"Color3uint8"))return true;
    }
    if(collision_group_alias_&&key=="CollisionGroup") {
        if(section_==kRustWorldSectionOrigins)return true;
        if(section_==kRustWorldSectionPropertyRevisions&&node_->metadata(section_,"CollisionGroupReplicate"))return true;
    }
    return node_->metadata(section_,key).has_value();
}
std::optional<Json> RustNodeProviderBundle::SectionAdapter::lookup(std::string_view key) const {
    if(color_alias_&&key=="Color") {
        if(section_==kRustWorldSectionOrigins)return Json("derived_received");
        if(section_==kRustWorldSectionPropertyRevisions) {
            if(auto source=node_->metadata(section_,"Color3uint8"))return source;
        }
    }
    if(collision_group_alias_&&key=="CollisionGroup") {
        if(section_==kRustWorldSectionOrigins)return Json("derived_received");
        if(section_==kRustWorldSectionPropertyRevisions) {
            if(auto source=node_->metadata(section_,"CollisionGroupReplicate"))return source;
        }
    }
    return node_->metadata(section_,key);
}
Json RustNodeProviderBundle::SectionAdapter::materialize() const {
    auto value=node_->section(section_);
    Json result=value?std::move(*value):Json::object();
    if(color_alias_&&section_==kRustWorldSectionOrigins)result["Color"]="derived_received";
    else if(color_alias_&&section_==kRustWorldSectionPropertyRevisions) {
        if(auto source=node_->metadata(section_,"Color3uint8"))result["Color"]=std::move(*source);
    }
    if(collision_group_alias_&&section_==kRustWorldSectionOrigins)result["CollisionGroup"]="derived_received";
    else if(collision_group_alias_&&section_==kRustWorldSectionPropertyRevisions) {
        if(auto source=node_->metadata(section_,"CollisionGroupReplicate"))result["CollisionGroup"]=std::move(*source);
    }
    return result;
}

RustNodeProviderBundle::PropertiesAdapter::PropertiesAdapter(RustWorldNodeView* node)
    : node_(node) {}
Json::value_t RustNodeProviderBundle::PropertiesAdapter::type() const {
    return Json::value_t::object;
}
Json::size_type RustNodeProviderBundle::PropertiesAdapter::size() const {
    if(size_.has_value())return size_.value();ptrdiff_t count=node_->api()->node_section_size(node_->handle(),0);
    if(count<0){if(count==-1||count==-2)count=0;else throw std::runtime_error("Rust world property count failed");}
    auto result=static_cast<Json::size_type>(count);
    if(color_alias_&&!node_->property("Color")&&node_->property("Color3uint8"))++result;
    if(collision_group_alias_&&!node_->property("CollisionGroup")&&node_->property("CollisionGroupReplicate"))++result;
    size_.store(result);
    return size_.value_or(result);
}
bool RustNodeProviderBundle::PropertiesAdapter::contains(std::string_view key) const {
    return node_->property(key).has_value()
        ||(color_alias_&&key=="Color"&&node_->property("Color3uint8").has_value())
        ||(collision_group_alias_&&key=="CollisionGroup"&&node_->property("CollisionGroupReplicate").has_value());
}
std::optional<Json> RustNodeProviderBundle::PropertiesAdapter::lookup(std::string_view key) const {
    if(auto value=node_->property(key))return value;
    if(color_alias_&&key=="Color")return node_->property("Color3uint8");
    if(collision_group_alias_&&key=="CollisionGroup") {
        if(auto wire=node_->property("CollisionGroupReplicate"))return received_collision_group_value(*wire);
    }
    return {};
}
Json RustNodeProviderBundle::PropertiesAdapter::materialize() const {
    auto value=node_->section(0);
    Json result=value?std::move(*value):Json::object();
    if(color_alias_&&!result.contains("Color"))if(auto color=node_->property("Color3uint8"))result["Color"]=std::move(*color);
    if(collision_group_alias_&&!result.contains("CollisionGroup")) {
        if(auto wire=node_->property("CollisionGroupReplicate"))result["CollisionGroup"]=received_collision_group_value(*wire);
    }
    return result;
}

RustNodeProviderBundle::RustNodeProviderBundle(
    RustWorldNodeView&& owned_node)
    : node_(std::move(owned_node)), properties_(&node_),
      origins_(&node_, kRustWorldSectionOrigins),
      revisions_(&node_, kRustWorldSectionPropertyRevisions) {}

std::shared_ptr<RustWorldNodeView> RustNodeProviderBundle::node_view(
    const std::shared_ptr<RustNodeProviderBundle>& owner) {
    if (!owner) return {};
    return std::shared_ptr<RustWorldNodeView>(owner, &owner->node_);
}
std::shared_ptr<const SharedJsonProvider> RustNodeProviderBundle::properties_provider(
    const std::shared_ptr<RustNodeProviderBundle>& owner) {
    if (!owner) return {};
    return std::shared_ptr<const SharedJsonProvider>(owner, &owner->properties_);
}
std::shared_ptr<const SharedJsonProvider> RustNodeProviderBundle::origins_provider(
    const std::shared_ptr<RustNodeProviderBundle>& owner) {
    if (!owner) return {};
    return std::shared_ptr<const SharedJsonProvider>(owner, &owner->origins_);
}
std::shared_ptr<const SharedJsonProvider> RustNodeProviderBundle::revisions_provider(
    const std::shared_ptr<RustNodeProviderBundle>& owner) {
    if (!owner) return {};
    return std::shared_ptr<const SharedJsonProvider>(owner, &owner->revisions_);
}
void RustNodeProviderBundle::set_color_alias(bool enabled) noexcept {
    properties_.set_color_alias(enabled);
    origins_.set_color_alias(enabled);
    revisions_.set_color_alias(enabled);
}
void RustNodeProviderBundle::set_collision_group_alias(bool enabled) noexcept {
    properties_.set_collision_group_alias(enabled);
    origins_.set_collision_group_alias(enabled);
    revisions_.set_collision_group_alias(enabled);
}

std::shared_ptr<RustNodeProviderBundle> rust_world_lookup_bundle(
    const void* borrowed_view,std::string_view id) {
    if(!borrowed_view)return {};
    auto* api=rust_projected_world_view_api();
    if(!api)throw std::runtime_error("Rust projected world view API is not registered");
    void* node=api->lookup_node(borrowed_view,reinterpret_cast<const uint8_t*>(id.data()),id.size());
    if(!node)return {};
    RustWorldNodeView owned(api,node);
    return std::make_shared<RustNodeProviderBundle>(std::move(owned));
}
