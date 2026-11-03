// Local display surface and the ordinary ScreenGui/GuiObject box model.
// This is layout state, not a renderer. Unsupported layout dependencies fail
// explicitly. Insets are total margins from the full surface (L,T,R,B).
Json validated_display(const Json& supplied) {
    if (!supplied.is_object() || !supplied.contains("size") || supplied.size()>4)
        throw std::runtime_error("display requires size and optional safe-area margins");
    Json out{{"size",Json::array({0,0})},{"device_insets",Json::array({0,0,0,0})},
             {"core_insets",Json::array({0,0,0,0})}};
    for(auto it=supplied.begin();it!=supplied.end();++it) {
        const auto& key=it.key();size_t count=key=="size"?2:4;
        if(key!="size"&&key!="device_insets"&&key!="core_insets"&&key!="topbar_insets")
            throw std::runtime_error("unknown display field");
        if(!it->is_array()||it->size()!=count)throw std::runtime_error("invalid display dimensions");
        for(const auto& v:*it)if(!v.is_number()||!std::isfinite(v.get<double>())||v.get<double>()<0||v.get<double>()>32768)
            throw std::runtime_error("display dimensions must be finite numbers in 0..32768");
        out[key]=*it;
    }
    if(!supplied.contains("core_insets"))out["core_insets"]=out["device_insets"];
    for(const char* key:{"device_insets","core_insets","topbar_insets"})if(out.contains(key)) {
        const auto& m=out[key];
        if(m[0].get<double>()+m[2].get<double>()>out["size"][0].get<double>()||
           m[1].get<double>()+m[3].get<double>()>out["size"][1].get<double>())
            throw std::runtime_error("display insets exceed surface size");
        if(std::strcmp(key,"device_insets")!=0)for(int i=0;i<4;i++)
            if(m[i].get<double>()<out["device_insets"][i].get<double>())
                throw std::runtime_error("UI safe area must be inside device safe area");
    }
    return out;
}
bool gui_object(World& w,const Node& n) {return class_is_a(w,n.class_name,"GuiObject");}
bool padding_property(const std::string& key) {
    return key=="PaddingLeft"||key=="PaddingRight"||key=="PaddingTop"||key=="PaddingBottom";
}
int gui_enum_kind(World& w,const Node& n,const std::string& key) {
    if(n.class_name=="UIAspectRatioConstraint") {
        if(key=="AspectType")return kGuiAspectType;
        if(key=="DominantAxis")return kGuiDominantAxis;
    }
    if(!gui_object(w,n))return -1;
    if(key=="SizeConstraint")return kGuiSizeConstraint;
    if(key=="AutomaticSize")return kGuiAutomaticSize;
    return -1;
}
std::string gui_property_type(World& w,const Node& n,const std::string& key) {
    if(n.class_name=="UICorner"&&key=="CornerRadius")return "UDim";
    if(n.class_name=="UIStroke"&&key=="Color")return "Color3";
    if(n.class_name=="UISizeConstraint"&&(key=="MinSize"||key=="MaxSize"))return "Vector2";
    if(n.class_name=="UIAspectRatioConstraint") {
        if(key=="AspectRatio")return "number";
        if(gui_enum_kind(w,n,key)>=0)return "EnumItem";
    }
    if(n.class_name=="UIPadding"&&padding_property(key))return "UDim";
    if(n.class_name=="UIScale"&&key=="Scale")return "number";
    if(gui_object(w,n)) {
        if(key=="BackgroundColor3"||key=="BorderColor3")return "Color3";
        if((n.class_name=="TextLabel"||n.class_name=="TextButton"||n.class_name=="TextBox")&&
                (key=="TextColor3"||key=="TextStrokeColor3"))return "Color3";
        if((n.class_name=="ImageLabel"||n.class_name=="ImageButton")&&key=="ImageColor3")return "Color3";
        if(key=="Size"||key=="Position")return "UDim2";
        if(key=="AnchorPoint")return "Vector2";
        if(key=="Rotation")return "number";
        if(gui_enum_kind(w,n,key)>=0)return "EnumItem";
    }
    return "";
}
const Json* gui_default_property(World& w,const Node& n,const std::string& key) {
    static const Json zero=0,one=1,origin=Json{{"$vector2",{0,0}}},
        padding=Json{{"$udim",{0,0}}},position=Json{{"$udim2",Json::array({padding,padding})}};
    static const Json unbounded=Json{{"$vector2",Json::array({Json{{"$number","inf"}},Json{{"$number","inf"}}})}};
    static const Json corner=Json{{"$udim",{0,8}}},black=Json{{"$color3",{0,0,0}}};
    if(n.class_name=="UICorner"&&key=="CornerRadius")return &corner;
    if(n.class_name=="UIStroke"&&key=="Color")return &black;
    if(n.class_name=="UISizeConstraint") {
        if(key=="MinSize")return &origin;
        if(key=="MaxSize")return &unbounded;
    }
    if(n.class_name=="UIPadding"&&padding_property(key))return &padding;
    if(n.class_name=="UIScale"&&key=="Scale")return &one;
    if(n.class_name=="UIAspectRatioConstraint") {
        if(key=="AspectRatio")return &one;
        if(key=="AspectType"||key=="DominantAxis")return &zero;
    }
    if(gui_object(w,n)) {
        if(key=="Rotation"||key=="SizeConstraint"||key=="AutomaticSize")return &zero;
        if(key=="AnchorPoint")return &origin;
        if(key=="Position")return &position;
    }
    return nullptr; // Class-specific default sizes are not inferred.
}
int gui_enum_value(World& w,const Node& n,const std::string& key) {
    int kind=gui_enum_kind(w,n,key);
    if(kind<0)throw std::runtime_error("unknown GUI enum property");
    const Json* stored=property_value(w,n,key);
    if(!stored)throw std::runtime_error("GUI enum property unavailable");
    const Json* raw=stored;
    if(stored->is_object()&&stored->contains("$enum")) {
        const auto& e=stored->at("$enum");
        if(!e.is_object()||e.value("type","")!=kGeometryEnums[kind].name||!e.contains("value"))
            throw std::runtime_error("GUI enum type mismatch");
        raw=&e.at("value");
    }
    if(!raw->is_number_integer()||*raw<0||*raw>=kGeometryEnums[kind].count)
        throw std::runtime_error("invalid GUI enum value");
    int value=raw->get<int>();
    if(stored->is_object()&&stored->at("$enum").contains("name")&&
       stored->at("$enum").at("name")!=kGeometryEnums[kind].names[value])
        throw std::runtime_error("GUI enum name/value mismatch");
    return value;
}
std::array<double,2> gui_size_bound(const Json& value,bool maximum) {
    if(!value.is_object()||!value.contains("$vector2"))throw std::runtime_error("size constraint requires Vector2");
    const auto& a=value.at("$vector2");
    if(!a.is_array()||a.size()!=2)throw std::runtime_error("invalid size constraint vector");
    std::array<double,2> result;
    for(int i=0;i<2;i++) {
        if(maximum&&a[i]==Json{{"$number","inf"}})result[i]=std::numeric_limits<double>::infinity();
        else {
            if(!a[i].is_number())throw std::runtime_error("size constraint components must be nonnegative; only MaxSize permits infinity");
            result[i]=a[i].get<double>();
            if(!std::isfinite(result[i])||result[i]<0)throw std::runtime_error("size constraint components must be finite and nonnegative");
        }
    }
    return result;
}
void validate_gui_bounds(const std::array<double,2>& minimum,const std::array<double,2>& maximum) {
    if(minimum[0]>maximum[0]||minimum[1]>maximum[1])throw std::runtime_error("MinSize must not exceed MaxSize");
}
bool validate_gui_assignment(lua_State* L,World& w,const Node& n,const std::string& key) {
    auto type=gui_property_type(w,n,key);if(type.empty())return false;
    if(type=="EnumItem") {
        if(!has_meta(L,3,kGeometryItemMeta)||
           static_cast<GeometryEnumItem*>(lua_touserdata(L,3))->kind!=gui_enum_kind(w,n,key))
            throw std::runtime_error("incorrect enum type for GUI property");
    } else if(type=="number") {
        if(lua_type(L,3)!=LUA_TNUMBER||!std::isfinite(lua_tonumber(L,3)))throw std::runtime_error("GUI property requires a finite number");
        if(n.class_name=="UIScale"&&lua_tonumber(L,3)<0)throw std::runtime_error("negative UIScale is unsupported");
        if(n.class_name=="UIAspectRatioConstraint"&&key=="AspectRatio"&&lua_tonumber(L,3)<=0)
            throw std::runtime_error("AspectRatio must be greater than zero");
    } else if(!is_authored_value(L,3)||type!=luaL_typename(L,3))
        throw std::runtime_error("incorrect datatype for GUI property");
    if(n.class_name=="UISizeConstraint") {
        Json supplied;capture_authored_value(L,3,supplied);
        const Json* min=key=="MinSize"?&supplied:property_value(w,n,"MinSize");
        const Json* max=key=="MaxSize"?&supplied:property_value(w,n,"MaxSize");
        if(!min||!max)throw std::runtime_error("size constraint bounds unavailable");
        validate_gui_bounds(gui_size_bound(*min,false),gui_size_bound(*max,true));
    }
    return true;
}
bool display_property(World& w,const Node& n,const std::string& key) {
    return ((n.class_name=="ScreenGui"||gui_object(w,n))&&
         (key=="AbsoluteSize"||key=="AbsolutePosition"||key=="AbsoluteRotation"));
}
Json display_vector(double x,double y) {return Json{{"$vector2",Json::array({x,y})}};}
// Keep the camera projection backend and GUI surface on the same host input.
Json display_from_viewport(const Json& vp) {
    const auto& d=vp.at("device_safe_insets");const auto& g=vp.at("gui_inset");
    return Json{{"size",vp.at("size")},{"device_insets",d},
        {"core_insets",Json::array({d[0].get<double>()+g[0].get<double>(),d[1].get<double>()+g[1].get<double>(),d[2],d[3]})}};
}
void update_display(World& w,const Json& supplied) {
    auto profile=validated_display(supplied);
    const auto& s=profile["size"];const auto& d=profile["device_insets"];const auto& c=profile["core_insets"];
    // A zero-area surface is detached. Preserve the last camera viewport, as
    // inactive cameras do, while GUI layout reports the empty surface.
    if(s[0].get<double>()>c[0].get<double>()+c[2].get<double>()&&s[1].get<double>()>c[1].get<double>()+c[3].get<double>())
        update_viewport(w,Json{{"size",s},{"device_safe_insets",d},
            {"gui_inset",Json::array({c[0].get<double>()-d[0].get<double>(),c[1].get<double>()-d[1].get<double>()})}});
    w.display=std::move(profile);
}
double layout_number(World& w,const Node& n,const char* key,double fallback) {
    const auto* v=property_value(w,n,key);if(!v)return fallback;
    if(!v->is_number()||!std::isfinite(v->get<double>()))throw std::runtime_error(std::string("invalid layout property: ")+key);
    return v->get<double>();
}
struct GuiBox {double x=0,y=0,width=0,height=0,rotation=0,scale=1;bool attached=false;};
struct GuiLayoutPass {
    World& w;
    std::unordered_map<std::string,GuiBox> boxes;
    std::unordered_map<std::string,std::vector<Node*>> children;
    bool indexed=false;
    void index() {
        if(indexed)return;indexed=true;
        for(auto& [id,n]:w.nodes)if(!n.destroyed)children[parent_id(w,n)].push_back(&n);
    }
    Node* component(Node& n,const char* cls) {
        index();Node* found=nullptr;
        for(auto* child:children[n.id])if(child->class_name==cls) {
            if(found)throw std::runtime_error(std::string("multiple active GUI components are unsupported: ")+cls);
            found=child;
        }
        return found;
    }
    double dimension(const Json& value,double extent,double scale) {
        const auto& a=value.at("$udim");
        if(!a.is_array()||a.size()!=2||!a[0].is_number()||!a[1].is_number())throw std::runtime_error("invalid GUI UDim");
        double result=a[0].get<double>()*extent+a[1].get<double>()*scale;
        if(!std::isfinite(result))throw std::runtime_error("nonfinite GUI layout");
        return result;
    }
    double automatic_offset(const Json& value) {
        const auto& a=value.at("$udim");
        if(!a.is_array()||a.size()!=2||!a[0].is_number()||!a[1].is_number())throw std::runtime_error("invalid GUI UDim");
        if(a[0].get<double>()!=0)throw std::runtime_error("automatic GUI sizing with scale content is not implemented");
        return dimension(value,0,1);
    }
    std::array<double,2> automatic_content(Node& n,int automatic) {
        if(n.class_name!="Frame")throw std::runtime_error("automatic GUI sizing for this class is not implemented");
        index();std::array<double,2> extent{0,0};
        for(auto* child:children[n.id]) {
            const auto& cls=child->class_name;
            if(class_is_a(w,cls,"UILayout")||cls=="UIListLayout"||cls=="UIGridLayout"||cls=="UIPageLayout"||cls=="UITableLayout")
                throw std::runtime_error("GUI content layout is not implemented: "+cls);
            if(!gui_object(w,*child))continue;
            if(gui_enum_value(w,*child,"AutomaticSize")!=0)throw std::runtime_error("nested automatic GUI sizing is not implemented");
            if(layout_number(w,*child,"Rotation",0)!=0)throw std::runtime_error("automatic GUI sizing with rotated content is not implemented");
            const auto* visible=property_value(w,*child,"Visible");
            if(!visible||!visible->is_boolean())throw std::runtime_error("automatic GUI content visibility unavailable");
            if(!visible->get<bool>())throw std::runtime_error("automatic GUI sizing with invisible content is not implemented");
            if(component(*child,"UIScale"))throw std::runtime_error("automatic GUI sizing with scaled content is not implemented");
            for(auto* modifier:children[child->id])if(class_is_a(w,modifier->class_name,"UIConstraint")&&modifier->class_name!="UITextSizeConstraint")
                throw std::runtime_error("automatic GUI sizing with constrained content is not implemented");
            const auto* size=property_value(w,*child,"Size");const auto* position=property_value(w,*child,"Position");
            if(!size||!position)throw std::runtime_error("automatic GUI content Size or Position unavailable");
            const auto* anchor=property_value(w,*child,"AnchorPoint");
            if(!anchor)throw std::runtime_error("automatic GUI content AnchorPoint unavailable");
            for(int axis=0;axis<2;axis++)if(automatic&(axis+1)) {
                double anchor_component=anchor->at("$vector2").at(axis).get<double>();
                if(!std::isfinite(anchor_component)||anchor_component!=0)
                    throw std::runtime_error("automatic GUI sizing with anchored content is not implemented");
                double offset=automatic_offset(position->at("$udim2").at(axis));
                double size_offset=std::max(0.0,automatic_offset(size->at("$udim2").at(axis)));
                extent[axis]=std::max(extent[axis],offset+size_offset);
            }
        }
        if(auto* padding=component(n,"UIPadding")) {
            if(automatic&1)extent[0]+=automatic_offset(*property_value(w,*padding,"PaddingLeft"))+
                automatic_offset(*property_value(w,*padding,"PaddingRight"));
            if(automatic&2)extent[1]+=automatic_offset(*property_value(w,*padding,"PaddingTop"))+
                automatic_offset(*property_value(w,*padding,"PaddingBottom"));
        }
        return extent;
    }
    GuiBox content(Node& n,GuiBox b) {
        if(auto* padding=component(n,"UIPadding")) {
            double left=dimension(*property_value(w,*padding,"PaddingLeft"),b.width,b.scale);
            double right=dimension(*property_value(w,*padding,"PaddingRight"),b.width,b.scale);
            double top=dimension(*property_value(w,*padding,"PaddingTop"),b.height,b.scale);
            double bottom=dimension(*property_value(w,*padding,"PaddingBottom"),b.height,b.scale);
            b.x+=left;b.y+=top;b.width=std::max(0.0,b.width-left-right);b.height=std::max(0.0,b.height-top-bottom);
        }
        return b;
    }
    GuiBox box(Node& n,int depth=0) {
        if(depth>128)throw std::runtime_error("GUI ancestry exceeds layout depth limit");
        if(auto it=boxes.find(n.id);it!=boxes.end())return it->second;
        GuiBox b;
        if(n.destroyed)return b;
        if(n.class_name=="ScreenGui") {
            auto* parent=find_node(w,parent_id(w,n));int hops=0;
            while(parent&&parent->class_name!="PlayerGui"&&parent->class_name!="CoreGui"&&++hops<=128)
                parent=find_node(w,parent_id(w,*parent));
            if(!parent||hops>128)return b; // No attached GUI surface.
            b.attached=w.display["size"][0].get<double>()>0&&w.display["size"][1].get<double>()>0;
            int mode=2;
            const auto* explicit_mode=property_value(w,n,"ScreenInsets");
            if(explicit_mode) {
                if(!explicit_mode->is_number_integer())throw std::runtime_error("unsupported ScreenInsets encoding");
                mode=explicit_mode->get<int>();
            } else if(const auto* ignore=property_value(w,n,"IgnoreGuiInset")) {
                if(!ignore->is_boolean())throw std::runtime_error("invalid IgnoreGuiInset");
                if(ignore->get<bool>())mode=1;
            }
            Json margins=Json::array({0,0,0,0});
            if(mode==1)margins=w.display["device_insets"];
            else if(mode==2)margins=w.display["core_insets"];
            else if(mode==3) {
                if(!w.display.contains("topbar_insets"))throw std::runtime_error("TopbarSafeInsets requires host topbar area");
                margins=w.display["topbar_insets"];
            } else if(mode!=0)throw std::runtime_error("unknown ScreenInsets value");
            b.x=margins[0].get<double>()-w.display["core_insets"][0].get<double>();
            b.y=margins[1].get<double>()-w.display["core_insets"][1].get<double>();
            b.width=w.display["size"][0].get<double>()-margins[0].get<double>()-margins[2].get<double>();
            b.height=w.display["size"][1].get<double>()-margins[1].get<double>()-margins[3].get<double>();
        } else if(gui_object(w,n)) {
            auto* p=find_node(w,parent_id(w,n));
            if(!p)return b;
            if(p->class_name!="ScreenGui"&&!gui_object(w,*p))throw std::runtime_error("GUI parent has no supported layout surface");
            if(p->class_name=="ScrollingFrame")throw std::runtime_error("ScrollingFrame canvas layout is not implemented");
            auto parent=box(*p,depth+1);
            if(!parent.attached)return b;
            if(parent.rotation!=0)throw std::runtime_error("rotated ancestor layout is not implemented");
            int automatic=gui_enum_value(w,n,"AutomaticSize");
            int constraint=gui_enum_value(w,n,"SizeConstraint");
            index();
            // A layout belongs to a container's contents, not its outer box.
            // Size constraints belong to the individual object being sized.
            for(auto* child:children[p->id]) {
                const auto& cls=child->class_name;
                if(class_is_a(w,cls,"UILayout")||cls=="UIListLayout"||cls=="UIGridLayout"||cls=="UIPageLayout"||cls=="UITableLayout")
                    throw std::runtime_error("GUI content layout is not implemented: "+cls);
            }
            for(auto* child:children[n.id]) {
                const auto& cls=child->class_name;
                if(cls!="UITextSizeConstraint"&&cls!="UIAspectRatioConstraint"&&cls!="UISizeConstraint"&&class_is_a(w,cls,"UIConstraint"))
                    throw std::runtime_error("GUI size constraint is not implemented: "+cls);
            }
            if(gui_object(w,*p))parent=content(*p,parent);
            const auto* size=property_value(w,n,"Size");const auto* position=property_value(w,n,"Position");
            if(!size||!position)throw std::runtime_error("GUI Size or Position unavailable in snapshot");
            double ax=0,ay=0;
            if(const auto* anchor=property_value(w,n,"AnchorPoint")) {
                const auto& a=anchor->at("$vector2");ax=a.at(0).get<double>();ay=a.at(1).get<double>();
                if(!std::isfinite(ax)||!std::isfinite(ay))throw std::runtime_error("invalid GUI AnchorPoint");
            }
            double own_scale=1;
            if(auto* scale=component(n,"UIScale"))own_scale=layout_number(w,*scale,"Scale",1);
            if(own_scale<0)throw std::runtime_error("negative UIScale is unsupported");
            b.scale=parent.scale*own_scale;b.attached=true;
            b.width=std::max(0.0,dimension(size->at("$udim2").at(0),constraint==2?parent.height:parent.width,parent.scale)*own_scale);
            b.height=std::max(0.0,dimension(size->at("$udim2").at(1),constraint==1?parent.width:parent.height,parent.scale)*own_scale);
            if(automatic) {
                auto content_extent=automatic_content(n,automatic);
                if(automatic&1)b.width=std::max(b.width,content_extent[0]*b.scale);
                if(automatic&2)b.height=std::max(b.height,content_extent[1]*b.scale);
            }
            if(auto* bounds=component(n,"UISizeConstraint")) {
                if(component(n,"UIAspectRatioConstraint"))throw std::runtime_error("combined size and aspect constraints are not implemented");
                const auto* min=property_value(w,*bounds,"MinSize");
                const auto* max=property_value(w,*bounds,"MaxSize");
                if(!min||!max)throw std::runtime_error("size constraint bounds unavailable");
                auto minimum=gui_size_bound(*min,false),maximum=gui_size_bound(*max,true);
                validate_gui_bounds(minimum,maximum);
                // Constraints are authored in the same pixel coordinate system
                // as offsets. Apply cumulative UIScale to both finite bounds.
                auto clamp_axis=[&](double size,int axis) {
                    if(b.scale==0)return 0.0;
                    double lower=minimum[axis]*b.scale;
                    double upper=std::isinf(maximum[axis])?maximum[axis]:maximum[axis]*b.scale;
                    return std::max(lower,std::min(size,upper));
                };
                b.width=clamp_axis(b.width,0);b.height=clamp_axis(b.height,1);
            }
            if(auto* aspect=component(n,"UIAspectRatioConstraint")) {
                double ratio=layout_number(w,*aspect,"AspectRatio",1);
                if(ratio<=0)throw std::runtime_error("AspectRatio must be greater than zero");
                int mode=gui_enum_value(w,*aspect,"AspectType");
                gui_enum_value(w,*aspect,"DominantAxis"); // Validate replicated enum values too.
                if(mode!=0)throw std::runtime_error("ScaleWithParentSize aspect layout is not implemented");
                // Fit the largest ratio-preserving rectangle inside the authored
                // box, before positioning its anchor or sizing descendants.
                if(b.width==0||b.height==0)b.width=b.height=0;
                else if(b.width/b.height>ratio)b.width=b.height*ratio;
                else b.height=b.width/ratio;
            }
            b.x=parent.x+dimension(position->at("$udim2").at(0),parent.width,parent.scale)-ax*b.width;
            b.y=parent.y+dimension(position->at("$udim2").at(1),parent.height,parent.scale)-ay*b.height;
            b.rotation=layout_number(w,n,"Rotation",0);
            if(!std::isfinite(b.x)||!std::isfinite(b.y)||!std::isfinite(b.width)||!std::isfinite(b.height)||!std::isfinite(b.scale))
                throw std::runtime_error("nonfinite GUI geometry");
        } else throw std::runtime_error("unsupported GUI layout class");
        boxes[n.id]=b;return b;
    }
    Json value(Node& n,const std::string& key) {
        auto b=box(n);
        if(key=="AbsoluteSize")return display_vector(b.width,b.height);
        if(key=="AbsolutePosition")return display_vector(b.x,b.y);
        return b.rotation;
    }
};
void watch_display_property(World& w,Node& n,const std::string& key) {
    if(!display_property(w,n,key))return;
    auto& watched=w.display_watches[n.id];
    if(watched.contains(key))return;
    try{GuiLayoutPass pass{w};watched[key]=pass.value(n,key);}catch(const std::exception&){watched[key]=nullptr;}
}
void refresh_display_events(World& w) {
    GuiLayoutPass pass{w};
    for(auto it=w.display_watches.begin();it!=w.display_watches.end();) {
        auto* n=find_node(w,it->first);
        if(!n||n->destroyed){it=w.display_watches.erase(it);continue;}
        for(auto p=it->second.begin();p!=it->second.end();++p) {
            Json current;
            try{current=pass.value(*n,p.key());}catch(const std::exception&){current=nullptr;}
            if(current!=p.value()){p.value()=current;property_events(w,*n,p.key());}
        }
        ++it;
    }
}
int gui_get_inset(lua_State* L) {
    auto* h=checked_handle(L,1);
    if(h->node->class_name!="GuiService"){luaL_error(L,"GetGuiInset requires GuiService");return 0;}
    const auto& m=h->world->display["core_insets"];
    push_json(L,*h->world,display_vector(m[0].get<double>(),m[1].get<double>()));
    push_json(L,*h->world,display_vector(m[2].get<double>(),m[3].get<double>()));return 2;
}
