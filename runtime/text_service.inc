// Ordinary local font service requests. No authentication or game protocol.
int text_service_continue(lua_State* L,int) {
    if(lua_type(L,1)!=LUA_TBOOLEAN){luaL_error(L,"TextService resumed without a host result");return 0;}
    const bool ok=lua_toboolean(L,1);lua_remove(L,1);
    if(!ok){if(lua_gettop(L)==0)lua_pushliteral(L,"TextService request failed");lua_error(L);return 0;}
    return lua_gettop(L);
}
int text_service_request(lua_State* L) {
    auto* h=checked_handle(L,1);World& w=*h->world;
    if(h->node->class_name!="TextService"||h->node->destroyed){luaL_error(L,"TextService receiver required");return 0;}
    const bool bounds=lua_toboolean(L,lua_upvalueindex(1));
    try {
        if(lua_gettop(L)!=2)throw std::runtime_error("TextService method expects one argument");
        Json request{{"kind",bounds?"bounds":"family"}};
        if(!bounds) {
            if(lua_type(L,2)!=LUA_TSTRING)throw std::runtime_error("font family must be a content string");
            size_t length=0;const char* family=lua_tolstring(L,2,&length);
            if(length==0||length>512||std::memchr(family,0,length))throw std::runtime_error("invalid font family reference");
            request["family"]=std::string(family,length);
        }else {
            auto* parameters=checked_handle(L,2);
            if(parameters->world!=&w||parameters->node->destroyed||parameters->node->class_name!="GetTextBoundsParams")throw std::runtime_error("GetTextBoundsParams instance required");
            auto& p=*parameters->node;
            auto required=[&](const char* key)->const Json&{const auto* v=property_value(w,p,key);if(!v)throw std::runtime_error(std::string("text bounds parameter unavailable: ")+key);return *v;};
            const auto& font=required("Font");authored_font_to_enum(font);request["font"]=font.at("$font");
            const auto& text=required("Text");
            if(!text.is_string()||text.get_ref<const std::string&>().size()>4096)throw std::runtime_error("text bounds supports at most 4096 UTF-8 bytes");
            const auto& string=text.get_ref<const std::string&>();
            if(!valid_utf8(string.data(),string.size())||std::memchr(string.data(),0,string.size()))throw std::runtime_error("text must be valid UTF-8 without NUL");
            request["text"]=text;
            const auto& size=required("Size");const auto& width=required("Width");const auto& rich=required("RichText");
            if(!size.is_number()||!std::isfinite(size.get<double>())||size<1||size>1000)throw std::runtime_error("text measurement Size must be in 1..1000");
            if(!width.is_number()||!std::isfinite(width.get<double>())||width<0||width>32768)throw std::runtime_error("text measurement Width must be in 0..32768");
            if(!rich.is_boolean())throw std::runtime_error("RichText must be a boolean");
            if(rich.get<bool>()) {
                if(auto document=rich_text::parse(string)) {
                    request["text"]=std::move(document->text);
                    request["runs"]=std::move(document->runs);
                }
            }
            request["size"]=size;request["width"]=width;
        }
        const auto key=request.dump();auto cached=w.text_service_cache.find(key);
        if(cached!=w.text_service_cache.end()){push_json(L,w,cached->second);return 1;}
        if(!w.persistent||!w.scheduler||!lua_isyieldable(L))throw std::runtime_error("uncached TextService requests require a yieldable persistent worker");
        if(w.text_service_waiters.size()>=32||w.text_service_requests.size()>=32)throw std::runtime_error("TextService pending request limit exceeded");
        const uint64_t id=w.next_text_service_request++;request["request_id"]=id;
        w.scheduler->wait_signal(L);lua_pushthread(L);int reference=lua_ref(L,-1);lua_pop(L,1);
        w.text_service_waiters.push_back({L,reference,id,key,bounds?"bounds":"family",std::chrono::steady_clock::now()+std::chrono::seconds(20)});
        w.text_service_requests.push_back(std::move(request));
    }catch(const std::exception& e){luaL_error(L,"TextService: %s",e.what());return 0;}
    lua_settop(L,0);return lua_yield(L,0);
}
Json text_service_result(const std::string& kind,const Json& value) {
    if(kind=="bounds") {
        if(!value.is_array()||value.size()!=2)throw std::runtime_error("invalid measured text bounds");
        for(const auto& n:value)if(!n.is_number()||!std::isfinite(n.get<double>())||n<0||n>4194304)throw std::runtime_error("invalid measured text extent");
        return Json{{"$vector2",value}};
    }
    if(!value.is_object()||value.size()!=2||!value.contains("Name")||!value["Name"].is_string()||value["Name"].get_ref<const std::string&>().size()>256||!value.contains("Faces")||!value["Faces"].is_array()||value["Faces"].empty()||value["Faces"].size()>64)throw std::runtime_error("invalid font family metadata");
    Json result=value;
    for(auto& face:result["Faces"]) {
        if(!face.is_object()||face.size()!=3||!face.contains("Name")||!face["Name"].is_string()||face["Name"].get_ref<const std::string&>().size()>128||!face.contains("Weight")||!face["Weight"].is_number_integer()||!face.contains("Style")||!face["Style"].is_number_integer())throw std::runtime_error("invalid font face metadata");
        if(face["Weight"]<100||face["Weight"]>900||face["Style"]<0||face["Style"]>1)throw std::runtime_error("invalid font face enum range");
        const int weight=face["Weight"].get<int>(),style=face["Style"].get<int>();
        if(weight%100)throw std::runtime_error("invalid font weight");
        face["Weight"]=Json{{"$enum",{{"type","FontWeight"},{"name",kGeometryEnums[kFontWeight].names[weight/100-1]},{"value",weight}}}};
        face["Style"]=Json{{"$enum",{{"type","FontStyle"},{"name",kGeometryEnums[kFontStyle].names[style]},{"value",style}}}};
    }
    return result;
}
void complete_text_service_impl(lua_State* from,World& w,const Json& responses) {
    if(!responses.is_array()||responses.size()>32||responses.dump().size()>256*1024)throw std::runtime_error("invalid TextService response batch");
    for(const auto& response:responses) {
        const uint64_t id=response.at("request_id").get<uint64_t>();
        auto it=std::find_if(w.text_service_waiters.begin(),w.text_service_waiters.end(),[&](const TextServiceWaiter& waiter){return waiter.request==id;});
        if(it==w.text_service_waiters.end())continue;
        const bool failed=response.contains("error");Json result;std::string error;
        if(failed){error=response.at("error").get<std::string>();if(error.size()>512)throw std::runtime_error("TextService error exceeds limit");}
        else result=text_service_result(it->kind,response.at("result"));
        if(!failed) {
            const size_t size=it->key.size()+result.dump().size();
            if(w.text_service_cache.size()<128&&w.text_service_cache_bytes+size<=512*1024) {
                auto [_,inserted]=w.text_service_cache.emplace(it->key,result);if(inserted)w.text_service_cache_bytes+=size;
            }
        }
        w.scheduler->wake_signal(it->thread,[&](lua_State* target){lua_pushboolean(target,!failed);if(failed)lua_pushlstring(target,error.data(),error.size());else push_json(target,w,result);return 2;},true);
        lua_unref(from,it->reference);w.text_service_waiters.erase(it);
    }
}
void progress_text_service(lua_State* from,World& w) {
    for(auto it=w.text_service_waiters.begin();it!=w.text_service_waiters.end();) {
        const bool finished=w.scheduler->completion(it->thread)!=LUA_YIELD;
        const bool expired=std::chrono::steady_clock::now()>=it->deadline;
        if(finished||expired) {
            if(!finished)w.scheduler->wake_signal(it->thread,[](lua_State* target){lua_pushboolean(target,false);lua_pushliteral(target,"TextService font request timed out");return 2;},true);
            const auto id=it->request;
            w.text_service_requests.erase(std::remove_if(w.text_service_requests.begin(),w.text_service_requests.end(),[&](const Json& request){return request.at("request_id")==id;}),w.text_service_requests.end());
            lua_unref(from,it->reference);it=w.text_service_waiters.erase(it);
        }else ++it;
    }
}
