// Real, synchronous glyph measurement supplied by the Rust host. A C++-only
// host or missing local font is an explicit error, never a guessed width.
std::array<double,2> local_text_bounds(const Json& request) {
    const auto* api=rust_text_measure_api();
    if(!api)throw std::runtime_error("local text measurement requires the Rust font backend");
    const auto encoded=request.dump();
    std::array<double,2> result{};std::array<uint8_t,512> error{};
    if(encoded.size()>64*1024)throw std::runtime_error("text measurement request exceeds limit");
    if(!api->bounds(reinterpret_cast<const uint8_t*>(encoded.data()),encoded.size(),
            result.data(),error.data(),error.size()))
        throw std::runtime_error(error[0]?reinterpret_cast<const char*>(error.data()):"local text measurement failed");
    for(double value:result)if(!std::isfinite(value)||value<0||value>4194304)
        throw std::runtime_error("invalid measured text extent");
    return result;
}
bool text_wraps(World& w,const Node& n) {
    const auto* value=property_value(w,n,"TextWrapped");
    if(!value||!value->is_boolean())throw std::runtime_error("TextWrapped is unavailable");
    return value->get<bool>();
}
std::array<double,2> text_intrinsic_size(World& w,const Node& n,double width) {
    const auto* scaled=property_value(w,n,"TextScaled");
    if(!scaled||!scaled->is_boolean())throw std::runtime_error("TextScaled is unavailable");
    if(scaled->get<bool>())throw std::runtime_error("combined TextScaled and AutomaticSize is not implemented");
    const auto* source=property_value(w,n,"Text");
    const auto* rich=property_value(w,n,"RichText");
    if(!source||!source->is_string()||!rich||!rich->is_boolean())
        throw std::runtime_error("automatic text content is unavailable");
    Json font;int font_enum=100;
    if(!text_font_values(w,n,font,font_enum))throw std::runtime_error("automatic text font is unavailable");
    Json request{{"request_id",1},{"kind","bounds"},{"text",*source},
        {"font",font.at("$font")},{"size",layout_number(w,n,"TextSize",14)},
        {"width",width},{"line_height",layout_number(w,n,"LineHeight",1)}};
    if(rich->get<bool>())if(auto document=rich_text::parse(source->get_ref<const std::string&>())) {
        request["text"]=std::move(document->text);request["runs"]=std::move(document->runs);
    }
    return local_text_bounds(request);
}
int text_service_get_text_size(lua_State* L) {
    auto* h=checked_handle(L,1);
    if(h->node->class_name!="TextService"||h->node->destroyed)
        return luaL_error(L,"GetTextSize requires TextService"),0;
    if(lua_gettop(L)!=5||lua_type(L,2)!=LUA_TSTRING||lua_type(L,3)!=LUA_TNUMBER)
        return luaL_error(L,"GetTextSize expects text, fontSize, font and frameSize"),0;
    const double size=lua_tonumber(L,3);
    double width=0,height=0;
    if(!std::isfinite(size)||size<1||size>1000||size!=std::floor(size)||
        !read_vector2(L,5,width,height)||!std::isfinite(width)||!std::isfinite(height)||
        width<0||height<0||width>32768||height>32768)
        return luaL_error(L,"GetTextSize requires fontSize in 1..1000 and finite frameSize in 0..32768"),0;
    const int font=geometry_numeric(kClientFont,checked_geometry_value(L,4,kClientFont));
    size_t length=0;const char* text=lua_tolstring(L,2,&length);
    if(length>4096||!valid_utf8(text,length)||std::memchr(text,0,length))
        return luaL_error(L,"GetTextSize requires valid UTF-8 text up to 4096 bytes without NUL"),0;
    try {
        const Json face=authored_font_from_enum(font);
        auto extent=local_text_bounds(Json{{"request_id",1},{"kind","bounds"},
            {"text",std::string(text,length)},{"size",size},{"width",width},{"font",face.at("$font")}});
        push_json(L,*h->world,display_vector(extent[0],extent[1]));return 1;
    }catch(const std::exception& e){return luaL_error(L,"GetTextSize: %s",e.what()),0;}
}
