// Local display projection. Reads the live VM, including local GUI and controller
// feedback, without running user source or exposing script bodies/credentials.
bool surface_visible(World& w,Node& n) {
    Node* current=&n;bool attached=false;
    for(int depth=0;current&&depth<128;++depth) {
        if(current->destroyed)return false;
        for(const char* key:{"Visible","Enabled"})
            if(const auto* value=property_value(w,*current,key);value&&value->is_boolean()&&!value->get<bool>())return false;
        if(current->class_name=="CoreGui") {attached=true;break;}
        if(current->class_name=="PlayerGui") {
            attached=parent_id(w,*current)==w.local_player;break;
        }
        current=find_node(w,parent_id(w,*current));
    }
    return attached;
}
Json render_snapshot(World& w) {
    Json out{{"version",2},{"sequence",w.sequence},{"parts",Json::array()},
             {"gui",Json::array()},{"players",Json::array()},{"viewport",w.display["size"]},
             {"skippedParts",0},{"skippedGui",0},{"meshBounds",0},{"terrainUnsupported",false}};
    size_t bytes=512;
    auto field=[&](const Node& n,const char* name,const Json& fallback)->Json {
        const auto* value=property_value(w,n,name);return value?*value:fallback;
    };
    auto text=[&](const Node& n,const char* name,size_t maximum)->std::string {
        auto value=field(n,name,"");if(!value.is_string())return "";
        const auto& source=value.get_ref<const std::string&>();size_t end=std::min(source.size(),maximum);
        while(end<source.size()&&end>0&&(static_cast<unsigned char>(source[end])&0xc0)==0x80)--end;
        return source.substr(0,end);
    };
    std::string workspace=w.services.count("Workspace")?w.services.at("Workspace"):"";
    Node* own=find_node(w,w.local_player);std::string character;
    if(own) {auto value=field(*own,"Character",nullptr);if(value.is_object())character=value.value("$ref",std::string());}
    std::vector<Node*> parts;
    std::unordered_map<std::string,std::vector<Node*>> children;
    for(const auto& id:w.order)if(auto* node=find_node(w,id);node&&!node->destroyed)
        children[parent_id(w,*node)].push_back(node);
    // Collect appearance once per model. Scanning every part's siblings would
    // make a large, flat Workspace quadratic even when no clothing is present.
    std::unordered_map<std::string,Json> character_clothing;
    for(const auto& [parent,siblings]:children) {
        bool humanoid=false;
        for(auto* sibling:siblings)if(sibling->class_name=="Humanoid"){humanoid=true;break;}
        if(!humanoid)continue;
        Json clothing=Json::object();
        for(auto* sibling:siblings) {
            if(sibling->class_name=="Shirt")clothing["shirt"]=text(*sibling,"ShirtTemplate",512);
            if(sibling->class_name=="Pants")clothing["pants"]=text(*sibling,"PantsTemplate",512);
            if(sibling->class_name=="ShirtGraphic")clothing["graphic"]=text(*sibling,"Graphic",512);
        }
        character_clothing.emplace(parent,std::move(clothing));
    }
    if(auto it=w.services.find("Lighting");it!=w.services.end())if(auto* light=find_node(w,it->second)) {
        Json values=Json::object();
        for(const char* key:{"Ambient","OutdoorAmbient","Brightness","ClockTime","TimeOfDay",
                "GeographicLatitude","GlobalShadows","ShadowSoftness","ExposureCompensation",
                "FogColor","FogStart","FogEnd","ColorShift_Top","ColorShift_Bottom"})
            if(const auto* value=property_value(w,*light,key))values[key]=*value;
        out["lighting"]=std::move(values);
    }
    GuiLayoutPass layout{w};
    for(const auto& id:w.order) {
        auto* n=find_node(w,id);if(!n||n->destroyed)continue;
        if(n->class_name=="Player")out["players"].push_back(Json{{"name",instance_name(w,*n)},{"displayName",field(*n,"DisplayName",instance_name(w,*n))},{"local",n->id==w.local_player}});
        if(n->class_name=="Terrain")out["terrainUnsupported"]=true;
        if(pivot_part(w,*n)) {
            auto* ancestor=n;bool attached=false;
            for(int depth=0;ancestor&&depth<128;++depth) {
                if(ancestor->id==workspace){attached=true;break;}
                ancestor=find_node(w,parent_id(w,*ancestor));
            }
            if(attached)parts.push_back(n);
        }
        if(gui_object(w,*n)&&surface_visible(w,*n)) {
            if(out["gui"].size()>=600||bytes>720000){out["skippedGui"]=out["skippedGui"].get<int>()+1;continue;}
            try {
                auto b=layout.box(*n);if(!b.attached||b.width<=0||b.height<=0)continue;
                Json g{{"id",n->id},{"class",n->class_name},{"name",instance_name(w,*n)},
                       {"box",{b.x,b.y,b.width,b.height,b.rotation}},
                       {"background",field(*n,"BackgroundColor3",Json{{"$color3",{1,1,1}}})},
                       {"backgroundTransparency",field(*n,"BackgroundTransparency",0)},
                       {"z",field(*n,"ZIndex",1)},{"text",text(*n,"Text",4096)},
                       {"textColor",field(*n,"TextColor3",Json{{"$color3",{0,0,0}}})},
                       {"textSize",field(*n,"TextSize",14)},{"textScaled",field(*n,"TextScaled",false)},
                       {"textTransparency",field(*n,"TextTransparency",0)},
                       {"image",text(*n,"Image",2048)},{"imageTransparency",field(*n,"ImageTransparency",0)},
                       {"interactive",class_is_a(w,n->class_name,"GuiButton")||n->class_name=="TextBox"}};
                if(auto* corner=layout.component(*n,"UICorner")) {
                    const double shortest=std::min(b.width,b.height);
                    g["cornerRadius"]=std::clamp(layout.dimension(
                        *property_value(w,*corner,"CornerRadius"),shortest,b.scale),0.0,shortest*.5);
                }
                if(auto* stroke=layout.component(*n,"UIStroke");stroke&&field(*stroke,"Enabled",true)==true) {
                    g["stroke"]={{"color",field(*stroke,"Color",Json{{"$color3",{0,0,0}}})},
                        {"thickness",field(*stroke,"Thickness",1)},
                        {"transparency",field(*stroke,"Transparency",0)}};
                }
                Node* ancestor=n;int order=0;
                for(int depth=0;ancestor&&depth<128;++depth){if(ancestor->class_name=="ScreenGui"){auto v=field(*ancestor,"DisplayOrder",0);if(v.is_number_integer())order=v.get<int>();break;}ancestor=find_node(w,parent_id(w,*ancestor));}
                g["displayOrder"]=order;
                bytes+=g.dump().size();out["gui"].push_back(std::move(g));
            }catch(const std::exception&){out["skippedGui"]=out["skippedGui"].get<int>()+1;}
        }
    }
    // Include nested accessory Handles when reserving space for the character.
    std::unordered_set<std::string> own_parts;
    if(!character.empty())for(auto* part:parts) {
        auto* ancestor=part;
        for(int depth=0;ancestor&&depth<128;++depth) {
            if(ancestor->id==character){own_parts.insert(part->id);break;}
            ancestor=find_node(w,parent_id(w,*ancestor));
        }
    }
    std::stable_sort(parts.begin(),parts.end(),[&](Node* a,Node* b){return own_parts.count(a->id)>own_parts.count(b->id);});
    for(auto* n:parts) {
        if(out["parts"].size()>=1800||bytes>720000){out["skippedParts"]=out["skippedParts"].get<int>()+1;continue;}
        try {
            auto state=part_transform_state(w,*n);
            auto size=field(*n,"Size",field(*n,"size",nullptr));
            if(size.is_null()||!state.contains("Position"))continue;
            Json p{{"id",n->id},{"name",instance_name(w,*n)},{"class",n->class_name},
                   {"position",state["Position"]},{"size",size},
                   {"color",field(*n,"Color",Json{{"$color3",{.64,.64,.64}}})},
                   {"transparency",field(*n,"Transparency",0)},
                   {"localTransparency",field(*n,"LocalTransparencyModifier",0)},
                   {"material",field(*n,"Material",256)},{"reflectance",field(*n,"Reflectance",0)},
                   {"castShadow",field(*n,"CastShadow",true)},
                   {"shape",field(*n,"Shape",field(*n,"shape",1))},{"own",own_parts.count(n->id)>0}};
            if(state.contains("CFrame"))p["frame"]=state["CFrame"];
            const auto parent=parent_id(w,*n);
            if(auto appearance=character_clothing.find(parent);appearance!=character_clothing.end()) {
                p["characterPart"]=instance_name(w,*n);
                if(!appearance->second.empty())p["clothing"]=appearance->second;
            }
            if(n->class_name=="MeshPart") {
                p["mesh"]={{"class","MeshPart"},{"id",text(*n,"MeshId",512)},
                    {"texture",text(*n,"TextureID",512)},{"size",field(*n,"MeshSize",nullptr)}};
                if(p["mesh"]["id"]=="")p["mesh"]["id"]=text(*n,"MeshContent",512);
                if(p["mesh"]["texture"]=="")p["mesh"]["texture"]=text(*n,"TextureContent",512);
            }
            for(auto* child:children[n->id]) {
                const auto& cls=child->class_name;
                if(cls=="SpecialMesh"||cls=="BlockMesh"||cls=="CylinderMesh")
                    p["mesh"]={{"class",cls},{"type",field(*child,"MeshType",0)},
                        {"id",text(*child,"MeshId",512)},{"texture",text(*child,"TextureId",512)},
                        {"scale",field(*child,"Scale",Json{{"$vector3",{1,1,1}}})},
                        {"offset",field(*child,"Offset",Json{{"$vector3",{0,0,0}}})},
                        {"vertexColor",field(*child,"VertexColor",Json{{"$vector3",{1,1,1}}})}};
                if(cls=="Decal"||cls=="Texture") {
                    if(!p.contains("decals"))p["decals"]=Json::array();
                    if(p["decals"].size()<12)p["decals"].push_back({{"id",child->id},{"class",cls},
                        {"texture",text(*child,"Texture",512)},{"face",field(*child,"Face",5)},
                        {"color",field(*child,"Color3",Json{{"$color3",{1,1,1}}})},
                        {"transparency",field(*child,"Transparency",0)},
                        {"tileU",field(*child,"StudsPerTileU",2)},{"tileV",field(*child,"StudsPerTileV",2)}});
                }
            }
            if(n->class_name=="MeshPart"||n->class_name=="UnionOperation")out["meshBounds"]=out["meshBounds"].get<int>()+1;
            if(p["own"]==true&&instance_name(w,*n)=="HumanoidRootPart")out["focus"]=state["Position"];
            bytes+=p.dump().size();out["parts"].push_back(std::move(p));
        }catch(const std::exception&){out["skippedParts"]=out["skippedParts"].get<int>()+1;}
    }
    if(auto* ws=find_node(w,workspace)) {
        auto camera=field(*ws,"CurrentCamera",nullptr);
        if(camera.is_object())if(auto* n=find_node(w,camera.value("$ref",std::string())))
            out["camera"]=Json{{"frame",field(*n,"CFrame",nullptr)},{"fov",field(*n,"FieldOfView",70)}};
    }
    return out;
}
void render_gui_action(World& w,const Json& action) {
    if(!action.is_object()||!action.contains("id")||!action["id"].is_string())throw std::runtime_error("GUI target required");
    auto* n=find_node(w,action["id"].get<std::string>());
    if(!n||!gui_object(w,*n)||!surface_visible(w,*n))throw std::runtime_error("GUI target is no longer visible");
    auto kind=action.value("kind",std::string());
    if(kind=="click"&&action.size()==2&&class_is_a(w,n->class_name,"GuiButton")) {
        if(const auto* enabled=property_value(w,*n,"Interactable");enabled&&enabled->is_boolean()&&!enabled->get<bool>())throw std::runtime_error("GUI target is disabled");
        GuiLayoutPass layout{w};auto box=layout.box(*n);
        const double x=box.x+box.width*.5,y=box.y+box.height*.5;
        const std::string id="__local_gui_pointer";
        if(!w.nodes.count(id)) {
            if(w.nodes.size()>=kMaxNodes)throw std::runtime_error("GUI input instance limit exceeded");
            Node input;input.id=id;input.name="InputObject";input.class_name="InputObject";input.server_owned=false;
            w.nodes.emplace(id,std::move(input));w.order.push_back(id);
        }
        auto& input=w.nodes.at(id);
        input.properties={{"UserInputType",input_enum_json("UserInputType",input_enum_item("UserInputType","MouseButton1"))},
            {"KeyCode",input_enum_json("KeyCode",input_enum_item("KeyCode","None"))},{"UserInputState",input_enum_json("UserInputState",input_enum_item("UserInputState","End"))},
            {"Position",{{"$vector3",{x,y,0}}}},{"Delta",{{"$vector3",{0,0,0}}}}};
        emit(w,*n,"MouseButton1Down",Json::array({x,y}));
        emit(w,*n,"MouseButton1Up",Json::array({x,y}));
        emit(w,*n,"MouseButton1Click");emit(w,*n,"Activated",Json::array({Json{{"$ref",id}},1}));
    }else if(kind=="text"&&action.size()==3&&n->class_name=="TextBox"&&action.contains("text")&&action["text"].is_string()&&action["text"].get<std::string>().size()<=4096) {
        w.local_properties[n->id]["Text"]=action["text"];property_events(w,*n,"Text");
        emit(w,*n,"FocusLost",Json::array({true,nullptr}));
    }else throw std::runtime_error("Unsupported GUI interaction");
}
