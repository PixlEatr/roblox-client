// Local display projection. Reads the live VM, including local GUI and controller
// feedback, without running user source or exposing script bodies/credentials.
static Json gradient_stops(const Json& value,bool color) {
    const auto& sequence=value.at(color?"$color_sequence":"$number_sequence");
    if(!sequence.is_array()||sequence.size()<2||sequence.size()>64)
        throw std::runtime_error("Invalid gradient sequence length");
    const auto unit=[](const Json& component) {
        if(!component.is_number()||!std::isfinite(component.get<double>()))throw std::runtime_error("Invalid gradient component");
        return std::clamp(component.get<double>(),0.0,1.0);
    };
    Json stops=Json::array();
    double previous=-1;
    for(const auto& point:sequence) {
        const auto& key=point.at(color?"$color_keypoint":"$number_keypoint");
        const double time=unit(key.at(0));
        if(time!=key[0]||time<previous)throw std::runtime_error("Invalid gradient keypoint order");
        previous=time;
        if(color) {
            const auto& rgb=key.at(1).at("$color3");
            stops.push_back({time,unit(rgb.at(0)),unit(rgb.at(1)),unit(rgb.at(2))});
        } else stops.push_back({time,1-unit(key.at(1))});
    }
    if(stops.front()[0]!=0||stops.back()[0]!=1)throw std::runtime_error("Missing gradient endpoints");
    return stops;
}
Json render_snapshot(World& w) {
    Json out{{"version",2},{"sequence",w.sequence},{"parts",Json::array()},
             {"gui",Json::array()},{"players",Json::array()},{"viewport",w.display["size"]},
             {"skippedParts",0},{"skippedGui",0},{"meshBounds",0},{"terrainUnsupported",false}};
    focused_textbox(w);
    out["textFocus"]={{"id",w.focused_textbox},{"revision",w.text_focus_revision}};
    out["textEditor"]={{"revision",w.text_editor_revision},{"cursor",w.text_cursor},{"anchor",w.text_selection}};
    size_t bytes=512;
    auto field=[&](const Node& n,const char* name,const Json& fallback)->Json {
        const auto* value=property_value(w,n,name);return value?*value:fallback;
    };
    auto number=[&](const Node& n,const char* name,double fallback) {
        const auto* value=property_value(w,n,name);
        if(!value||!value->is_number())return fallback;
        const double result=value->get<double>();
        return std::isfinite(result)?result:fallback;
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
    const auto gradient_for=[&](const Node& parent)->Json {
        auto list=children.find(parent.id);
        if(list==children.end())return nullptr;
        for(auto* gradient:list->second) {
            if(gradient->class_name!="UIGradient"||field(*gradient,"Enabled",true)!=true)continue;
            try {
                const auto* offset=property_value(w,*gradient,"Offset");
                const auto& shift=offset->at("$vector2");
                const double x=shift.at(0).get<double>(),y=shift.at(1).get<double>();
                if(!std::isfinite(x)||!std::isfinite(y))return nullptr;
                return {{"colors",gradient_stops(*property_value(w,*gradient,"Color"),true)},
                    {"alpha",gradient_stops(*property_value(w,*gradient,"Transparency"),false)},
                    {"offset",{std::clamp(x,-1000.,1000.),std::clamp(y,-1000.,1000.)}},
                    {"rotation",std::fmod(number(*gradient,"Rotation",0),360.)},
                    {"scale",std::clamp(number(*gradient,"Scale",1),.001,1000.)},
                    {"tile",gui_enum_value(w,*gradient,"TileMode")},
                    {"type",gui_enum_value(w,*gradient,"Type")}};
            }catch(const std::exception&){return nullptr;}
        }
        return nullptr;
    };
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
                "FogColor","FogStart","FogEnd","ColorShift_Top","ColorShift_Bottom",
                "EnvironmentDiffuseScale","EnvironmentSpecularScale"})
            if(const auto* value=property_value(w,*light,key))values[key]=*value;
        out["lighting"]=std::move(values);
    }
    std::unordered_set<std::string> pointer_receivers,wheel_receivers;
    bool global_wheel_receiver=false;
    auto* player_mouse=local_player_mouse(w);
    for(const auto& connection:w.connections) {
        if(!connection->connected||!connection->signal||!connection->signal->node)continue;
        const auto& key=connection->signal->key;const auto& id=connection->signal->node->id;
        if(connection->signal->node->class_name=="UserInputService"&&(key=="InputChanged"||key=="LastInputTypeChanged"))global_wheel_receiver=true;
        if(connection->signal->node==player_mouse&&(key=="WheelForward"||key=="WheelBackward"))global_wheel_receiver=true;
        if(key=="InputBegan"||key=="InputEnded")pointer_receivers.insert(id);
        if(key=="InputChanged"||key=="MouseWheelForward"||key=="MouseWheelBackward")wheel_receivers.insert(id);
    }
    for(const auto& binding:w.context_actions.bindings)
        if(binding->active&&context_matches(*binding,3,0)){global_wheel_receiver=true;break;}
    GuiLayoutPass layout{w};
    bool clip_rotated_children=false;
    if(auto it=w.services.find("StarterGui");it!=w.services.end())
        if(auto* starter=find_node(w,it->second))clip_rotated_children=field(*starter,"ClipsDescendantsSupportsRotation",false)==true;
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
                std::string pointer_target;
                auto* current=n;
                for(int depth=0;current&&gui_object(w,*current)&&depth<128;++depth,current=find_node(w,parent_id(w,*current))) {
                    if(pointer_receivers.count(current->id)||class_is_a(w,current->class_name,"GuiButton")) {
                        pointer_target=current->id;break;
                    }
                }
                Json g{{"id",n->id},{"class",n->class_name},{"name",instance_name(w,*n)},
                       {"box",{b.x,b.y,b.width,b.height,b.rotation}},
                       {"background",field(*n,"BackgroundColor3",Json{{"$color3",{1,1,1}}})},
                       {"backgroundTransparency",field(*n,"BackgroundTransparency",0)},
                       {"z",field(*n,"ZIndex",1)},{"text",text(*n,"Text",4096)},
                       {"textColor",field(*n,"TextColor3",Json{{"$color3",{0,0,0}}})},
                       {"textSize",field(*n,"TextSize",14)},{"textScaled",field(*n,"TextScaled",false)},
                       {"textTransparency",field(*n,"TextTransparency",0)},
                       {"image",text(*n,"Image",2048)},{"imageTransparency",field(*n,"ImageTransparency",0)},
                       {"interactive",!pointer_target.empty()||n->class_name=="TextBox"},
                       {"pointerTarget",pointer_target},{"textEditable",field(*n,"TextEditable",true)}};
                // A flat browser surface still needs all ancestor clip bounds.
                // Bounds are in display coordinates, before the child's rotation.
                // Rounded and rotated clipping parents are not supported by Roblox.
                bool has_clip=false;
                double left=0,top=0,right=0,bottom=0;
                if(clip_rotated_children||b.rotation==0) {
                    auto* parent=find_node(w,parent_id(w,*n));
                    for(int depth=0;parent&&depth<128;++depth,parent=find_node(w,parent_id(w,*parent))) {
                        if(!gui_object(w,*parent)||field(*parent,"ClipsDescendants",false)!=true)continue;
                        auto bounds=layout.box(*parent);
                        if(!bounds.attached||bounds.rotation!=0)continue;
                        if(!has_clip){left=bounds.x;top=bounds.y;right=left+bounds.width;bottom=top+bounds.height;has_clip=true;}
                        else {left=std::max(left,bounds.x);top=std::max(top,bounds.y);
                            right=std::min(right,bounds.x+bounds.width);bottom=std::min(bottom,bounds.y+bounds.height);}
                    }
                }
                if(has_clip)g["clip"]={left,top,std::max(0.0,right-left),std::max(0.0,bottom-top)};
                if(global_wheel_receiver)g["wheelTarget"]=true;
                auto* wheel=n;
                for(int depth=0;wheel&&depth<128;++depth,wheel=find_node(w,parent_id(w,*wheel))) {
                    if(wheel->class_name=="ScreenGui")break;
                    if(wheel_receivers.count(wheel->id)){g["wheelTarget"]=true;break;}
                }
                auto* scroll=n;
                for(int depth=0;scroll&&depth<128;++depth,scroll=find_node(w,parent_id(w,*scroll))) {
                    if(scroll->class_name=="ScreenGui")break;
                    if(scroll->class_name!="ScrollingFrame"||field(*scroll,"ScrollingEnabled",true)!=true)continue;
                    auto viewport=layout.box(*scroll);
                    if(!viewport.attached||viewport.scale<=0)continue;
                    g["scrollTarget"]=scroll->id;
                    g["scrollViewport"]={viewport.width,viewport.height};break;
                }
                if(text_font_node(*n)) {
                    g["textWrapped"]=field(*n,"TextWrapped",false);
                    g["lineHeight"]=field(*n,"LineHeight",1);
                    g["textScale"]=b.scale;
                    g["multiLine"]=field(*n,"MultiLine",false);
                    g["textTruncate"]=gui_enum_value(w,*n,"TextTruncate");
                    const double legacy_transparency=std::clamp(number(*n,"TextStrokeTransparency",1),0.0,1.0);
                    if(legacy_transparency<1)g["legacyTextStroke"]={{"color",field(*n,"TextStrokeColor3",nullptr)},{"transparency",legacy_transparency}};
                    g["maxVisibleGraphemes"]=field(*n,"MaxVisibleGraphemes",-1);
                    if(n->class_name=="TextBox") {
                        g["placeholderText"]=text(*n,"PlaceholderText",4096);
                        g["placeholderColor"]=field(*n,"PlaceholderColor3",nullptr);
                        g["clearTextOnFocus"]=field(*n,"ClearTextOnFocus",true);
                    }
                    for(const auto& [property,output]:{std::pair{"TextXAlignment","textXAlignment"},std::pair{"TextYAlignment","textYAlignment"},std::pair{"TextDirection","textDirection"}}) {
                        if(property_value(w,*n,property))g[output]=gui_enum_value(w,*n,property);
                    }
                    if(auto* constraint=layout.component(*n,"UITextSizeConstraint"))g["textSizeConstraint"]=text_constraint_values(w,*constraint);
                    try {
                        Json font;int index=100;
                        if(text_font_values(w,*n,font,index))g["fontFace"]=font.at("$font");
                    }catch(const std::exception&){g["fontUnavailable"]=true;}
                    if(field(*n,"RichText",false)==true) {
                        if(auto document=rich_text::parse(g["text"].get_ref<const std::string&>())) {
                            g["contentText"]=std::move(document->text);
                            for(auto& run:document->runs) {
                                const auto& style=run.at("style");
                                if(!style.contains("family")&&!style.contains("weight")&&!style.contains("style"))continue;
                                Json face=g.value("fontFace",Json::object());
                                for(const auto* key:{"family","weight","style"})
                                    if(style.contains(key))face[key]=style.at(key);
                                if(face.contains("family")) {
                                    if(!face.contains("weight"))face["weight"]=400;
                                    if(!face.contains("style"))face["style"]=0;
                                    run["fontFace"]=std::move(face);
                                }
                            }
                            g["richRuns"]=std::move(document->runs);
                        }
                    }
                }
                if(auto* corner=layout.component(*n,"UICorner")) {
                    const double shortest=std::min(b.width,b.height);
                    g["cornerRadius"]=std::clamp(layout.dimension(
                        *property_value(w,*corner,"CornerRadius"),shortest,b.scale),0.0,shortest*.5);
                }
                bool has_image=g["image"]!="";
                if(n->class_name=="ImageButton") {
                    g["imageHover"]=text(*n,"HoverImage",2048);
                    g["imagePressed"]=text(*n,"PressedImage",2048);
                    g["imageButtonEnabled"]=field(*n,"Interactable",true);
                    has_image=has_image||g["imageHover"]!=""||g["imagePressed"]!="";
                }
                if(gui_image(*n)&&has_image) {
                    const int mode=gui_enum_value(w,*n,"ScaleType");
                    g["imageMode"]=mode;
                    g["imageColor"]=field(*n,"ImageColor3",nullptr);
                    g["imagePixelated"]=gui_enum_value(w,*n,"ResampleMode")==1;
                    g["imageOffset"]=field(*n,"ImageRectOffset",nullptr);
                    g["imageRect"]=field(*n,"ImageRectSize",nullptr);
                    if(mode==1) {
                        g["imageSlice"]=field(*n,"SliceCenter",nullptr);
                        g["imageSliceScale"]=number(*n,"SliceScale",1)*b.scale;
                    }else if(mode==2) {
                        const auto& size=property_value(w,*n,"TileSize")->at("$udim2");
                        g["imageTile"]={layout.dimension(size.at(0),b.width,b.scale),layout.dimension(size.at(1),b.height,b.scale)};
                    }
                    const auto* source=property_value(w,*n,"Image");
                    const bool reportable=source&&g["image"]!=""&&*source==g["image"];
                    if(reportable&&n->image_load_token.empty()) {
                        std::array<char,16> entropy{};
                        if(host_entropy(entropy.data(),entropy.size()))n->image_load_token=bytes_hex(entropy.data(),entropy.size());
                    }
                    if(reportable&&!n->image_load_token.empty()) {
                        g["imageLoadToken"]=n->image_load_token;
                        g["imageLoaded"]=n->image_loaded;
                    }
                }
                if(n->class_name=="Frame"||n->class_name=="TextLabel"||n->class_name=="TextButton"||
                   n->class_name=="ImageLabel"||n->class_name=="ImageButton"||n->class_name=="ViewportFrame") {
                    if(auto gradient=gradient_for(*n);!gradient.is_null())g["backgroundGradient"]=std::move(gradient);
                }
                if(const auto modifiers=children.find(n->id);modifiers!=children.end()) {
                    const double shortest=std::min(b.width,b.height);
                    double text_order=-INFINITY;
                    std::vector<std::pair<double,Json>> borders;
                    for(auto* stroke:modifiers->second) {
                        if(stroke->class_name!="UIStroke"||field(*stroke,"Enabled",true)!=true)continue;
                        const double thickness=number(*stroke,"Thickness",1);
                        const double transparency=std::clamp(number(*stroke,"Transparency",0),0.0,1.0);
                        if(thickness<=0||transparency>=1)continue;
                        const bool outline=text_font_node(*n)&&gui_enum_value(w,*stroke,"ApplyStrokeMode")==0;
                        const bool scaled=gui_enum_value(w,*stroke,"StrokeSizingMode")==1;
                        const double z=number(*stroke,"ZIndex",1);
                        const double width=scaled&&!outline?thickness*shortest:thickness;
                        Json output={{"color",field(*stroke,"Color",nullptr)},{"thickness",std::min(width,1000.0)},{"transparency",transparency}};
                        if(outline) {
                            if(z<=text_order)continue;
                            text_order=z;
                            output["scaled"]=scaled;
                            g["textOutline"]=std::move(output);
                        } else {
                            const auto insertion=std::upper_bound(borders.begin(),borders.end(),z,
                                [](double order,const auto& entry){return order<entry.first;});
                            if(borders.size()==8&&insertion==borders.begin())continue;
                            const int position=gui_enum_value(w,*stroke,"BorderStrokePosition");
                            const int join=gui_enum_value(w,*stroke,"LineJoinMode");
                            const double offset=layout.dimension(*property_value(w,*stroke,"BorderOffset"),shortest,b.scale);
                            if(position!=1)output["position"]=position;
                            if(join!=0)output["join"]=join;
                            if(offset!=0)output["offset"]=std::clamp(offset,-32768.0,32768.0);
                            if(auto gradient=gradient_for(*stroke);!gradient.is_null())output["gradient"]=std::move(gradient);
                            borders.emplace(insertion,z,std::move(output));
                            if(borders.size()>8)borders.erase(borders.begin());
                        }
                    }
                    if(!borders.empty()) {
                        g["stroke"]=borders.back().second;
                        if(borders.size()>1) {
                            auto& layers=g["borderStrokes"]=Json::array();
                            for(auto& border:borders)layers.push_back(std::move(border.second));
                        }
                    }
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
                if(cls=="SurfaceAppearance"&&n->class_name=="MeshPart") {
                    Json appearance{{"color",field(*child,"Color",Json{{"$color3",{1,1,1}}})},
                                    {"alphaMode",field(*child,"AlphaMode",0)}};
                    for(const auto& entry:std::vector<std::pair<std::string,std::string>>{
                            {"colorMap","ColorMap"},{"normalMap","NormalMap"},
                            {"roughnessMap","RoughnessMap"},{"metalnessMap","MetalnessMap"}}) {
                        auto reference=text(*child,entry.second.c_str(),512);
                        if(reference.empty())reference=text(*child,(entry.second+"Content").c_str(),512);
                        if(!reference.empty())appearance[entry.first]=reference;
                    }
                    p["appearance"]=std::move(appearance);
                }
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
Json text_submit_input(World& w) {
    const std::string id="__local_text_submit";
    if(!w.nodes.count(id)) {
        if(w.nodes.size()>=kMaxNodes)throw std::runtime_error("GUI input instance limit exceeded");
        Node input;input.id=id;input.name="InputObject";input.class_name="InputObject";input.server_owned=false;
        input.properties={{"UserInputType",input_enum_json("UserInputType",input_enum_item("UserInputType","Keyboard"))},
            {"KeyCode",input_enum_json("KeyCode",input_enum_item("KeyCode","Return"))},
            {"UserInputState",input_enum_json("UserInputState",input_enum_item("UserInputState","Begin"))},
            {"Position",{{"$vector3",{0,0,0}}}},{"Delta",{{"$vector3",{0,0,0}}}}};
        w.nodes.emplace(id,std::move(input));w.order.push_back(id);
    }
    return Json{{"$ref",id}};
}
void render_gui_action(World& w,const Json& action) {
    if(!action.is_object()||!action.contains("id")||!action["id"].is_string())throw std::runtime_error("GUI target required");
    auto kind=action.value("kind",std::string());
    auto* n=find_node(w,action["id"].get<std::string>());
    if(kind=="image_loaded") {
        const auto& token=action.at("token");
        if(action.size()!=3||!token.is_string()||token.get_ref<const std::string&>().size()!=32)
            throw std::runtime_error("Invalid image load receipt");
        if(n&&gui_image(*n)&&!n->image_loaded&&token==n->image_load_token&&surface_visible(w,*n)) {
            n->image_loaded=true;
            property_events(w,*n,"IsLoaded");
        }
        return;
    }
    if(kind=="text_focus"&&action.size()==2) {
        if(n)capture_textbox(w,*n);
        if(n&&focused_textbox(w)==n)virtual_input_record_gui(w,action);
        return;
    }
    if(kind=="text_blur"||kind=="text_edit"||kind=="text_select") {
        const auto& revision=action.at("revision");
        const bool blur=kind=="text_blur";
        const bool edit=kind=="text_edit";
        const bool selection=kind=="text_select"||(edit&&action.size()!=4);
        if(!revision.is_number_unsigned()||action.size()!=(selection?(edit?7:6):4))
            throw std::runtime_error("Invalid TextBox interaction");
        int cursor=1,anchor=-1;
        if(selection) {
            const auto& c=action.at("cursor");const auto& a=action.at("anchor");
            if(!action.at("editorRevision").is_number_unsigned()||!c.is_number_integer()||
                c<1||c>4097||!a.is_number_integer()||a<-1||a==0||a>4097)
                throw std::runtime_error("Invalid TextBox selection");
            cursor=c.get<int>();anchor=a.get<int>();
        }
        if(blur&&!action.at("submitted").is_boolean())throw std::runtime_error("Invalid TextBox submission");
        if(edit&&(!action.at("text").is_string()||action["text"].get_ref<const std::string&>().size()>4096))
            throw std::runtime_error("Invalid TextBox text");
        if(!n||focused_textbox(w)!=n||revision!=w.text_focus_revision)return;
        if(selection&&action["editorRevision"]!=w.text_editor_revision)return;
        if(blur) {
            const bool submitted=action["submitted"].get<bool>();
            release_textbox(w,submitted,submitted?text_submit_input(w):Json(nullptr));
        } else if(!edit||textbox_editable(w,*n)) {
            if(edit) {
                const auto& text=action["text"].get_ref<const std::string&>();
                if(!valid_utf8(text.data(),text.size()))
                    throw std::runtime_error("Invalid TextBox text");
                write_textbox(w,*n,action["text"]);
                if(!selection)cursor=static_cast<int>(text.size())+1;
            }
            update_textbox_selection(w,*n,cursor,anchor);
        }
        virtual_input_record_gui(w,action);
        return;
    }
    const bool releasing=kind=="pointer_up"&&action.size()==2&&w.gui_pointer_target==action["id"];
    if(releasing&&(!n||n->destroyed)) {
        if(auto* input=find_node(w,w.gui_pointer_input)) {
            input->properties["UserInputState"]=input_enum_json("UserInputState",input_enum_item("UserInputState","Cancel"));
            property_events(w,*input,"UserInputState");
        }
        w.gui_pointer_target.clear();w.gui_pointer_input.clear();return;
    }
    if(!n||!gui_object(w,*n)||(!releasing&&!surface_visible(w,*n)))throw std::runtime_error("GUI target is no longer visible");
    if((kind=="scroll"&&action.size()==3)||(kind=="wheel"&&action.size()==5)) {
        const bool wheel=kind=="wheel";
        const auto& delta=action.at("delta");
        if((!wheel&&n->class_name!="ScrollingFrame")||!delta.is_array()||delta.size()!=2)
            throw std::runtime_error("scroll requires a ScrollingFrame and two pixel deltas");
        for(const auto& value:delta)
            if(!value.is_number()||!std::isfinite(value.get<double>())||std::abs(value.get<double>())>4096)
                throw std::runtime_error("scroll delta must be finite and within 4096 pixels");
        double dx=delta[0].get<double>(),dy=delta[1].get<double>();
        GuiLayoutPass layout{w};
        double x=0,y=0;int steps=0;bool processed=false;
        std::string wheel_input_id;
        auto contains=[](const GuiBox& box,double x,double y) {
            const double angle=-box.rotation*3.14159265358979323846/180;
            const double dx=x-box.x-box.width*.5,dy=y-box.y-box.height*.5;
            const double localX=dx*std::cos(angle)-dy*std::sin(angle)+box.width*.5;
            const double localY=dx*std::sin(angle)+dy*std::cos(angle)+box.height*.5;
            return box.attached&&box.width>0&&box.height>0&&localX>=0&&localY>=0&&localX<box.width&&localY<box.height;
        };
        if(wheel) {
            const auto& position=action.at("position");const auto& count=action.at("steps");
            if(!position.is_array()||position.size()!=2||!count.is_number_integer()||count<-128||count>128)
                throw std::runtime_error("wheel requires a screen position and bounded integer steps");
            for(const auto& value:position)if(!value.is_number()||!std::isfinite(value.get<double>())||std::abs(value.get<double>())>32768)
                throw std::runtime_error("wheel screen coordinates must be finite and within 32768 pixels");
            x=position[0].get<double>();y=position[1].get<double>();steps=count.get<int>();
            // A prior wheel request can move a rendered child away before the
            // next browser frame arrives. Keep the gesture on its still-hovered
            // ancestor instead of dropping the remainder of a trackpad burst.
            if(!contains(layout.box(*n),x,y)) {
                auto* ancestor=find_node(w,parent_id(w,*n));Node* replacement=nullptr;
                for(int depth=0;ancestor&&depth<128;++depth,ancestor=find_node(w,parent_id(w,*ancestor))) {
                    if(ancestor->class_name=="ScreenGui")break;
                    if(gui_object(w,*ancestor)&&contains(layout.box(*ancestor),x,y)){replacement=ancestor;break;}
                }
                if(!replacement)throw std::runtime_error("wheel target is no longer under the cursor");
                n=replacement;
            }
            const auto own=layout.box(*n);
            bool clip_rotated=false;
            if(auto it=w.services.find("StarterGui");it!=w.services.end())if(auto* starter=find_node(w,it->second))
                if(auto* flag=property_value(w,*starter,"ClipsDescendantsSupportsRotation"))clip_rotated=*flag==true;
            if(own.rotation==0||clip_rotated) {
                auto* parent=find_node(w,parent_id(w,*n));
                for(int depth=0;parent&&depth<128;++depth,parent=find_node(w,parent_id(w,*parent))) {
                    if(!gui_object(w,*parent))continue;
                    const auto* clips=property_value(w,*parent,"ClipsDescendants");
                    if(!clips||*clips!=true)continue;
                    const auto bounds=layout.box(*parent);
                    if(bounds.rotation==0&&!contains(bounds,x,y))throw std::runtime_error("wheel cursor is outside the ancestor clip");
                }
            }
            const auto service=w.services.find("UserInputService");
            auto* uis=service==w.services.end()?nullptr:find_node(w,service->second);
            if(!uis||uis->destroyed)throw std::runtime_error("input service unavailable");
            context_reserve(w,1,w.context_actions.bindings.size());
            if(auto existing=w.input.continuous.find(3);existing!=w.input.continuous.end())wheel_input_id=existing->second;
            if(wheel_input_id.empty()&&w.nodes.size()>=kMaxNodes)throw std::runtime_error("GUI input instance limit exceeded");
        }
        std::vector<std::pair<Node*,Json>> changes;
        auto* current=n;
        for(int depth=0;current&&depth<128;++depth,current=find_node(w,parent_id(w,*current))) {
            if(current->class_name=="ScreenGui")break;
            if(current->class_name!="ScrollingFrame")continue;
            const auto* enabled=property_value(w,*current,"ScrollingEnabled");
            if(!enabled||!enabled->is_boolean()||!enabled->get<bool>())continue;
            auto box=layout.box(*current);if(!box.attached||box.scale<=0)continue;
            if(wheel&&!contains(box,x,y))continue;
            auto before=layout.scroll(*current,box);
            const int direction=gui_enum_value(w,*current,"ScrollingDirection");
            processed|=(dx!=0&&direction!=1&&before.canvas_width>before.window_width)||
                       (dy!=0&&direction!=0&&before.canvas_height>before.window_height);
            Json requested=display_vector(before.x+dx/box.scale,before.y+dy/box.scale);
            auto after=layout.scroll(*current,box,&requested);
            dx-=(after.x-before.x)*box.scale;dy-=(after.y-before.y)*box.scale;
            if(before.x!=after.x||before.y!=after.y)
                changes.emplace_back(current,display_vector(after.x,after.y));
            if(std::abs(dx)<1e-8&&std::abs(dy)<1e-8)break;
        }
        if(wheel) {
            if(wheel_input_id.empty()) {
                do{wheel_input_id="__local_input_"+std::to_string(w.next_local_id++);}while(w.nodes.count(wheel_input_id));
                w.input.continuous[3]=wheel_input_id;
            }
            const auto& id=wheel_input_id;
            if(!w.nodes.count(id)) {
                Node input;input.id=id;input.name="InputObject";input.class_name="InputObject";input.server_owned=false;
                w.nodes.emplace(id,std::move(input));w.order.push_back(id);
            }
            auto& input=w.nodes.at(id);
            const Json fields={{"UserInputType",input_enum_json("UserInputType",input_enum_item("UserInputType","MouseWheel"))},
                {"KeyCode",input_enum_json("KeyCode",input_enum_item("KeyCode","None"))},
                {"UserInputState",input_enum_json("UserInputState",input_enum_item("UserInputState","Change"))},
                {"Position",{{"$vector3",{x,y,double(steps)}}}},{"Delta",{{"$vector3",{0,0,steps}}}}};
            for(auto it=fields.begin();it!=fields.end();++it) {
                clear_local_property(w,id,it.key());
                if(input.properties[it.key()]!=it.value()){input.properties[it.key()]=it.value();property_events(w,input,it.key());}
            }
            auto* target=n;
            for(int depth=0;target&&depth<128;++depth,target=find_node(w,parent_id(w,*target))) {
                if(target->class_name=="ScreenGui")break;
                if(!gui_object(w,*target)||!contains(layout.box(*target),x,y))continue;
                if(const auto* active=property_value(w,*target,"Active");active&&*active==true)processed=true;
                emit(w,*target,"InputChanged",Json::array({Json{{"$ref",id}}}));
                if(steps)emit(w,*target,steps>0?"MouseWheelForward":"MouseWheelBackward",Json::array({x,y}));
            }
            w.input.location={static_cast<float>(x),static_cast<float>(y)};
            publish_input_event(w,id,3,0,1,input_enum_item("UserInputType","MouseWheel"),processed,steps);
        }
        for(auto& [target,position]:changes)w.local_properties[target->id]["CanvasPosition"]=std::move(position);
        refresh_display_events(w);
    }else if((kind=="pointer_down"||kind=="pointer_up")&&action.size()==2) {
        const bool down=kind=="pointer_down";
        const auto* enabled=property_value(w,*n,"Interactable");
        const bool interactable=!(enabled&&enabled->is_boolean()&&!enabled->get<bool>());
        if(down&&!interactable)throw std::runtime_error("GUI target is disabled");
        if(down&&!w.gui_pointer_target.empty())throw std::runtime_error("GUI pointer is already pressed");
        if(!down&&w.gui_pointer_target!=n->id)throw std::runtime_error("GUI pointer must begin on this target");
        GuiLayoutPass layout{w};auto box=layout.box(*n);
        const double x=box.x+box.width*.5,y=box.y+box.height*.5;
        if(down) {
            // The local display bridge models one mouse button, as the legacy
            // click action below does. Reuse its node rather than retaining an
            // additional world instance for every display click.
            const std::string id="__local_gui_pointer";
            if(!w.nodes.count(id)&&w.nodes.size()>=kMaxNodes)throw std::runtime_error("GUI input instance limit exceeded");
            Node input;input.id=id;
            input.name="InputObject";input.class_name="InputObject";input.server_owned=false;
            input.properties={{"UserInputType",input_enum_json("UserInputType",input_enum_item("UserInputType","MouseButton1"))},
                {"KeyCode",input_enum_json("KeyCode",input_enum_item("KeyCode","None"))},
                {"UserInputState",input_enum_json("UserInputState",input_enum_item("UserInputState","Begin"))},
                {"Position",{{"$vector3",{x,y,0}}}},{"Delta",{{"$vector3",{0,0,0}}}}};
            w.gui_pointer_input=input.id;w.gui_pointer_target=n->id;
            if(!w.nodes.count(id)){w.order.push_back(id);w.nodes.emplace(id,std::move(input));}
            else {clear_local_property(w,id,"UserInputState");w.nodes.at(id).properties=std::move(input.properties);}
        }else {
            auto& input=w.nodes.at(w.gui_pointer_input);
            input.properties["UserInputState"]=input_enum_json("UserInputState",input_enum_item("UserInputState","End"));
            property_events(w,input,"UserInputState");
        }
        const Json input=Json{{"$ref",w.gui_pointer_input}};
        emit(w,*n,down?"InputBegan":"InputEnded",Json::array({input}));
        if(class_is_a(w,n->class_name,"GuiButton")) {
            emit(w,*n,down?"MouseButton1Down":"MouseButton1Up",Json::array({x,y}));
            if(!down&&interactable&&surface_visible(w,*n)){emit(w,*n,"MouseButton1Click");emit(w,*n,"Activated",Json::array({input,1}));}
        }
        if(!down){w.gui_pointer_target.clear();w.gui_pointer_input.clear();}
    }else if(kind=="click"&&action.size()==2&&class_is_a(w,n->class_name,"GuiButton")) {
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
        if(!textbox_editable(w,*n))throw std::runtime_error("TextBox is read-only");
        write_textbox(w,*n,action["text"]);
        authored_textbox_text_changed(w,*n);
        emit(w,*n,"FocusLost",Json::array({true,nullptr}));
    }else throw std::runtime_error("Unsupported GUI interaction");
    virtual_input_record_gui(w,action);
}
