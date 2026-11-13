#pragma once

#include "json.hpp"
#include "shared_json_scope.hpp"

#include <initializer_list>
#include <algorithm>
#include <memory>
#include <map>
#include <list>
#include <optional>
#include <set>
#include <string_view>
#include <type_traits>
#include <variant>
#include <stdexcept>
#include <string>
#include <utility>
#include <unordered_map>
#include <vector>

// Generic immutable backing for values projected from another owner (for
// example a Rust-owned node). The provider owns its opaque handle and exposes
// only immutable JSON reads. Implementations must return independent Json
// values from lookup/materialize; SharedJson controls their cache lifetime.
class SharedJsonProvider {
public:
    using Json = nlohmann::json;
    virtual ~SharedJsonProvider() = default;
    virtual Json::value_t type() const = 0;
    virtual Json::size_type size() const = 0;
    virtual bool contains(std::string_view key) const = 0;
    virtual std::optional<Json> lookup(std::string_view key) const = 0;
    virtual std::optional<Json> lookup(Json::size_type index) const { (void)index; return {}; }
    virtual Json materialize() const = 0;
};

// JSON COW holder for immutable Actor snapshot data. Sparse key writes live in
// a private overlay; explicit whole-Json mutable access detaches as before.
class SharedJson {
public:
    using Json = nlohmann::json;

private:
    struct Sparse {std::map<std::string,Json> overlay;std::set<std::string> deleted;};
    struct BaseState {std::shared_ptr<const Json> base;std::unique_ptr<Json> local;};
    struct ProviderState {std::shared_ptr<const SharedJsonProvider> provider;std::unique_ptr<Sparse> sparse;};
    using State = std::variant<std::monostate,std::unique_ptr<Json>,BaseState,ProviderState>;

public:

    static std::size_t state_layout_bytes() noexcept { return sizeof(State); }

    SharedJson() : state_(BaseState{empty_base(),nullptr}) {}
    // A value already owned by this holder does not need an immutable copy
    // followed by another copy on its first write. Only explicit snapshot
    // aliases use shared backing; copying owned values remains a deep copy.
    explicit SharedJson(Json value)
        : state_(std::in_place_type<std::unique_ptr<Json>>, std::make_unique<Json>(std::move(value))) {}
    explicit SharedJson(std::shared_ptr<const Json> immutable_base)
        : state_(BaseState{immutable_base ? std::move(immutable_base) : empty_base(),nullptr}) {}
    explicit SharedJson(std::shared_ptr<const SharedJsonProvider> provider)
        : state_(ProviderState{std::move(provider),nullptr}) {}

    // Most replicated nodes have no local attributes, origins, or revisions.
    // Share their empty map until the first write, while the ordinary default
    // constructor remains JSON null (notably for absent script code).
    static SharedJson object() {
        static const auto empty = std::make_shared<const Json>(Json::object());
        return SharedJson(empty);
    }

    SharedJson(const SharedJson& other) : state_(clone_state(other.state_)) {}
    SharedJson& operator=(const SharedJson& other) {
        if (this == &other) return *this;
        // Allocate before changing the destination so a failed copy retains
        // its previous value, including when it still has shared backing.
        State copied_state=clone_state(other.state_);
        state_=std::move(copied_state);
        cache_.reset();
        return *this;
    }
    SharedJson(SharedJson&&) noexcept = default;
    SharedJson& operator=(SharedJson&&) noexcept = default;

    SharedJson& operator=(Json value) { replace(std::move(value)); return *this; }
    SharedJson& operator=(Json::initializer_list_t values) {
        // Use nlohmann's own initializer-list type. A list<Json> is treated
        // as a generic array container, even for {key, value} object entries.
        // Empty assignment matches `Json value; value = {};` (JSON null).
        replace(values.size() == 0 ? Json() : Json(values)); return *this;
    }

