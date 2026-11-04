// Synchronized Font/FontFace scripting state for the three text GUI classes.
bool text_font_node(const Node& n){return n.class_name=="TextLabel"||n.class_name=="TextButton"||n.class_name=="TextBox";}
Json text_font_enum_json(int value) {
    for(int i=0;i<kGeometryEnums[kClientFont].count;++i)if(geometry_numeric(kClientFont,i)==value)
        return Json{{"$enum",{{"type","Font"},{"name",kGeometryEnums[kClientFont].names[i]},{"value",value}}}};
    throw std::runtime_error("invalid Font enum value");
}
int text_font_enum_number(const Json& value) {
    const Json* number=&value;
    if(value.is_object()&&value.size()==1&&value.contains("$enum")) {
        const auto& item=value["$enum"];
        if(!item.is_object()||item.value("type","")!="Font"||!item.contains("value"))throw std::runtime_error("invalid Font enum type");
        number=&item["value"];
    }
    if(!number->is_number_integer()||*number<0||*number>100)throw std::runtime_error("invalid Font enum value");
    const int index=number->get<int>();const auto canonical=text_font_enum_json(index);
    if(value.is_object()&&value["$enum"].contains("name")&&value["$enum"]["name"]!=canonical["$enum"]["name"])throw std::runtime_error("Font enum name/value mismatch");
    return index;
}
bool text_font_values(World& w,const Node& n,Json& face,int& index) {
    const auto* face_value=property_value(w,n,"FontFace");const auto* font_value=property_value(w,n,"Font");
    // An Unknown-only update invalidates the previous family, including any
    // snapshot-wide constructor default. A later explicit assignment wins.
    if(!local_value(w,n,"FontFace")&&n.origins.value("FontFace",Json())=="unavailable")face_value=nullptr;
    if(face_value) {
        face=*face_value;index=authored_font_to_enum(face); // Also validates the font.
        if(font_value) {
            const int received=text_font_enum_number(*font_value);
            if(received!=100&&authored_font_from_enum(received)==face)index=received;
        }
        return true;
    }
    if(font_value) {
        index=text_font_enum_number(*font_value);
        if(index==100)return false;
        face=authored_font_from_enum(index);return true;
    }
    return false;
}
bool text_font_index(lua_State* L,World& w,Node& n,const std::string& key) {
    if(!text_font_node(n)||(key!="Font"&&key!="FontFace"))return false;
    try {
        Json face;int index=100;
        if(!text_font_values(w,n,face,index)) {
            const auto* known=property_value(w,n,"Font");
            if(key=="Font"&&known&&text_font_enum_number(*known)==100) {
                world_bindings::push_geometry_enum_item(L,"Font",100);return true;
            }
            throw std::runtime_error("text font has not been received or assigned");
        }
        if(key=="Font")world_bindings::push_geometry_enum_item(L,"Font",index);
        else push_authored_value(L,face);
    }catch(const std::exception& e){luaL_error(L,"text font unavailable: %s",e.what());return false;}
    return true;
}
std::string text_font_origin(World& w,const Node& n,const std::string& key) {
    if(!text_font_node(n)||(key!="Font"&&key!="FontFace"))return "";
    if(key=="FontFace"&&!local_value(w,n,key)&&n.origins.value(key,Json())=="unavailable")return "unavailable";
    if(property_value(w,n,key))return "";
    try {
        Json face;int index=100;if(!text_font_values(w,n,face,index))return "unavailable";
        const std::string source=key=="Font"?"FontFace":"Font";
        if(local_value(w,n,source))return "derived_local";
        if(n.properties.contains(source))return "derived_received";
        return "derived_class_default";
    }catch(const std::exception&){return "unavailable";}
}
bool text_font_write(lua_State* L,World& w,Node& n,const std::string& key) {
    if(!text_font_node(n)||(key!="Font"&&key!="FontFace"))return false;
    Json face;int index=100;
    if(key=="Font") {
        index=geometry_numeric(kClientFont,checked_geometry_value(L,3,kClientFont));
        face=authored_font_from_enum(index);
    } else {
        if(!is_authored_value(L,3)||std::strcmp(luaL_typename(L,3),"Font")!=0)throw std::runtime_error("FontFace requires a Font value");
        capture_authored_value(L,3,face);index=authored_font_to_enum(face);
    }
    Json old_face;int old_index=100;bool known=false;
    try{known=text_font_values(w,n,old_face,old_index);}catch(const std::exception&){} // A valid write repairs malformed input.
    const Json enumeration=text_font_enum_json(index);
    std::vector<Json> entries{
        Json{{"kind","set"},{"id",n.id},{"property","FontFace"},{"value",face}},
        Json{{"kind","set"},{"id",n.id},{"property","Font"},{"value",enumeration}}};
    check_mutation_budget(w,entries);
    w.local_properties[n.id]["FontFace"]=face;w.local_properties[n.id]["Font"]=enumeration;
    append_mutations(w,std::move(entries));
    if(!known||old_face!=face)property_events(w,n,"FontFace");
    if(!known||old_index!=index)property_events(w,n,"Font");
    return true;
}
bool apply_received_text_font(World& w,Node& n,const Json& fields,uint64_t sequence) {
    if(!text_font_node(n)||(!fields.contains("FontFace")&&!fields.contains("Font")))return false;
    Json face;int index=100;
    if(fields.contains("FontFace")) {
        face=fields["FontFace"];index=authored_font_to_enum(face);
        if(fields.contains("Font")) {
            int supplied=text_font_enum_number(fields["Font"]);
            if(supplied!=100&&authored_font_from_enum(supplied)==face)index=supplied;
        }
    }else {index=text_font_enum_number(fields["Font"]);if(index!=100)face=authored_font_from_enum(index);}
    Json old_face;int old_index=100;bool known=false;
    try{known=text_font_values(w,n,old_face,old_index);}catch(const std::exception&){}
    for(const auto& key:{"FontFace","Font"}) {
        clear_local_property(w,n.id,key);
        if(std::strcmp(key,"FontFace")==0&&face.is_null()) {
            n.properties.erase(key);n.revisions[key]=sequence;n.origins[key]="unavailable";continue;
        }
        n.properties[key]=std::strcmp(key,"FontFace")==0?face:text_font_enum_json(index);
        n.revisions[key]=sequence;n.origins[key]=fields.contains(key)?"received":"derived_received";
    }
    if(!known||old_face!=face)property_events(w,n,"FontFace");
    if(!known||old_index!=index)property_events(w,n,"Font");
    return true;
}
