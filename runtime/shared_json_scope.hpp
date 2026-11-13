#pragma once

#include <memory>
#include <unordered_map>

// A request scope bounds decoded JSON caches to one outer host command. The
// registry holds weak references so destroying a SharedJson during a nested
// call is safe. Only SharedJson caches that were actually read are tracked.
struct SharedJsonTransientCache {
    virtual ~SharedJsonTransientCache() = default;
    virtual void clear_transient() noexcept = 0;
};

class SharedJsonScope {
public:
    SharedJsonScope() noexcept { ++depth(); }
    SharedJsonScope(const SharedJsonScope&) = delete;
    SharedJsonScope& operator=(const SharedJsonScope&) = delete;
    ~SharedJsonScope() {
        auto& d=depth();
        if(d==0||--d!=0)return;
        auto& entries=cache_registry();
        for(auto& [_,weak]:entries)if(auto cache=weak.lock())cache->clear_transient();
        entries.clear();
    }
    static void track(const std::shared_ptr<SharedJsonTransientCache>& cache) {
        if(depth()==0)return;
        // Avoid an O(number-of-caches) scan on every field access in large
        // snapshots while still retaining weak ownership only.
        auto& entries=cache_registry();
        auto [it,inserted]=entries.try_emplace(cache.get(),cache);
        if(!inserted&&it->second.expired())it->second=cache;
    }
    static void untrack(const SharedJsonTransientCache* cache) noexcept {
        if(cache)cache_registry().erase(cache);
    }
private:
    static unsigned& depth() noexcept { static thread_local unsigned value=0; return value; }
    static std::unordered_map<const SharedJsonTransientCache*,std::weak_ptr<SharedJsonTransientCache>>& cache_registry() {
        static thread_local std::unordered_map<const SharedJsonTransientCache*,std::weak_ptr<SharedJsonTransientCache>> value;
        return value;
    }
};