    const Json& read() const {
        if (const auto* local=local_json()) return *local;
        const auto* provider=provider_ptr();
        if (!provider) {const auto* base=base_ptr();return base?*base:*empty_base();}
        touch_cache();
        if (cache_->whole_versions.empty() || cache_->whole_dirty) {
            Json value=provider->materialize();
            if (value.is_object()) {
                if(const auto* sparse=sparse_state()){for(const auto& key:sparse->deleted)value.erase(key);for(const auto& [key,item]:sparse->overlay)value[key]=item;}
            }
            cache_->whole_versions.emplace_back(std::move(value));
            cache_->whole_dirty=false;
        }
        return cache_->whole_versions.back();
    }
    Json& write() {
        if (!local_json()) {
            auto owned=std::make_unique<Json>(read());
            if(std::holds_alternative<BaseState>(state_)) {
                std::get<BaseState>(state_).local=std::move(owned);
            } else {
                state_.emplace<std::unique_ptr<Json>>(std::move(owned));
            }
        }
        return *local_json();
    }
    operator const Json&() const { return read(); }
    operator Json&() { return write(); }
    // nlohmann otherwise recognizes the forwarded iterators as a generic
    // array container, losing object keys when a field is exported to JSON.
    friend void to_json(Json& destination, const SharedJson& value) {
        destination = value.read();
    }
    const Json* operator->() const { return &read(); }
    Json* operator->() { return &write(); }

    const Json& at(const std::string& key) const { if(provider_ptr()&&!local_json()){const auto* v=lookup(key);if(!v)throw std::out_of_range("key not found");return *v;}return read().at(key); }
    Json& at(const std::string& key) { if(provider_ptr()&&!local_json()){if(!contains(key))throw std::out_of_range("key not found");return mutable_key(key);}return write().at(key); }
    const Json& at(Json::size_type index) const { return read().at(index); }
    Json& at(Json::size_type index) { return write().at(index); }
    const Json& operator[](const std::string& key) const { if(provider_ptr()&&!local_json()){const auto* v=lookup(key);if(!v)throw std::out_of_range("key not found");return *v;}return read()[key]; }
    Json& operator[](const std::string& key) { return provider_ptr()&&!local_json()?mutable_key(key):write()[key]; }
    const Json& operator[](const char* key) const { return (*this)[std::string(key)]; }
    Json& operator[](const char* key) { return (*this)[std::string(key)]; }
    const Json& operator[](Json::size_type index) const { return read()[index]; }
    Json& operator[](Json::size_type index) { return write()[index]; }

