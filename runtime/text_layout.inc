// Validation and rendering metadata for ordinary text GUI properties.
bool text_layout_node(const Node& n){return n.class_name=="TextLabel"||n.class_name=="TextButton"||n.class_name=="TextBox";}
void validate_text_assignment(lua_State* L,World& w,const Node& n,const std::string& key) {
    if(text_layout_node(n)&&(key=="LineHeight"||key=="TextSize")) {
        if(lua_type(L,3)!=LUA_TNUMBER||!std::isfinite(lua_tonumber(L,3)))throw std::runtime_error("text size and line height must be finite numbers");
        const double value=lua_tonumber(L,3);
        if(key=="LineHeight"&&(value<1||value>3))throw std::runtime_error("LineHeight must be in 1..3");
        if(key=="TextSize"&&(value<1||value>1000))throw std::runtime_error("TextSize must be in 1..1000");
    }
    if(n.class_name=="UITextSizeConstraint"&&(key=="MinTextSize"||key=="MaxTextSize")) {
        if(lua_type(L,3)!=LUA_TNUMBER)throw std::runtime_error("text constraint requires an integer");
        const double supplied=lua_tonumber(L,3);
        if(!std::isfinite(supplied)||supplied<1||supplied>1000||supplied!=std::floor(supplied))throw std::runtime_error("text size constraint must be an integer in 1..1000");
        const auto* other=property_value(w,n,key=="MinTextSize"?"MaxTextSize":"MinTextSize");
        if(!other||!other->is_number()||!std::isfinite(other->get<double>()))throw std::runtime_error("other text constraint bound is unavailable");
        if((key=="MinTextSize"&&supplied>other->get<double>())||(key=="MaxTextSize"&&supplied<other->get<double>()))throw std::runtime_error("MinTextSize must not exceed MaxTextSize");
    }
}
Json text_constraint_values(World& w,const Node& n) {
    const auto* min=property_value(w,n,"MinTextSize");const auto* max=property_value(w,n,"MaxTextSize");
    if(!min||!max||!min->is_number()||!max->is_number())throw std::runtime_error("text constraint bounds unavailable");
    const double low=min->get<double>(),high=max->get<double>();
    if(!std::isfinite(low)||!std::isfinite(high)||low<1||high>1000||low>high||low!=std::floor(low)||high!=std::floor(high))throw std::runtime_error("invalid text size constraint bounds");
    return Json::array({low,high});
}
