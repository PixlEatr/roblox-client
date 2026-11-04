// Legacy Region3 queries keep their defaults/list conversion separate from
// OverlapParams. Both families traverse the same current world and filters.
bool world_region_method(const std::string& name) {
    return name=="FindPartsInRegion3"||name=="findPartsInRegion3"||
        name=="FindPartsInRegion3WithIgnoreList"||name=="FindPartsInRegion3WithWhiteList"||
        name=="IsRegion3Empty"||name=="IsRegion3EmptyWithIgnoreList";
}
std::vector<std::string> legacy_query_filter_list(lua_State* L,World& w,int index) {
    if(!lua_istable(L,index))throw std::runtime_error("legacy query filter requires an array of Instances");
    index=lua_absindex(L,index);std::vector<std::string> result;
    // Historical Instances arguments consume the array prefix up to its first
    // nil. Do not reuse the modern, deliberately dense filter-array converter.
    for(int i=1;i<=4097;++i) {
        lua_rawgeti(L,index,i);
        if(lua_isnil(L,-1)){lua_pop(L,1);return result;}
        if(i>4096)throw std::runtime_error("legacy query filter limit exceeded");
        if(!has_meta(L,-1,kInstanceMeta))throw std::runtime_error("legacy query filter requires Instances");
        const auto* instance=static_cast<Handle*>(lua_touserdata(L,-1));
        if(instance->world!=&w)throw std::runtime_error("legacy query filter belongs to another world");
        result.push_back(instance->node->id);lua_pop(L,1);
    }
    return result;
}
bool region_intersects_aabb(const FrameVector& low,const FrameVector& high,
                            const CFrameValue& pose,const FrameVector& half) {
    for(int axis=0;axis<3;++axis) {
        double extent=0;
        for(int k=0;k<3;++k)extent+=std::abs(pose.r[axis*3+k])*half[k];
        if(pose.p[axis]+extent<low[axis]||pose.p[axis]-extent>high[axis])return false;
    }
    return true;
}
int world_root_region(lua_State* L) {
    const auto* h=checked_handle(L,1);const std::string method=lua_tostring(L,lua_upvalueindex(1));
    try {
        if(h->node->destroyed||!collision_world_root(*h->node))throw std::runtime_error("region query requires a live WorldRoot");
        const bool empty=method=="IsRegion3Empty"||method=="IsRegion3EmptyWithIgnoreList";
        const bool include=method=="FindPartsInRegion3WithWhiteList";
        const bool list=include||method=="FindPartsInRegion3WithIgnoreList"||method=="IsRegion3EmptyWithIgnoreList";
        if(lua_gettop(L)<(list?3:2)||lua_gettop(L)>(empty?3:4))
            throw std::runtime_error("region query expects Region3, filter, and optional maxParts for result lists");
        FrameVector low{},high{};
        if(!spatial_values::read_region_bounds(L,2,low,high))throw std::runtime_error("region query requires Region3");
        for(int axis=0;axis<3;++axis) {
            if(!std::isfinite(low[axis])||!std::isfinite(high[axis])||low[axis]>high[axis])
                throw std::runtime_error("region query requires finite ordered bounds");
            if(empty) {
                // Creator documentation describes IsRegion3Empty as an AABB
                // check over 4-stud grid cells, possibly larger than Region3.
                low[axis]=std::floor(low[axis]/4)*4;
                high[axis]=std::ceil(high[axis]/4)*4;
            }
        }
        auto& w=*h->world;WorldRayFilter filter;
        if(list) {
            auto entries=legacy_query_filter_list(L,w,3);
            if(include){filter.include_active=true;filter.include=std::move(entries);}
            else filter.exclude=std::move(entries);
        } else if(!lua_isnoneornil(L,3)) {
            if(!has_meta(L,3,kInstanceMeta))throw std::runtime_error("ignoreDescendantsInstance requires an Instance or nil");
            const auto* instance=static_cast<Handle*>(lua_touserdata(L,3));
            if(instance->world!=&w)throw std::runtime_error("ignored Instance belongs to another world");
            filter.exclude.push_back(instance->node->id);
        }
        size_t limit=empty?1:20;bool no_results=false;
        if(!empty&&!lua_isnoneornil(L,4)) {
            if(lua_type(L,4)!=LUA_TNUMBER)throw std::runtime_error("maxParts requires a number");
            const double value=lua_tonumber(L,4);
            // Positive infinity is the long-standing unlimited legacy idiom;
            // unlike OverlapParams, an explicit zero requests no results.
            if(value==std::numeric_limits<double>::infinity())limit=0;
            else {
                if(!std::isfinite(value)||value<0||value>INT32_MAX||std::floor(value)!=value)
                    throw std::runtime_error("maxParts requires a nonnegative integer or positive infinity");
                limit=size_t(value);no_results=limit==0;
            }
        }
        std::vector<Node*> result;
        if(!no_results)result=query_world_bounds(w,*h->node,filter,limit,
            [&](const CFrameValue& pose,const FrameVector& half){return region_intersects_aabb(low,high,pose,half);});
        if(empty)lua_pushboolean(L,result.empty());else push_bounds_results(L,w,result);
        return 1;
    }catch(const std::exception& e){return luaL_error(L,"%s: %s",method.c_str(),e.what()),0;}
}