    template<bool IsConst> class BasicIterator {
        using Owner=std::conditional_t<IsConst,const SharedJson,SharedJson>;
        using JIter=std::conditional_t<IsConst,typename Json::const_iterator,typename Json::iterator>;
        using OIter=std::conditional_t<IsConst,typename std::map<std::string,Json>::const_iterator,typename std::map<std::string,Json>::iterator>;
        using Variant=std::variant<std::monostate,JIter,OIter>;
    public:
        BasicIterator()=default;
        static BasicIterator sentinel(Owner* owner){BasicIterator out;out.owner_=owner;out.is_end_=true;return out;}
        static BasicIterator whole(Owner* owner,JIter it){BasicIterator out;out.owner_=owner;out.inner_=it;out.mode_=1;out.is_end_=false;return out;}
        static BasicIterator sparse(Owner* owner,OIter it){BasicIterator out;out.owner_=owner;out.inner_=it;out.mode_=2;out.is_end_=false;return out;}
        static BasicIterator cached(Owner* owner,OIter it){BasicIterator out;out.owner_=owner;out.inner_=it;out.mode_=3;out.is_end_=false;return out;}
        decltype(auto) operator*() const {return mode_==1?**std::get_if<JIter>(&inner_):std::get<OIter>(inner_)->second;}
        auto operator->() const {return &operator*();}
        auto& value() const {return mode_==1?std::get<JIter>(inner_).value():std::get<OIter>(inner_)->second;}
        auto key() const {return mode_==1?std::get<JIter>(inner_).key():std::get<OIter>(inner_)->first;}
        BasicIterator& operator++(){if(mode_==1){auto& i=std::get<JIter>(inner_);++i;if(i==owner_->whole_end())is_end_=true;}else if(mode_==2){auto& i=std::get<OIter>(inner_);++i;if(i==owner_->sparse_state()->overlay.end())is_end_=true;}else if(mode_==3){auto& i=std::get<OIter>(inner_);++i;if(i==owner_->cache_->keys.end())is_end_=true;}return *this;}
        bool operator==(const BasicIterator& other) const {
            if(owner_!=other.owner_)return false;
            if(is_end_&&other.is_end_)return true;
            if(is_end_)return other.at_end();
            if(other.is_end_)return at_end();
            if(mode_!=other.mode_)return false;
            return mode_==1?std::get<JIter>(inner_)==std::get<JIter>(other.inner_):std::get<OIter>(inner_)==std::get<OIter>(other.inner_);
        }
        bool operator!=(const BasicIterator& other) const{return !(*this==other);}
    private:
        bool at_end() const {if(mode_==1)return std::get<JIter>(inner_)==owner_->whole_end();if(mode_==2)return std::get<OIter>(inner_)==owner_->sparse_state()->overlay.end();if(mode_==3)return std::get<OIter>(inner_)==owner_->cache_->keys.end();return true;}
        Owner* owner_=nullptr; Variant inner_{}; int mode_=0; bool is_end_=true;
    };
    using iterator=BasicIterator<false>; using const_iterator=BasicIterator<true>;
    iterator begin(){auto& json=write();return json.empty()?iterator::sentinel(this):iterator::whole(this,json.begin());}
    const_iterator begin() const {const auto& json=read();return json.empty()?const_iterator::sentinel(this):const_iterator::whole(this,json.begin());}
    iterator end(){return iterator::sentinel(this);} const_iterator end() const{return const_iterator::sentinel(this);}
    iterator find(const std::string& key) {
        if(auto* local=local_json()){auto it=local->find(key);return it==local->end()?iterator::sentinel(this):iterator::whole(this,it);}
        if(auto* provider=provider_ptr();provider&&provider->type()==Json::value_t::object){if(!contains(key))return iterator::sentinel(this);auto& value=mutable_key(key);(void)value;return iterator::sparse(this,sparse_state()->overlay.find(key));}
        auto& json=write();auto it=json.find(key);return it==json.end()?iterator::sentinel(this):iterator::whole(this,it);
    }
    const_iterator find(const std::string& key) const {
        if(auto* local=local_json()){auto it=local->find(key);return it==local->end()?const_iterator::sentinel(this):const_iterator::whole(this,it);}
        if(auto* provider=provider_ptr();provider&&provider->type()==Json::value_t::object){if(auto* sparse=sparse_state())if(auto it=sparse->overlay.find(key);it!=sparse->overlay.end())return const_iterator::sparse(this,it);if(!lookup(key))return const_iterator::sentinel(this);return const_iterator::cached(this,cache_->keys.find(key));}
        auto it=read().find(key);return it==read().end()?const_iterator::sentinel(this):const_iterator::whole(this,it);
    }
    auto erase(const std::string& key) { return write().erase(key); }
    template<class T> bool contains(T&& key) const {
        auto* provider=provider_ptr();
        if(local_json()||!provider||provider->type()!=Json::value_t::object)return read().contains(std::forward<T>(key));
        if constexpr(std::is_convertible_v<T,std::string_view>){std::string_view k(std::forward<T>(key));std::string owned(k);if(auto* sparse=sparse_state();sparse&&sparse->deleted.count(owned))return false;if(auto* sparse=sparse_state();sparse&&sparse->overlay.count(owned))return true;return provider->contains(k);}
        else return read().contains(std::forward<T>(key));
    }
    const Json* lookup(const std::string& key) const {
        if(auto* local=local_json()){auto it=local->find(key);return it==local->end()?nullptr:&it.value();}
        auto* provider=provider_ptr();
        if(!provider){const Json& value=read();if(!value.is_object())return nullptr;auto it=value.find(key);return it==value.end()?nullptr:&it.value();}
        if(auto* sparse=sparse_state();sparse&&sparse->deleted.count(key))return nullptr;
        if(auto* sparse=sparse_state())if(auto it=sparse->overlay.find(key);it!=sparse->overlay.end())return &it->second;
        touch_cache();if(auto it=cache_->keys.find(key);it!=cache_->keys.end())return &it->second;
        auto value=provider->lookup(key);if(!value)return nullptr;
        return &cache_->keys.emplace(key,std::move(*value)).first->second;
    }
    Json& mutable_key(const std::string& key) {
        if(auto* local=local_json())return (*local)[key];
        auto* provider=provider_ptr();
        if(!provider||provider->type()!=Json::value_t::object)return write()[key];
        if(auto* sparse=sparse_state())if(auto it=sparse->overlay.find(key);it!=sparse->overlay.end()){if(cache_)cache_->whole_dirty=true;return it->second;}
        Json value=nullptr;if(const auto* existing=lookup(key))value=*existing;
        auto& overlay=ensure_sparse();overlay.deleted.erase(key);if(cache_)cache_->whole_dirty=true;return overlay.overlay.emplace(key,std::move(value)).first->second;
    }
    template<class T> auto value(T&& key, const char* fallback) const {
        if constexpr(std::is_convertible_v<T,std::string_view>){auto* v=lookup(std::string(std::string_view(std::forward<T>(key))));return v?v->template get<std::string>():std::string(fallback);}
        else return read().value(std::forward<T>(key),fallback);
    }
    template<class T, class U> auto value(T&& key, U&& fallback) const {
        if constexpr(std::is_convertible_v<T,std::string_view>){auto* v=lookup(std::string(std::string_view(std::forward<T>(key))));return v?v->template get<std::decay_t<U>>():std::decay_t<U>(std::forward<U>(fallback));}
        else return read().value(std::forward<T>(key), std::forward<U>(fallback));
    }
    bool is_object() const noexcept { auto* p=provider_ptr();return p&&!local_json()?p->type()==Json::value_t::object:read().is_object(); }
    bool is_array() const noexcept { auto* p=provider_ptr();return p&&!local_json()?p->type()==Json::value_t::array:read().is_array(); }
    bool is_string() const noexcept { auto* p=provider_ptr();return p&&!local_json()?p->type()==Json::value_t::string:read().is_string(); }
    bool is_null() const noexcept { auto* p=provider_ptr();return p&&!local_json()?p->type()==Json::value_t::null:read().is_null(); }
    bool is_boolean() const noexcept { auto* p=provider_ptr();return p&&!local_json()?p->type()==Json::value_t::boolean:read().is_boolean(); }
    bool empty() const { return size()==0; }
    Json::size_type size() const {
        auto* provider=provider_ptr();
        if(local_json()||!provider)return read().size();
        if(provider->type()!=Json::value_t::object)return provider->size();
        auto count=provider->size();if(auto* sparse=sparse_state()){for(const auto& key:sparse->deleted)if(provider->contains(key))--count;for(const auto& [key,_]:sparse->overlay)if(!provider->contains(key))++count;}
        return count;
    }
    template<class T> T get() const { return read().template get<T>(); }
    template<class T> T& get_ref() { return write().template get_ref<T>(); }
    template<class T> const T& get_ref() const { return read().template get_ref<T>(); }
    std::string dump(int indent = -1) const { return read().dump(indent); }
    bool operator==(const SharedJson& other) const { return read() == other.read(); }
    bool operator!=(const SharedJson& other) const { return !(*this == other); }
    bool operator==(const Json& other) const { return read() == other; }
    bool operator!=(const Json& other) const { return read() != other; }
    friend bool operator==(const Json& a, const SharedJson& b) { return a == b.read(); }
    friend bool operator!=(const Json& a, const SharedJson& b) { return a != b.read(); }

