// Canonical model scaling. Cache the actual values at a known scale rather
// than repeatedly multiplying the last rounded output. All writes are staged.
// Physical property rules are measured in Studio 0.745; see the reference
// fixture. Non-avatar animation integration remains explicitly unsupported.
constexpr size_t kModelScaleCacheBytes=8*1024*1024;
std::string character_motion_root_id(World& w,Node& part);
double model_scale_value(World& w,Node& n) {
    const auto* value=property_value(w,n,"Scale");
    if(!value)return 1;
    if(!value->is_number()||!std::isfinite(value->get<double>())||value->get<double>()<=0)
        throw std::runtime_error("model canonical scale is invalid");
    return value->get<double>();
}
int model_get_scale(lua_State* L) {
    auto* h=checked_pivot_object(L,true);
    try {
        if(lua_gettop(L)!=1)throw std::runtime_error("expected no arguments");
        lua_pushnumber(L,model_scale_value(*h->world,*h->node));return 1;
    }catch(const std::exception& e){return luaL_error(L,"GetScale: %s",e.what()),0;}
}
void check_model_scale_cache(World& w,const std::unordered_map<std::string,Json>& replacements) {
    size_t bytes=0;
    auto add=[&](const Json& value){const size_t size=value.dump().size();
        if(size>kModelScaleCacheBytes-bytes)throw std::runtime_error("model scale cache limit exceeded");bytes+=size;};
    for(const auto& [id,value]:w.model_scale_caches)if(!replacements.count(id))add(value);
    for(const auto& [id,value]:replacements)add(value);
}
void clone_model_scale_caches(World& w,const std::unordered_map<std::string,std::string>& ids) {
    std::unordered_map<std::string,Json> copies;
    for(const auto& [old_id,new_id]:ids) {
        auto source=w.model_scale_caches.find(old_id);if(source==w.model_scale_caches.end())continue;
        Json cache=source->second;Json rows=Json::object();
        for(auto row=cache["rows"].begin();row!=cache["rows"].end();++row)
            if(auto mapped=ids.find(row.key());mapped!=ids.end())rows[mapped->second]=row.value();
        cache["rows"]=std::move(rows);copies[new_id]=std::move(cache);
    }
    check_model_scale_cache(w,copies);
    for(auto& [id,cache]:copies)w.model_scale_caches[id]=std::move(cache);
}
enum class ModelScaleKind {Scalar,Vector,Vector2,LocalFrame,WorldFrame,Range,Sequence};
struct ModelScaleField {const char* name;ModelScaleKind kind;bool required=false;double exponent=1;bool infinite=false;};
bool model_force_scale_fields(const Node& n,std::vector<ModelScaleField>& fields) {
    using K=ModelScaleKind;const auto& c=n.class_name;
    auto add=[&](const char* name,K kind=K::Scalar,double exponent=1.){fields.push_back({name,kind,false,exponent,true});};
    auto limits=[&](std::initializer_list<const char*> names){for(auto name:names)add(name,K::Scalar,3);};
    auto lengths=[&](std::initializer_list<const char*> names){for(auto name:names)add(name);};
    if(c=="BodyForce"||c=="BodyThrust"||c=="VectorForce") {
        add("Force",K::Vector,3);if(c=="BodyThrust")add("Location",K::Vector);
    } else if(c=="BodyVelocity"||c=="BodyPosition"||c=="BodyGyro"||c=="BodyAngularVelocity") {
        add(c=="BodyGyro"||c=="BodyAngularVelocity"?"MaxTorque":"MaxForce",K::Vector,3);
        add("P",K::Scalar,2);
        if(c=="BodyPosition"||c=="BodyGyro")add("D",K::Scalar,2.5);
        if(c=="BodyVelocity")add("Velocity",K::Vector);
        // World targets and angular velocities remain unchanged.
    } else if(c=="RocketPropulsion") {
        add("MaxTorque",K::Vector,3);limits({"MaxThrust"});add("MaxSpeed");
        for(auto name:{"ThrustP","TurnP"})add(name,K::Scalar,2);
        for(auto name:{"ThrustD","TurnD"})add(name,K::Scalar,2.5);
    } else if(c=="Torque")add("Torque",K::Vector,3);
    else if(c=="LineForce") {add("Magnitude");limits({"MaxForce"});}
    else if(c=="LinearVelocity") {
        limits({"MaxForce"});add("MaxAxesForce",K::Vector,3);add("MaxPlanarAxesForce",K::Vector2,3);
        add("LineVelocity");add("VectorVelocity",K::Vector);add("PlaneVelocity",K::Vector2);
    } else if(c=="AngularVelocity"||c=="AlignOrientation")limits({"MaxTorque"});
    else if(c=="AlignPosition") {limits({"MaxForce"});add("MaxAxesForce",K::Vector,3);add("MaxVelocity");}
    else if(c=="AnimationConstraint") {limits({"MaxForce","MaxTorque"});add("Transform",K::LocalFrame);}
    else if(c=="HingeConstraint") {limits({"MotorMaxTorque","ServoMaxTorque"});add("Radius");}
    else if(c=="PrismaticConstraint"||c=="CylindricalConstraint") {
        limits({"MotorMaxForce","ServoMaxForce"});lengths({"LowerLimit","UpperLimit","TargetPosition","Speed","Velocity","Size"});
        if(c=="CylindricalConstraint")limits({"MotorMaxTorque","ServoMaxTorque"});
    } else if(c=="SpringConstraint"||c=="TorsionSpringConstraint") {
        limits({c=="SpringConstraint"?"MaxForce":"MaxTorque"});add("Stiffness",K::Scalar,2);add("Damping",K::Scalar,2.5);add("Radius");
        if(c=="SpringConstraint")lengths({"FreeLength","MinLength","MaxLength","Thickness"});
    } else if(c=="RopeConstraint") {limits({"WinchForce"});lengths({"Length","Thickness","WinchSpeed","WinchTarget"});}
    else if(c=="RodConstraint")lengths({"Length","Thickness"});
    else if(c=="BallSocketConstraint") {limits({"MaxFrictionTorque"});add("Radius");}
    else if(c=="UniversalConstraint")add("Radius");
    else if(c!="PlaneConstraint"&&c!="NoCollisionConstraint"&&c!="RigidConstraint"&&c!="WeldConstraint")return false;
    return true;
}
std::vector<ModelScaleField> model_scale_fields(World& w,Node& n,Node& root) {
    using K=ModelScaleKind;std::vector<ModelScaleField> fields;
    auto add=[&](const char* name,K kind,bool required=false){fields.push_back({name,kind,required});};
    if(pivot_part(w,n)) {
        if(n.class_name=="Terrain")throw std::runtime_error("ScaleTo cannot scale Terrain");
        add("CFrame",K::WorldFrame,true);add("Size",K::Vector,true);add("PivotOffset",K::LocalFrame);
        add("AssemblyLinearVelocity",K::Vector);add("Velocity",K::Vector);
    } else if(attachment_node(w,n)) {
        add("CFrame",K::LocalFrame,true);if(n.class_name=="Bone")add("Transform",K::LocalFrame);
    } else if(class_is_a(w,n.class_name,"JointInstance")) {
        add("C0",K::LocalFrame,true);add("C1",K::LocalFrame,true);
        if(n.class_name=="Motor6D")add("Transform",K::LocalFrame);
    } else if(class_is_a(w,n.class_name,"BodyMover")||class_is_a(w,n.class_name,"Constraint")) {
        if(!model_force_scale_fields(n,fields))throw std::runtime_error("ScaleTo force/constraint scaling is not implemented for "+n.class_name);
    } else if(n.class_name=="Animator") {
        // The active local-avatar animation pipeline consumes canonical Scale
        // and the detached joint geometry. Other actors do not yet have that
        // pose integration, so keep their scaling transactional and explicit.
        auto* player=find_node(w,w.local_player);
        const Json* character=player?property_value(w,*player,"Character"):nullptr;
        auto* humanoid=find_node(w,parent_id(w,n));
        if(!character||!character->is_object()||!character->contains("$ref")||
           !(*character)["$ref"].is_string()||
           !humanoid||humanoid->class_name!="Humanoid"||
           parent_id(w,*humanoid)!=(*character)["$ref"].get<std::string>())
            throw std::runtime_error("ScaleTo animation scaling is not implemented outside the local character");
    } else if(class_is_a(w,n.class_name,"DataModelMesh")) {
        add("Offset",K::Vector);
        // Primitive mesh scale is relative to its parent's Size, which is
        // already scaled. File meshes use their imported dimensions instead.
        if(n.class_name=="SpecialMesh") {
            const auto* value=property_value(w,n,"MeshType");
            if(!value)throw std::runtime_error("SpecialMesh.MeshType is unavailable for scaling");
            if(value->is_object()&&value->contains("$enum")) {
                const auto& item=(*value)["$enum"];
                if(!item.is_object()||item.value("type","")!="MeshType"||!item.contains("value"))throw std::runtime_error("invalid MeshType");
                value=&item["value"];
            }
            if(!value->is_number_integer())throw std::runtime_error("invalid MeshType");
            const int kind=value->get<int>();
            if(kind==5)add("Scale",K::Vector);
            else if(kind!=2&&kind!=3&&kind!=4&&kind!=6)
                throw std::runtime_error("ScaleTo nonstandard mesh scaling is not implemented");
        } else if(n.class_name=="FileMesh")add("Scale",K::Vector);
        else if(n.class_name!="BlockMesh"&&n.class_name!="CylinderMesh")
            throw std::runtime_error("ScaleTo mesh scaling is not implemented for "+n.class_name);
    } else if(n.class_name=="Sound") {
        for(const char* key:{"RollOffMinDistance","RollOffMaxDistance","EmitterSize","MinDistance","MaxDistance"})add(key,K::Scalar);
    } else if(n.class_name=="ParticleEmitter") {
        add("Size",K::Sequence);add("Speed",K::Range);add("Acceleration",K::Vector);add("ZOffset",K::Scalar);
    } else if(n.class_name=="Beam") {
        for(const char* key:{"Width0","Width1","CurveSize0","CurveSize1","TextureLength","ZOffset"})add(key,K::Scalar);
    } else if(n.class_name=="Trail") {
        for(const char* key:{"MinLength","MaxLength","TextureLength"})add(key,K::Scalar);
    } else if(n.class_name=="Humanoid") {
        for(auto name:{"HipHeight","WalkSpeed","JumpHeight","HealthDisplayDistance","NameDisplayDistance"})add(name,K::Scalar);
        fields.push_back({"JumpPower",K::Scalar,false,.5});add("CameraOffset",K::Vector);
    }
    if(pivot_model(w,n)&& &n!=&root) {
        add("Scale",K::Scalar,true);
        if(property_value(w,n,"WorldPivot")&&(local_value(w,n,"WorldPivot")||origin(w,n,"WorldPivot")!="class_default"))
            add("WorldPivot",K::WorldFrame);
    }
    return fields;
}
double scale_finite(double value,double ratio) {
    double result=value*ratio;
    if(!std::isfinite(value)||!std::isfinite(result))throw std::runtime_error("scaled value must be finite");
    return result;
}
Json model_scaled_value(const Json& base,ModelScaleKind kind,double ratio,const CFrameValue& pivot,bool infinite=false) {
    using K=ModelScaleKind;
    auto component=[&](const Json& raw)->Json {
        const double value=json_number(raw);
        if(infinite&&std::isinf(value))return raw;
        return scale_finite(value,ratio);
    };
    if(kind==K::LocalFrame||kind==K::WorldFrame) {
        auto frame=pivot_decode(base);
        if(ratio==1)return base;
        for(int i=0;i<3;++i)frame.p[i]=kind==K::LocalFrame?scale_finite(frame.p[i],ratio):
            pivot.p[i]+scale_finite(frame.p[i]-pivot.p[i],ratio);
        auto out=pivot_encode(frame);pivot_decode(out);return out;
    }
    if(kind==K::Scalar) {
        return component(base);
    }
    if(kind==K::Vector||kind==K::Vector2) {
        const char* key=kind==K::Vector?"$vector3":"$vector2";const size_t count=kind==K::Vector?3:2;
        if(!base.is_object()||!base.contains(key)||!base[key].is_array()||base[key].size()!=count)
            throw std::runtime_error("scaled property requires a vector");
        Json out=base;for(auto& value:out[key])value=component(value);return out;
    }
    Json out=base;
    if(kind==K::Range) {
        if(!out.is_object()||!out.contains("$number_range")||!out["$number_range"].is_array()||out["$number_range"].size()!=2)
            throw std::runtime_error("scaled property requires NumberRange");
        for(auto& value:out["$number_range"])value=scale_finite(json_number(value),ratio);
    } else {
        if(!out.is_object()||!out.contains("$number_sequence")||!out["$number_sequence"].is_array())
            throw std::runtime_error("scaled property requires NumberSequence");
        for(auto& point:out["$number_sequence"]) {
            if(!point.is_object()||!point.contains("$number_keypoint")||!point["$number_keypoint"].is_array()||point["$number_keypoint"].size()!=3)
                throw std::runtime_error("invalid scale sequence keypoint");
            for(int i:{1,2})point["$number_keypoint"][i]=scale_finite(json_number(point["$number_keypoint"][i]),ratio);
        }
    }
    return out;
}
int model_scale_to(lua_State* L) {
    auto* h=checked_pivot_object(L,true);
    try {
        if(lua_gettop(L)!=2||lua_type(L,2)!=LUA_TNUMBER)throw std::runtime_error("expected one positive finite number");
        const double target=lua_tonumber(L,2);
        if(!std::isfinite(target)||target<=0)throw std::runtime_error("scale must be positive and finite");
        auto& w=*h->world;auto& root=*h->node;const double current=model_scale_value(w,root);
        if(current==target)return 0;
        auto* primary=model_primary(w,root);
        if(primary&&!is_descendant_of(w,*primary,root.id))throw std::runtime_error("PrimaryPart must be inside the model");
        const auto pivot=object_pivot(w,root);const auto pivot_json=pivot_encode(pivot);pivot_decode(pivot_json);
        auto nodes=pivot_descendants(w,root,true);
        Json cache{{"rows",Json::object()}};
        if(auto old=w.model_scale_caches.find(root.id);old!=w.model_scale_caches.end()&&old->second.value("scale",0.)==current)cache=old->second;
        const bool same_pivot=cache.contains("pivot")&&cache["pivot"]==pivot_json;
        Json rows=Json::object();std::vector<EnginePoseWrite> writes;std::vector<Json> entries;size_t bytes=0;
        for(auto* node:nodes) {
            Json before=Json::object(),after=Json::object();
            for(const auto& field:model_scale_fields(w,*node,root)) {
                const auto* known=property_value(w,*node,field.name);Json value;
                if(pivot_model(w,*node)&&std::string_view(field.name)=="Scale")value=model_scale_value(w,*node);
                else if(known)value=*known;
                else if(field.required)throw std::runtime_error(std::string(field.name)+" unavailable for "+node->class_name);
                else continue;
                Json entry;
                if(cache["rows"].contains(node->id)&&cache["rows"][node->id].contains(field.name))entry=cache["rows"][node->id][field.name];
                if(entry.is_null()||entry["last"]!=value||(field.kind==ModelScaleKind::WorldFrame&&!same_pivot))
                    entry=Json{{"base",value},{"at_scale",current},{"pivot",pivot_json}};
                const double ratio=std::pow(target/entry["at_scale"].get<double>(),field.exponent);
                auto scaled=model_scaled_value(entry["base"],field.kind,ratio,pivot_decode(entry["pivot"]),field.infinite);
                if(pivot_part(w,*node)&&std::string_view(field.name)=="Size")
                    for(double size:part_vector(scaled))if(size<=0)throw std::runtime_error("scaled part Size must be positive");
                entry["last"]=scaled;rows[node->id][field.name]=std::move(entry);
                before[field.name]=value;after[field.name]=scaled;
                if(std::string_view(field.name)=="CFrame") {
                    auto old_fields=attachment_node(w,*node)?attachment_local_state(w,*node):previous_part_transform(w,*node);
                    auto fields=attachment_node(w,*node)?attachment_local_fields(scaled):part_fields_from_frame(scaled);
                    for(auto it=fields.begin();it!=fields.end();++it){after[it.key()]=it.value();if(old_fields.contains(it.key()))before[it.key()]=old_fields[it.key()];}
                }
            }
            if(node==&root){before["Scale"]=current;after["Scale"]=target;}
            for(auto it=after.begin();it!=after.end();) {
                if(before.contains(it.key())&&before[it.key()]==it.value())it=after.erase(it);else ++it;
            }
            if(after.empty())continue;
            for(auto it=after.begin();it!=after.end();++it) {
                Json entry{{"kind","set"},{"id",node->id},{"property",it.key()},{"value",it.value()}};
                const size_t size=entry.dump().size();
                if(entries.size()>=kMaxMutations-w.mutations.size()||size>kMaxMutationBytes-w.mutation_bytes-bytes)
                    throw std::runtime_error("local mutation output limit exceeded");
                bytes+=size;entries.push_back(std::move(entry));
            }
            writes.push_back({node,std::move(before),std::move(after)});
        }
        // Determine the getter's final pivot from staged state before committing.
        // In particular, changing a primary PivotOffset must not make the cache
        // mistake cancellation roundoff for an independently edited pivot.
        std::unordered_map<std::string,const Json*> staged;
        for(const auto& write:writes)staged[write.node->id]=&write.after;
        auto final_value=[&](Node& n,const char* key)->const Json* {
            auto found=staged.find(n.id);
            if(found!=staged.end()&&found->second->contains(key))return &found->second->at(key);
            return property_value(w,n,key);
        };
        auto final_pivot=pivot;
        if(primary)final_pivot=pivot_product(pivot_decode(*final_value(*primary,"CFrame")),pivot_decode(*final_value(*primary,"PivotOffset")));
        else if(!local_value(w,root,"WorldPivot")&&origin(w,root,"WorldPivot")=="class_default") {
            FrameVector low{},high{};bool any=false;
            for(auto* node:nodes)if(pivot_part(w,*node)) {
                auto cf=pivot_decode(*final_value(*node,"CFrame"));auto size=part_vector(*final_value(*node,"Size"));
                for(int i=0;i<3;++i) {
                    double extent=0;for(int j=0;j<3;++j)extent+=std::abs(cf.r[i*3+j])*size[j]/2;
                    const double lo=cf.p[i]-extent,hi=cf.p[i]+extent;
                    if(!std::isfinite(lo)||!std::isfinite(hi))throw std::runtime_error("scaled bounds must be finite");
                    if(!any){low[i]=lo;high[i]=hi;}else{low[i]=std::min(low[i],lo);high[i]=std::max(high[i],hi);}
                }
                any=true;
            }
            final_pivot=CFrameValue{};
            if(any)for(int i=0;i<3;++i)final_pivot.p[i]=(low[i]+high[i])/2;
        }
        auto final_pivot_json=pivot_encode(final_pivot);pivot_decode(final_pivot_json);
        cache["rows"]=std::move(rows);cache["scale"]=target;cache["pivot"]=std::move(final_pivot_json);
        std::unordered_map<std::string,Json> replacements{{root.id,cache}};
        for(auto* node:nodes)if(node!=&root&&pivot_model(w,*node))replacements[node->id]=Json{{"rows",Json::object()}};
        check_model_scale_cache(w,replacements);check_mutation_budget(w,entries);
        std::unordered_map<std::string,Json> applied;
        for(auto* node:nodes)if(node->class_name=="Motor6D") {
            if(auto previous=w.motor_applied_transforms.find(node->id);previous!=w.motor_applied_transforms.end()&&!previous->second.is_null())
                applied[node->id]=model_scaled_value(previous->second,ModelScaleKind::LocalFrame,target/current,pivot);
        }
        // The detached physics model needs one ordered velocity update for its
        // owned character, after all size/mass changes. Staging each part's
        // value above avoids repeatedly scaling one assembly via its members.
        for(const auto& write:writes)if(pivot_part(w,*write.node)&&write.after.contains("AssemblyLinearVelocity")) {
            auto motion_root=character_motion_root_id(w,*write.node);
            if(motion_root.empty()||motion_root!=write.node->id)continue;
            auto velocity=part_vector(write.after["AssemblyLinearVelocity"]);
            lua_pushvector(L,velocity[0],velocity[1],velocity[2]);
            try {
                movement_bindings::enqueue_linear_velocity(L,write.node->id,-1,motion_root);
                movement_bindings::tag_latest_mutation_cursor(L,static_cast<int>(w.mutations.size()+entries.size()));
            } catch(...) {lua_pop(L,1);throw;}
            lua_pop(L,1);break;
        }
        for(const auto& write:writes)for(auto it=write.after.begin();it!=write.after.end();++it)
            w.local_properties[write.node->id][it.key()]=it.value();
        append_mutations(w,std::move(entries));
        for(auto& [id,value]:applied)w.motor_applied_transforms[id]=std::move(value);
        for(auto* node:nodes)if(pivot_model(w,*node))w.pivot_caches.erase(node->id);
        for(auto& [id,value]:replacements)w.model_scale_caches[id]=std::move(value);
        for(const auto& write:writes)for(auto it=write.after.begin();it!=write.after.end();++it)property_events(w,*write.node,it.key());
        return 0;
    }catch(const std::exception& e){return luaL_error(L,"ScaleTo: %s",e.what()),0;}
}