    void replace(Json value) {
        State replacement(std::in_place_type<std::unique_ptr<Json>>,std::make_unique<Json>(std::move(value)));
        cache_.reset();
        state_=std::move(replacement);
    }
    void reset_to(std::shared_ptr<const Json> immutable_base) {
        State replacement(BaseState{immutable_base ? std::move(immutable_base) : empty_base(),nullptr});
        cache_.reset();state_=std::move(replacement);
    }
    void reset_to(std::shared_ptr<const SharedJsonProvider> provider) {
        State replacement(ProviderState{std::move(provider),nullptr});
        cache_.reset();state_=std::move(replacement);
    }
    bool is_materialized() const noexcept { return local_json()!=nullptr; }
    // Call only after any references/iterators borrowed from this holder have
    // expired (for example once a parsed node is fully installed).
    void clear_transient_cache() const noexcept {
        SharedJsonScope::untrack(cache_.get());
        cache_.reset();
    }
    long shared_base_owners() const noexcept {
        if(const auto* state=std::get_if<BaseState>(&state_))return state->base?state->base.use_count():0;
        return 0;
    }

private:
    struct Cache final : SharedJsonTransientCache {
        std::map<std::string,Json> keys;
        std::list<Json> whole_versions;
        bool whole_dirty=true;
        void clear_transient() noexcept override {keys.clear();whole_versions.clear();whole_dirty=true;}
    };
    static State clone_state(const State& source) {
        if(const auto* owned=std::get_if<std::unique_ptr<Json>>(&source)) {
            std::unique_ptr<Json> local;
            if(*owned)local=std::make_unique<Json>(**owned);
            return State(std::in_place_type<std::unique_ptr<Json>>,std::move(local));
        }
        if(const auto* base=std::get_if<BaseState>(&source)) {
            std::unique_ptr<Json> local;
            if(base->local)local=std::make_unique<Json>(*base->local);
            return State(BaseState{base->base,std::move(local)});
        }
        if(const auto* provider=std::get_if<ProviderState>(&source)) {
            std::unique_ptr<Sparse> sparse;
            if(provider->sparse)sparse=std::make_unique<Sparse>(*provider->sparse);
            return State(ProviderState{provider->provider,std::move(sparse)});
        }
        return State(std::monostate{});
    }
    Sparse* sparse_state() noexcept {auto* s=std::get_if<ProviderState>(&state_);return s&&s->sparse?s->sparse.get():nullptr;}
    const Sparse* sparse_state() const noexcept {const auto* s=std::get_if<ProviderState>(&state_);return s&&s->sparse?s->sparse.get():nullptr;}
    Json* local_json() noexcept {
        if(auto* owned=std::get_if<std::unique_ptr<Json>>(&state_))return owned->get();
        auto* base=std::get_if<BaseState>(&state_);return base&&base->local?base->local.get():nullptr;
    }
    const Json* local_json() const noexcept {
        if(const auto* owned=std::get_if<std::unique_ptr<Json>>(&state_))return owned->get();
        const auto* base=std::get_if<BaseState>(&state_);return base&&base->local?base->local.get():nullptr;
    }
    const Json* base_ptr() const noexcept {const auto* b=std::get_if<BaseState>(&state_);return b&&b->base?b->base.get():nullptr;}
    const SharedJsonProvider* provider_ptr() const noexcept {const auto* p=std::get_if<ProviderState>(&state_);return p&&p->provider?p->provider.get():nullptr;}
    Sparse& ensure_sparse(){
        // mutable_key reaches this only after confirming a provider object.
        auto& provider=std::get<ProviderState>(state_);
        if(!provider.sparse)provider.sparse=std::make_unique<Sparse>();
        return *provider.sparse;
    }
    void touch_cache() const {if(!cache_)cache_=std::make_shared<Cache>();SharedJsonScope::track(cache_);}
    auto whole_end(){return write().end();}
    auto whole_end() const {return read().end();}
    static const std::shared_ptr<const Json>& empty_base() {
        static const auto value = std::make_shared<const Json>(Json());
        return value;
    }
    State state_;
    mutable std::shared_ptr<Cache> cache_;
};

// Received provenance maps repeat across thousands of instances. Intern only
// immutable, equal values; SharedJson materializes private storage on mutation.
// Weak entries and a bucket cap prevent the pool from retaining old worlds or
// growing indefinitely when the server supplies many distinct field layouts.
class SharedJsonPool {
public:
    SharedJson intern(const nlohmann::json& value) {
        const size_t key=std::hash<nlohmann::json>{}(value);
        auto found=entries_.find(key);
        if(found==entries_.end()) {
            if(entries_.size()>=256) {
                for(auto it=entries_.begin();it!=entries_.end();) {
                    auto& bucket=it->second;
                    bucket.erase(std::remove_if(bucket.begin(),bucket.end(),
                        [](const auto& entry){return entry.expired();}),bucket.end());
                    if(bucket.empty())it=entries_.erase(it);else ++it;
                }
                if(entries_.size()>=256)return SharedJson(value);
            }
            found=entries_.emplace(key,Bucket{}).first;
        }
        auto& bucket=found->second;
        for(auto it=bucket.begin();it!=bucket.end();) {
            if(auto shared=it->lock()) {
                if(*shared==value)return SharedJson(std::move(shared));
                ++it;
            } else it=bucket.erase(it);
        }
        // A collision must never imply equality or defeat the memory bound.
        if(bucket.size()>=8)return SharedJson(value);
        auto shared=std::make_shared<const nlohmann::json>(value);
        bucket.emplace_back(shared);
        return SharedJson(std::move(shared));
    }
private:
    using Bucket=std::vector<std::weak_ptr<const nlohmann::json>>;
    std::unordered_map<size_t,Bucket> entries_;
};
