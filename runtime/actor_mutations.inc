namespace {

struct ActorMutationStack {
    lua_State* L;
    int top;
    explicit ActorMutationStack(lua_State* state):L(state),top(lua_gettop(state)){}
    ~ActorMutationStack(){lua_settop(L,top);}
};

std::string actor_id_token(const std::string& actor_id) {
    static const char* hex="0123456789abcdef";
    std::string out;out.reserve(actor_id.size()*2);
    for(unsigned char byte:actor_id){out.push_back(hex[byte>>4]);out.push_back(hex[byte&15]);}
    return out;
}

std::string actor_local_prefix(const std::string& actor_id) {
    return "local:actor:"+actor_id_token(actor_id)+":";
}

std::pair<std::string,uint64_t> split_local_id(const std::string& id) {
    if(id.rfind("local:",0)!=0)throw std::runtime_error("Actor create requires a local instance ID");
    const size_t split=id.find_last_of(':');
    if(split==std::string::npos||split+1==id.size())throw std::runtime_error("invalid local instance ID");
    const std::string suffix=id.substr(split+1);
    if(suffix.size()>20||suffix[0]<'0'||suffix[0]>'9')throw std::runtime_error("invalid local instance ID suffix");
    uint64_t value=0;
    for(char c:suffix){
        if(c<'0'||c>'9'||value>(std::numeric_limits<uint64_t>::max()-uint64_t(c-'0'))/10)
            throw std::runtime_error("invalid local instance ID suffix");
        value=value*10+uint64_t(c-'0');
    }
    if(value==0)throw std::runtime_error("invalid local instance ID suffix");
    return {id.substr(0,split+1),value};
}

void actor_mutation_lua_call(lua_State* L,int nargs,const char* operation) {
    if(lua_pcall(L,nargs,0,0)==LUA_OK)return;
    size_t length=0;const char* raw=lua_tolstring(L,-1,&length);
    std::string detail=raw?std::string(raw,length):"unknown Luau error";
    lua_pop(L,1);
    throw std::runtime_error(std::string(operation)+" failed: "+detail);
}

void validate_actor_value_refs(const World& world,const Json& value,size_t depth=0) {
    if(depth>32)throw std::runtime_error("Actor mutation value nesting exceeds limit");
    if(value.is_object()) {
        if(value.size()==1&&value.contains("$ref")) {
            if(!value["$ref"].is_string()||world.nodes.find(value["$ref"].get<std::string>())==world.nodes.end())
                throw std::runtime_error("Actor mutation references an instance outside this world");
            return;
        }
        if(value.size()==1&&value.contains("$instance_handle")) {
            if(!value["$instance_handle"].is_null()) {
                if(!value["$instance_handle"].is_string()
                   ||world.nodes.find(value["$instance_handle"].get<std::string>())==world.nodes.end())
                    throw std::runtime_error("Actor mutation InstanceHandle references an instance outside this world");
            }
            return;
        }
        for(auto it=value.begin();it!=value.end();++it)validate_actor_value_refs(world,it.value(),depth+1);
    } else if(value.is_array()) {
        for(const auto& item:value)validate_actor_value_refs(world,item,depth+1);
    }
}

void actor_mutation_set(lua_State* L,World& world,const std::string& id,
                        const std::string& property,const Json& value) {
    Node* node=find_node(world,id);
    // Destroy retains the Instance handle and locks Parent, while ordinary
    // writable properties remain usable through that retained reference.
    // Let the normal Instance setter enforce the remaining property rules.
    if(!node)throw std::runtime_error("set target is not present in the local world");
    validate_actor_value_refs(world,value);
    lua_pushcfunction(L,instance_newindex,"Instance.__newindex");
    push_node(L,world,node);
    lua_pushlstring(L,property.data(),property.size());
    push_json(L,world,value);
    actor_mutation_lua_call(L,3,"Actor property set");
}

// Root-owned transform writes already arrive in the canonical four-field
// order emitted by part_transform_write(). Replaying those four setters one
// by one invokes joint_part_write() for each field and rebuilds the same
// assembly graph repeatedly. A complete canonical run can be installed as
// one pose while retaining the setters' final values and event order.
bool actor_mutation_replay_complete_pose(World& world,const Json& actions,size_t first) {
    constexpr size_t kPoseFieldCount=4;
    static const char* fields[]={"CFrame","Orientation","Position","Rotation"};
    if(first+kPoseFieldCount>actions.size())return false;
    const Json& initial=actions[first];
    if(initial.value("kind",std::string())!="set"
       ||!initial.contains("id")||!initial["id"].is_string())return false;
    const std::string id=initial["id"].get<std::string>();
    Node* node=find_node(world,id);
    if(!node||node->destroyed||!pivot_part(world,*node))return false;

    Json supplied=Json::object();
    for(size_t offset=0;offset<kPoseFieldCount;++offset) {
        const Json& action=actions[first+offset];
        if(action.value("kind",std::string())!="set"
           ||!action.contains("id")||!action["id"].is_string()||action["id"].get<std::string>()!=id
           ||!action.contains("property")||!action["property"].is_string()
           ||action["property"].get<std::string>()!=fields[offset]
           ||!action.contains("value"))return false;
        const Json* descriptor=property_descriptor(*node,fields[offset]);
        if(!descriptor||!descriptor->value("writable",false))return false;
        bool scriptable=!reflected_hidden_property(world,*node,fields[offset]);
        auto overrides=world.scriptability.find(id);
        if(overrides!=world.scriptability.end()) {
            auto override=overrides->second.find(fields[offset]);
            if(override!=overrides->second.end())scriptable=override->second;
        }
        if(!scriptable)return false;
        supplied[fields[offset]]=action["value"];
    }

    // Only optimize values that are exactly what the ordinary CFrame setter
    // would derive. Anything malformed or inconsistent takes the established
    // per-property validation and error path below.
    Json after;
    try {
        for(const char* field:fields)validate_actor_value_refs(world,supplied[field]);
        after=part_fields_from_frame(supplied["CFrame"]);
    } catch(const std::exception&) { return false; }
    for(const char* field:fields)if(!after.contains(field)||after[field]!=supplied[field])return false;

    Json before;
    try { before=previous_part_transform(world,*node); }
    catch(const std::exception&) { return false; }
    for(const char* field:fields)world.local_properties[id][field]=after[field];
    part_transform_events(world,*node,before,after);
    return true;
}

void actor_mutation_attribute(lua_State* L,World& world,const std::string& id,
                              const std::string& name,const Json& value) {
    Node* node=find_node(world,id);
    if(!node||node->destroyed)throw std::runtime_error("attribute target is not present in the local world");
    validate_actor_value_refs(world,value);
    lua_pushcfunction(L,set_attribute,"Instance.SetAttribute");
    push_node(L,world,node);
    lua_pushlstring(L,name.data(),name.size());
    push_json(L,world,value);
    actor_mutation_lua_call(L,3,"Actor attribute set");
}

void actor_mutation_set_tags(World& world,const std::string& id,const Json& tags,
                             bool journal_root_change) {
    Node* node=find_node(world,id);
    if(!node||node->destroyed)
        throw std::runtime_error("tag target is not present in the local world");
    // set_tags is an authoritative full-set record. Reuse the CollectionService
    // decoder so root and Actor mutations share sorting and validation rules.
    Node decoded;
    collection_read_state(decoded,
        Json{{"collection_tags",tags},{"collection_tags_known",true}});
    const bool had_known_state=node->collection_tags_known;
    const auto previous=node->collection_tags;
    const auto& replacement=decoded.collection_tags;
    if(had_known_state&&previous==replacement)return;

    std::vector<Json> entries;
    if(journal_root_change) {
        entries.emplace_back(Json{{"kind","set_tags"},{"id",id},{"tags",replacement}});
        check_mutation_budget(world,entries);
    }

    node->collection_tags=replacement;
    node->collection_tags_known=true;
    if(journal_root_change)append_mutations(world,std::move(entries));

    if(!collection_in_datamodel(world,*node))return;
    for(const auto& tag:previous)
        if(!std::binary_search(replacement.begin(),replacement.end(),tag))
            collection_emit(world,*node,tag,false);
    for(const auto& tag:replacement)
        if(!std::binary_search(previous.begin(),previous.end(),tag))
            collection_emit(world,*node,tag,true);
}

void actor_mutation_player_scripts(lua_State* L,World& world,const Node& expected) {
    auto [prefix,number]=split_local_id(expected.id);
    if(prefix!="local:"||expected.class_name!="PlayerScripts"||expected.name!="PlayerScripts"
       ||expected.parent!=world.local_player||expected.server_owned||expected.destroyed
       ||!expected.properties.empty()||expected.attributes.size()!=0||!expected.attributes.known()
       ||!expected.collection_tags.empty()||!expected.collection_tags_known
       ||expected.origins!=Json{{"Name","local"},{"ClassName","local"},{"Parent","local"}})
        throw std::runtime_error("unsupported local PlayerScripts container record");
    if(Node* existing=find_node(world,expected.id)) {
        if(existing->class_name!="PlayerScripts"||existing->name!="PlayerScripts"
           ||existing->parent!=world.local_player||existing->server_owned)
            throw std::runtime_error("local PlayerScripts ID conflicts with existing node");
        push_node(L,world,existing);lua_pop(L,1);return;
    }
    const std::string old_prefix=world.local_id_prefix;
    const uint64_t old_next=world.next_local_id;
    world.local_id_prefix=prefix;world.next_local_id=number;
    const size_t mutation_count=world.mutations.size();
    const size_t mutation_bytes=world.mutation_bytes;
    const size_t order_count=world.order.size();
    try {
        push_player_scripts(L,world);
        Node* created=find_node(world,expected.id);
        if(!created||created->class_name!="PlayerScripts"||created->parent!=world.local_player
           ||created->name!=expected.name||created->properties!=expected.properties
           ||created->origins!=expected.origins)
            throw std::runtime_error("local PlayerScripts identity conflicts with existing container");
        lua_pop(L,1);
    } catch(...) {
        for(size_t i=order_count;i<world.order.size();++i) {
            const std::string id=world.order[i];
            world.nodes.erase(id);world.local_properties.erase(id);
        }
        world.order.erase(world.order.begin()+order_count,world.order.end());
        world.mutations.erase(world.mutations.begin()+mutation_count,world.mutations.end());
        world.mutation_bytes=mutation_bytes;
        world.local_id_prefix=old_prefix;world.next_local_id=old_next;
        throw;
    }
    world.local_id_prefix=old_prefix;world.next_local_id=old_next;
}

void actor_mutation_active_camera(World& world,const Node& expected,
                                  std::unordered_set<std::string>& replayed_camera_destroys) {
    if(expected.class_name!="Camera"||expected.name!="Camera"||expected.parent.empty()
       ||expected.server_owned||expected.destroyed||expected.attributes.size()!=0
       ||!expected.attributes.known()||!expected.collection_tags.empty()
       ||!expected.collection_tags_known||expected.constructor_defaults_known
       ||!expected.revisions.empty())
        throw std::runtime_error("unsupported active Camera creation record");
    auto [prefix,number]=split_local_id(expected.id);
    if(prefix!="local:")throw std::runtime_error("active Camera must retain its root local identity");
    Node* workspace=find_node(world,expected.parent);
    if(!workspace||workspace->class_name!="Workspace"||workspace->destroyed)
        throw std::runtime_error("active Camera parent is not this world's Workspace");

    Json properties=Json::object();
    Json origins=Json::object();
    const Json* camera=constructor_class("Camera");
    if(!camera)throw std::runtime_error("Camera constructor metadata unavailable");
    const auto& defaults=camera->at("properties");
    for(auto it=defaults.begin();it!=defaults.end();++it) {
        properties[it.key()]=it.value().at("value");
        origins[it.key()]="class_default";
    }
    // set_current_camera() adds CameraType=Custom after applying constructor
    // defaults, so this may be absent from the pinned constructor registry.
    properties["CameraType"]=5;
    origins["CameraType"]="local";origins["Name"]="local";origins["Parent"]="local";
    if(expected.properties!=properties||expected.origins!=origins)
        throw std::runtime_error("active Camera create differs from the local constructor record");
    if(Node* existing=find_node(world,expected.id)) {
        if(existing->class_name==expected.class_name&&existing->name==expected.name
           &&existing->parent==expected.parent&&existing->properties==expected.properties
           &&existing->origins==expected.origins&&existing->attributes.snapshot()==expected.attributes.snapshot()
           &&existing->collection_tags==expected.collection_tags&&!existing->server_owned
           &&!existing->destroyed)
            return;
        throw std::runtime_error("active Camera ID conflicts with an existing node");
    }

    // The root's active Camera is born parented by set_current_camera(), not
    // Instance.new(). Reuse that normal engine path with the received local ID
    // so CurrentCamera, viewport state, parent events, and camera defaults stay
    // consistent across the root and Actor views.
    std::unordered_set<std::string> replaced_camera_subtree;
    const auto children=children_index(world);
    for(const auto& id:world.order) {
        const Node& node=world.nodes.at(id);
        if(id==expected.id||node.destroyed||node.class_name!="Camera"
           ||parent_id(world,node)!=workspace->id)continue;
        std::deque<std::string> pending{id};
        while(!pending.empty()) {
            std::string current=std::move(pending.front());pending.pop_front();
            if(!replaced_camera_subtree.insert(current).second)continue;
            auto found=children.find(current);
            if(found!=children.end())for(const auto& child:found->second)pending.push_back(child);
        }
    }
    const std::string old_prefix=world.local_id_prefix;
    const uint64_t old_next=world.next_local_id;
    world.local_id_prefix=prefix;world.next_local_id=number;
    try {
        set_current_camera(world,*workspace,nullptr);
        Node* created=find_node(world,expected.id);
        if(!created||created->class_name!=expected.class_name||created->name!=expected.name
           ||created->parent!=expected.parent||created->properties!=expected.properties
           ||created->origins!=expected.origins||created->attributes.snapshot()!=expected.attributes.snapshot()
           ||created->collection_tags!=expected.collection_tags||created->server_owned
           ||created->destroyed)
            throw std::runtime_error("active Camera did not match its received local identity");
        for(const auto& id:replaced_camera_subtree) {
            const Node* retired=find_node(world,id);
            if(retired&&retired->destroyed&&world.locally_destroyed.find(id)!=world.locally_destroyed.end())
                replayed_camera_destroys.insert(id);
        }
    } catch(...) {
        world.local_id_prefix=old_prefix;world.next_local_id=old_next;
        throw;
    }
    world.local_id_prefix=old_prefix;world.next_local_id=old_next;
}

bool actor_mutation_same_node(const Node& actual,const Node& expected) {
    return actual.id==expected.id&&actual.class_name==expected.class_name
        &&actual.name==expected.name&&actual.parent==expected.parent
        &&actual.properties==expected.properties&&actual.origins==expected.origins
        &&actual.attributes.snapshot()==expected.attributes.snapshot()&&actual.attributes.known()==expected.attributes.known()
        &&actual.destroyed==expected.destroyed&&actual.server_owned==expected.server_owned
        &&actual.constructor_defaults_known==expected.constructor_defaults_known
        &&actual.collection_tags==expected.collection_tags
        &&actual.collection_tags_known==expected.collection_tags_known
        &&actual.revisions==expected.revisions;
}

void actor_mutation_clone_graph(World& world,const Json& action,
                                const std::string* required_prefix) {
    const std::string kind=action.value("kind",std::string());
    const bool is_template=kind=="template_clone";
    if((kind!="clone_graph"&&!is_template)
       ||action.size()!=5u+(is_template?2u:0u)+(action.contains("actor_origin")?1u:0u)
       ||!action.contains("source_root")||!action["source_root"].is_string()
       ||!action.contains("source_ids")||!action["source_ids"].is_array()
       ||!action.contains("clone_ids")||!action["clone_ids"].is_array()
       ||!action.contains("node_hashes")||!action["node_hashes"].is_array())
        throw std::runtime_error("clone graph record is malformed");
    const std::string source_root=action["source_root"].get<std::string>();
    const auto& source_ids=action["source_ids"];
    const auto& clone_ids=action["clone_ids"];
    const auto& node_hashes=action["node_hashes"];
    if(source_root.empty()||source_ids.empty()||source_ids.size()!=clone_ids.size()
       ||source_ids.size()!=node_hashes.size()||source_ids.size()>kMaxNodes+1000)
        throw std::runtime_error("clone graph record has an invalid node count");
    if(is_template&&(!action.contains("id")||!action["id"].is_string()
       ||!clone_ids[0].is_string()
       ||action["id"].get<std::string>()!=clone_ids[0].get<std::string>()
       ||!action.contains("nodes")||!action["nodes"].is_number_unsigned()
       ||action["nodes"].get<size_t>()!=source_ids.size()))
        throw std::runtime_error("template clone summary differs from its compact graph");

    std::string namespace_prefix;
    std::unordered_set<std::string> destinations;
    std::unordered_set<std::string> sources;
    size_t preexisting=0;
    for(size_t i=0;i<source_ids.size();++i) {
        if(!source_ids[i].is_string()||!clone_ids[i].is_string()||!node_hashes[i].is_string())
            throw std::runtime_error("clone graph identity arrays are malformed");
        if(source_ids[i].get<std::string>().empty()
           ||!sources.insert(source_ids[i].get<std::string>()).second)
            throw std::runtime_error("clone graph contains duplicate source IDs");
        const std::string hash=node_hashes[i].get<std::string>();
        if(hash.size()!=64||!std::all_of(hash.begin(),hash.end(),[](char c){return (c>='0'&&c<='9')||(c>='a'&&c<='f');}))
            throw std::runtime_error("clone graph node digest is malformed");
        const auto [prefix,number]=split_local_id(clone_ids[i].get<std::string>());
        (void)number;
        if(i==0)namespace_prefix=prefix;
        if(prefix!=namespace_prefix||(required_prefix&&prefix!=*required_prefix)
           ||(!required_prefix&&prefix!="local:"
              &&(!action.contains("actor_origin")||!action["actor_origin"].is_string()
                 ||prefix!=actor_local_prefix(action["actor_origin"].get<std::string>())))
           ||!destinations.insert(clone_ids[i].get<std::string>()).second)
            throw std::runtime_error("clone graph node has an invalid or duplicate local identity");
        if(Node* existing=find_node(world,clone_ids[i].get<std::string>())) {
            auto birth=world.local_clone_birth_hashes.find(existing->id);
            if(birth==world.local_clone_birth_hashes.end()||birth->second!=hash)
                throw std::runtime_error("clone graph destination ID conflicts with another node");
            ++preexisting;
        }
    }
    if(preexisting!=0) {
        if(preexisting!=source_ids.size())
            throw std::runtime_error("clone graph replay found only part of its destination tree");
        return;
    }
    if(world.nodes.size()>kMaxNodes+1000||source_ids.size()>kMaxNodes+1000-world.nodes.size())
        throw std::runtime_error("clone graph exceeds the local instance limit");

    Node* root=find_node(world,source_root);
    if(!root||root->destroyed||!archivable(world,*root))
        throw std::runtime_error("clone graph source root is missing or not Archivable");
    const auto children=children_index(world);
    std::deque<std::string> pending{source_root};
    std::vector<std::string> selected;
    while(!pending.empty()) {
        std::string id=std::move(pending.front());pending.pop_front();
        auto found=world.nodes.find(id);
        if(found==world.nodes.end()||found->second.destroyed)
            throw std::runtime_error("clone graph source descendant is missing");
        const Node& node=found->second;
        if(!archivable(world,node))continue;
        selected.push_back(id);
        auto found_children=children.find(id);
        if(found_children!=children.end())
            for(const auto& child:found_children->second)pending.push_back(child);
    }
    if(selected.size()!=source_ids.size())
        throw std::runtime_error("clone graph source tree size differs from its producer record");

    std::unordered_map<std::string,std::string> clones_by_source;
    for(size_t i=0;i<selected.size();++i) {
        if(source_ids[i].get<std::string>()!=selected[i])
            throw std::runtime_error("clone graph source order differs from its producer record");
        clones_by_source.emplace(selected[i],clone_ids[i].get<std::string>());
    }

    std::vector<Node> expected_nodes;
    expected_nodes.reserve(selected.size());
    std::vector<std::string> birth_hashes;
    birth_hashes.reserve(selected.size());
    for(size_t i=0;i<selected.size();++i) {
        const Node& source=world.nodes.at(selected[i]);
        Node expected=clone_node_copy(world,source,clones_by_source.at(selected[i]),source_root,clones_by_source);
        const std::string birth_hash=source_crypto::hex(source_crypto::sha256(node_record(expected).dump()));
        if(birth_hash!=node_hashes[i].get<std::string>())
            throw std::runtime_error("clone graph node digest differs from a faithful source clone");
        birth_hashes.push_back(birth_hash);
        expected_nodes.push_back(std::move(expected));
    }
    // Keep the graph entry compact for Actor replay while forwarding the usual
    // per-node create rows to root-side Python consumers.
    std::vector<Json> journal{action};
    if(!is_template)
        for(const auto& node:expected_nodes)
            journal.push_back(Json{{"kind","create"},{"node",node_record(node)}});
    check_mutation_budget(world,journal);
    clone_model_scale_caches(world,clones_by_source);
    std::vector<std::pair<uint64_t,std::string>> gui_copies;
    for(const auto& source:selected) {
        const Node& source_node=world.nodes.at(source);
        const std::string& clone_id=clones_by_source.at(source);
        if(source!=source_root&&class_is_a(world,source_node.class_name,"GuiObject")) {
            auto order=world.gui_parent_order.find(source);
            gui_copies.emplace_back(order==world.gui_parent_order.end()?0:order->second,clone_id);
        }
    }
    std::stable_sort(gui_copies.begin(),gui_copies.end(),[](const auto& a,const auto& b){return a.first<b.first;});
    for(const auto& entry:gui_copies)world.gui_parent_order[entry.second]=world.next_gui_parent_order++;
    const std::string last_id=expected_nodes.back().id;
    for(auto& node:expected_nodes) {
        if(node.class_name=="ScrollingFrame")world.active_scroll_layouts.insert(node.id);
        auto inserted=world.nodes.emplace(node.id,std::move(node));
        if(!inserted.second)throw std::runtime_error("clone graph destination ID appeared during replay");
        world.order.push_back(inserted.first->first);
    }
    if(namespace_prefix==world.local_id_prefix) {
        const auto [prefix,last]=split_local_id(last_id);
        if(prefix==world.local_id_prefix&&last<std::numeric_limits<uint64_t>::max())
            world.next_local_id=std::max(world.next_local_id,last+1);
    }
    for(size_t i=0;i<expected_nodes.size();++i)
        world.local_clone_birth_hashes.emplace(clone_ids[i].get<std::string>(),birth_hashes[i]);
    append_mutations(world,std::move(journal));
}

bool actor_mutation_engine_local_node(const Node& expected,const Json& action,
                                      const std::string* required_prefix) {
    if(expected.server_owned||expected.destroyed||expected.attributes.size()!=0
       ||!expected.attributes.known()||!expected.collection_tags.empty()
       ||expected.collection_tags_known||expected.constructor_defaults_known
       ||!expected.revisions.empty())return false;
    const auto [prefix,number]=split_local_id(expected.id);
    (void)number;
    if(required_prefix)return prefix==*required_prefix;
    if(prefix=="local:")return true;
    const Json origin=action.value("actor_origin",Json(nullptr));
    return origin.is_string()&&!origin.get_ref<const std::string&>().empty()
        &&prefix==actor_local_prefix(origin.get_ref<const std::string&>());
}

#include "actor_tween_mutations.inc"

void actor_mutation_animation_node(World& world,const Node& expected,const Json& action,
                                   const std::string* required_prefix) {
    if(expected.class_name!="Animator"&&expected.class_name!="AnimationTrack")
        throw std::runtime_error("unsupported animation engine node");
    if(!actor_mutation_engine_local_node(expected,action,required_prefix))
        throw std::runtime_error("animation engine node has invalid local ownership or metadata");
    const Json properties{{"Archivable",true}};
    const Json origins{{"Archivable","class_default"},{"Name","local"},{"Parent","local"}};
    if(expected.properties!=properties||expected.origins!=origins)
        throw std::runtime_error("animation engine node differs from its local producer record");
    if(expected.class_name=="Animator") {
        if(expected.name!="Animator"||expected.parent.empty())
            throw std::runtime_error("engine-created Animator must be parented to its source controller");
        Node* controller=find_node(world,expected.parent);
        if(!controller||controller->destroyed
           ||(controller->class_name!="Humanoid"&&controller->class_name!="AnimationController"))
            throw std::runtime_error("engine-created Animator parent is not a supported controller");
        if(Node* existing=find_node(world,expected.id)) {
            if(actor_mutation_same_node(*existing,expected))return;
            throw std::runtime_error("Animator ID conflicts with a different world node");
        }
        const auto [prefix,number]=split_local_id(expected.id);
        const std::string old_prefix=world.local_id_prefix;
        const uint64_t old_next=world.next_local_id;
        world.local_id_prefix=prefix;world.next_local_id=number;
        try {
            Node* created=animation_node(world,"Animator","Animator",expected.parent);
            if(!created||!actor_mutation_same_node(*created,expected))
                throw std::runtime_error("Animator create differs from its local engine record");
        } catch(...) {
            world.local_id_prefix=old_prefix;world.next_local_id=old_next;throw;
        }
        world.local_id_prefix=old_prefix;world.next_local_id=old_next;
        return;
    }

    if(!expected.parent.empty()||!action.contains("animation_track")
       ||!action["animation_track"].is_object())
        throw std::runtime_error("AnimationTrack create is missing engine references");
    const Json& metadata=action["animation_track"];
    if(metadata.size()!=6||metadata.value("version",0)!=1
       ||!metadata.contains("animator_id")||!metadata["animator_id"].is_string()
       ||!metadata.contains("animation_id")||!metadata["animation_id"].is_string()
       ||!metadata.contains("reference")||!metadata["reference"].is_string()
       ||!metadata.contains("looped")||!metadata["looped"].is_boolean()
       ||!metadata.contains("priority")||!metadata["priority"].is_number_integer())
        throw std::runtime_error("AnimationTrack engine references are malformed");
    const std::string animator_id=metadata["animator_id"].get<std::string>();
    const std::string animation_id=metadata["animation_id"].get<std::string>();
    const std::string reference=metadata["reference"].get<std::string>();
    const int priority=metadata["priority"].get<int>();
    if(animator_id.empty()||animator_id.size()>256||animation_id.empty()||animation_id.size()>256
       ||reference.empty()||reference.size()>512||priority_index(priority)<0)
        throw std::runtime_error("AnimationTrack engine references are outside supported ranges");
    Node* animator=find_node(world,animator_id);
    Node* animation=find_node(world,animation_id);
    auto workspace_entry=world.services.find("Workspace");
    Node* workspace=workspace_entry==world.services.end()?nullptr:find_node(world,workspace_entry->second);
    if(!animator||animator->destroyed||animator->class_name!="Animator"
       ||!animation||animation->destroyed||animation->class_name!="Animation"
       ||!workspace||workspace->destroyed||!is_descendant_of(world,*animator,workspace->id))
        throw std::runtime_error("AnimationTrack references a missing Animator, Animation, or Workspace");
    const Json* content=property_value(world,*animation,"AnimationId");
    if(!content||!content->is_string()||content->get_ref<const std::string&>().empty())
        content=property_value(world,*animation,"AnimationContent");
    if(!content||!content->is_string()||content->get_ref<const std::string&>()!=reference
       ||expected.name!=instance_name(world,*animation))
        throw std::runtime_error("AnimationTrack does not match its referenced Animation");

    AnimationTrackState track;
    track.animator=animator_id;track.animation=animation_id;track.reference=reference;
    track.looped=metadata["looped"].get<bool>();track.priority=priority;
    const bool actor_originated=required_prefix!=nullptr;
    auto register_reference=[&]() {
        if(world.animation.requested.count(reference))return;
        if(world.animation.requested.size()>=128
           ||(actor_originated&&world.animation.requests.size()>=128))
            throw std::runtime_error("animation asset request limit exceeded while replaying Actor track");
        world.animation.requested.insert(reference);
        // Actor VM request queues are not drained by the outer host. Forward
        // the first request for this content reference through the root queue;
        // sibling Actors and root-origin replicas then share the same result.
        if(actor_originated)world.animation.requests.push_back(Json{{"reference",reference}});
    };
    if(Node* existing=find_node(world,expected.id)) {
        auto state=world.animation.tracks.find(expected.id);
        if(actor_mutation_same_node(*existing,expected)&&state!=world.animation.tracks.end()
           &&state->second.animator==track.animator&&state->second.animation==track.animation
           &&state->second.reference==track.reference) {
            register_reference();return;
        }
        throw std::runtime_error("AnimationTrack ID conflicts with a different node or engine state");
    }
    if(!world.animation.requested.count(reference)
       &&(world.animation.requested.size()>=128
          ||(actor_originated&&world.animation.requests.size()>=128)))
        throw std::runtime_error("animation asset request limit exceeded while replaying Actor track");
    if(world.animation.tracks.size()>=AnimationState::track_limit)
        throw std::runtime_error("AnimationTrack limit exceeded while replaying engine creation");
    const auto [prefix,number]=split_local_id(expected.id);
    const std::string old_prefix=world.local_id_prefix;
    const uint64_t old_next=world.next_local_id;
    world.local_id_prefix=prefix;world.next_local_id=number;
    try {
        Node* created=animation_node(world,"AnimationTrack",expected.name,"",
                                     animation_track_replay_record(track));
        if(!created||!actor_mutation_same_node(*created,expected))
            throw std::runtime_error("AnimationTrack create differs from its local engine record");
        if(!world.animation.tracks.emplace(expected.id,std::move(track)).second)
            throw std::runtime_error("AnimationTrack engine state ID already exists");
        register_reference();
    } catch(...) {
        world.local_id_prefix=old_prefix;world.next_local_id=old_next;throw;
    }
    world.local_id_prefix=old_prefix;world.next_local_id=old_next;
}

void actor_mutation_client_object(lua_State* L,World& world,const Node& expected,
                                  const Json& entry,const std::string* required_prefix) {
    const auto [prefix,number]=split_local_id(expected.id);
    const std::string origin=entry.value("actor_origin",std::string());
    if((required_prefix&&prefix!=*required_prefix)
       ||(!required_prefix&&prefix!="local:"
          &&(origin.empty()||prefix!=actor_local_prefix(origin))))
        throw std::runtime_error("client object create has an invalid local namespace");
    if(expected.server_owned||expected.destroyed||expected.constructor_defaults_known
       ||expected.attributes.size()!=0||!expected.attributes.known()
       ||!expected.collection_tags.empty()||!expected.collection_tags_known
       ||!expected.revisions.empty())
        throw std::runtime_error("client object create differs from its engine metadata");

    const bool mouse=expected.class_name=="PlayerMouse";
    Json origins{{"Name","local"},{"ClassName","local"},{"Parent","local"}};
    Node* parent=nullptr;
    if(mouse) {
        origins["Icon"]="local";origins["TargetFilter"]="local";
        if(expected.name!="Mouse"||!expected.parent.empty()
           ||expected.properties!=Json{{"Icon",""},{"TargetFilter",nullptr}})
            throw std::runtime_error("PlayerMouse create differs from GetMouse's initial record");
        parent=find_node(world,world.local_player);
        if(!parent||parent->destroyed||parent->class_name!="Player")
            throw std::runtime_error("PlayerMouse requires this world's LocalPlayer");
        auto cached=world.client_services.player_mice.find(world.local_player);
        if(cached!=world.client_services.player_mice.end()&&cached->second!=expected.id) {
            auto* existing=find_node(world,cached->second);
            if(existing&&!existing->destroyed)
                throw std::runtime_error("PlayerMouse create conflicts with the existing singleton");
        }
    } else {
        if(expected.class_name!="StatsItem"||!expected.properties.empty())
            throw std::runtime_error("unsupported generated client object");
        parent=find_node(world,expected.parent);
        const auto service=world.services.find("Stats");
        Node* stats=service==world.services.end()?nullptr:find_node(world,service->second);
        auto under=[&](Node* node,const char* name,Node* ancestor) {
            return node&&!node->destroyed&&node->class_name=="StatsItem"
                &&instance_name(world,*node)==name&&ancestor
                &&parent_id(world,*node)==ancestor->id;
        };
        bool valid=false;
        if(stats&&!stats->destroyed&&parent&&!parent->destroyed) {
            if(expected.name=="Network")valid=parent==stats;
            else if(expected.name=="ServerStatsItem")valid=under(parent,"Network",stats);
            else if(expected.name=="Data Ping") {
                Node* network=find_node(world,parent_id(world,*parent));
                valid=under(parent,"ServerStatsItem",network)&&under(network,"Network",stats);
            }
        }
        if(!valid)throw std::runtime_error("StatsItem create is outside the generated Stats tree");
    }
    if(expected.origins!=origins)
        throw std::runtime_error("client object create has unsupported property origins");
    auto restore_cache=[&]() {
        if(mouse)world.client_services.player_mice[world.local_player]=expected.id;
        else if(expected.name=="Data Ping")world.stats_ping_item=expected.id;
    };
    if(Node* existing=find_node(world,expected.id)) {
        if(!actor_mutation_same_node(*existing,expected))
            throw std::runtime_error("client object ID conflicts with an existing node");
        restore_cache();return;
    }

    const std::string old_prefix=world.local_id_prefix;
    const uint64_t old_next=world.next_local_id;
    const size_t order_count=world.order.size(),mutation_count=world.mutations.size();
    const size_t mutation_bytes=world.mutation_bytes;
    const auto old_mice=world.client_services.player_mice;
    const std::string old_ping=world.stats_ping_item;
    world.local_id_prefix=prefix;world.next_local_id=number;
    try {
        Node* created=nullptr;
        if(mouse) {
            lua_pushcfunction(L,player_get_mouse,"GetMouse");push_node(L,world,parent);
            if(lua_pcall(L,1,1,0)!=LUA_OK) {
                size_t length=0;const char* error=lua_tolstring(L,-1,&length);
                throw std::runtime_error("PlayerMouse replay failed: "
                    +(error?std::string(error,length):"unknown Luau error"));
            }
            created=find_node(world,expected.id);lua_pop(L,1);
        } else created=stats_child(world,*parent,expected.name);
        if(!created||!actor_mutation_same_node(*created,expected))
            throw std::runtime_error("generated client object identity differs from producer record");
        restore_cache();
    } catch(...) {
        for(size_t i=order_count;i<world.order.size();++i) {
            world.nodes.erase(world.order[i]);world.local_properties.erase(world.order[i]);
        }
        world.order.resize(order_count);
        world.mutations.erase(world.mutations.begin()+mutation_count,world.mutations.end());
        world.mutation_bytes=mutation_bytes;
        world.client_services.player_mice=old_mice;world.stats_ping_item=old_ping;
        world.local_id_prefix=old_prefix;world.next_local_id=old_next;throw;
    }
    world.local_id_prefix=old_prefix;world.next_local_id=old_next;
}

void actor_mutation_create(lua_State* L,World& world,const Json& entry,
                           const std::string* required_prefix,
                           std::unordered_set<std::string>& replayed_camera_destroys) {
    if(!entry.is_object()||!entry.contains("node")||!entry["node"].is_object())
        throw std::runtime_error("create entry must contain a node record");
    Node expected=parse_node_record(entry["node"]);
    auto clone_birth=world.local_clone_birth_hashes.find(expected.id);
    if(clone_birth!=world.local_clone_birth_hashes.end()) {
        const std::string expected_birth=source_crypto::hex(source_crypto::sha256(node_record(expected).dump()));
        if(clone_birth->second==expected_birth) {
            if(!find_node(world,expected.id))throw std::runtime_error("cloned node birth record lost its instance");
            return;
        }
    }
    if(expected.class_name=="PlayerMouse"||expected.class_name=="StatsItem") {
        actor_mutation_client_object(L,world,expected,entry,required_prefix);return;
    }
    if(expected.class_name=="Tween") {
        actor_mutation_tween_create(world,expected,entry,required_prefix);return;
    }
    const bool engine_animator=expected.class_name=="Animator"&&!expected.parent.empty()
        &&!expected.collection_tags_known&&expected.properties==Json{{"Archivable",true}}
        &&expected.origins==Json{{"Archivable","class_default"},{"Name","local"},{"Parent","local"}};
    if(expected.class_name=="AnimationTrack"||engine_animator) {
        actor_mutation_animation_node(world,expected,entry,required_prefix);return;
    }
    if(!required_prefix&&expected.class_name=="PlayerScripts") {
        actor_mutation_player_scripts(L,world,expected);return;
    }
    if(!required_prefix&&expected.class_name=="Camera"&&!expected.parent.empty()
       &&expected.origins.is_object()&&expected.origins.value("CameraType",std::string())=="local"
       &&expected.origins.value("Parent",std::string())=="local") {
        actor_mutation_active_camera(world,expected,replayed_camera_destroys);return;
    }
    const bool service=local_service_supported(expected.class_name);
    if(expected.server_owned||expected.destroyed||(!service&&!expected.parent.empty())
       ||expected.attributes.size()!=0||!expected.attributes.known()
       ||!expected.collection_tags.empty()||!expected.collection_tags_known)
        throw std::runtime_error("unsupported local create record: id="+expected.id
            +", class="+expected.class_name+", parent="+expected.parent
            +", server_owned="+(expected.server_owned?"true":"false")
            +", destroyed="+(expected.destroyed?"true":"false")
            +", attributes="+std::to_string(expected.attributes.size())
            +", attributes_known="+(expected.attributes.known()?"true":"false")
            +", collection_tags="+std::to_string(expected.collection_tags.size())
            +", collection_tags_known="+(expected.collection_tags_known?"true":"false"));
    auto [prefix,number]=split_local_id(expected.id);
    if(required_prefix&&prefix!=*required_prefix)
        throw std::runtime_error("Actor create ID does not belong to its Actor namespace");
    if(Node* existing=find_node(world,expected.id)) {
        // Root journals can be retried when an earlier item in the same batch
        // failed validation. Consuming an identical constructor record is
        // idempotent; accepting a different node under the same ID would hide
        // a real namespace or ordering conflict.
        if(actor_mutation_same_node(*existing,expected))return;
        throw std::runtime_error("Actor create ID conflicts with existing node: id="
            +expected.id+", expected_class="+expected.class_name
            +", existing_class="+existing->class_name);
    }
    const std::string old_prefix=world.local_id_prefix;
    const uint64_t old_next=world.next_local_id;
    world.local_id_prefix=prefix;world.next_local_id=number;
    const size_t mutation_count=world.mutations.size();
    const size_t mutation_bytes=world.mutation_bytes;
    const size_t order_count=world.order.size();
    try {
        Node* created=nullptr;
        if(service) {
            created=ensure_client_service(world,expected.class_name);
        } else {
            lua_pushlightuserdata(L,&world);
            lua_pushcclosure(L,instance_new,"Instance.new",1);
            lua_pushlstring(L,expected.class_name.data(),expected.class_name.size());
            if(lua_pcall(L,1,1,0)!=LUA_OK) {
                size_t length=0;const char* raw=lua_tolstring(L,-1,&length);
                std::string detail=raw?std::string(raw,length):"unknown Luau error";
                lua_pop(L,1);
                throw std::runtime_error("Actor Instance.new failed: "+detail);
            }
            if(!has_meta(L,-1,kInstanceMeta))throw std::runtime_error("Instance.new returned a non-Instance");
            auto* handle=static_cast<Handle*>(lua_touserdata(L,-1));
            created=handle&&handle->world==&world?handle->node:nullptr;
        }
        if(!created||created->id!=expected.id||created->class_name!=expected.class_name
           ||created->name!=expected.name||created->properties!=expected.properties
           ||created->origins!=expected.origins||created->parent!=expected.parent)
            throw std::runtime_error("create entry contains unsupported non-constructor state");
    } catch(...) {
        // Instance.new can fail after allocating a node if its mutation budget
        // is exhausted. Roll back only this unparented new node and its journal.
        for(size_t i=order_count;i<world.order.size();++i) {
            const std::string id=world.order[i];
            world.nodes.erase(id);world.local_properties.erase(id);
        }
        world.order.resize(order_count);
        auto service_entry=world.services.find(expected.class_name);
        if(service_entry!=world.services.end()&&service_entry->second==expected.id)world.services.erase(service_entry);
        world.mutations.erase(world.mutations.begin()+mutation_count,world.mutations.end());world.mutation_bytes=mutation_bytes;
        world.local_id_prefix=old_prefix;world.next_local_id=old_next;
        throw;
    }
    if(!service)lua_pop(L,1);
    world.local_id_prefix=old_prefix;world.next_local_id=old_next;
}

void validate_actor_mutation_journal(const Json& actions) {
    if(!actions.is_array()||actions.size()>1024)
        throw std::runtime_error("Actor mutation journal must be a bounded array");
    if(actions.dump().size()>1024*1024)
        throw std::runtime_error("Actor mutation journal exceeds the byte limit");
    for(size_t i=0;i<actions.size();++i) {
        const Json& action=actions[i];
        if(!action.is_object()||!action.contains("kind")||!action["kind"].is_string())
            throw std::runtime_error("Actor mutation["+std::to_string(i)+"] is malformed");
        if(action.contains("_actor_feedback_broadcast"))
            throw std::runtime_error("Actor mutation contains a reserved engine feedback marker");
        const std::string kind=action["kind"].get<std::string>();
        if(kind=="set") {
            if(!action.contains("id")||!action["id"].is_string()
               ||!action.contains("property")||!action["property"].is_string()
               ||!action.contains("value"))
                throw std::runtime_error("Actor set mutation is malformed");
        } else if(kind=="attribute") {
            if(!action.contains("id")||!action["id"].is_string()
               ||!action.contains("name")||!action["name"].is_string()
               ||!action.contains("value"))
                throw std::runtime_error("Actor attribute mutation is malformed");
        } else if(kind=="set_tags") {
            if(!action.contains("id")||!action["id"].is_string()
               ||!action.contains("tags")||!action["tags"].is_array())
                throw std::runtime_error("Actor tag mutation is malformed");
            Node candidate;
            collection_read_state(candidate,
                Json{{"collection_tags",action.at("tags")},{"collection_tags_known",true}});
        } else if(kind=="create") {
            if(!action.contains("node")||!action["node"].is_object())
                throw std::runtime_error("Actor create mutation is malformed");
        } else if(kind=="tween_state") {
            if(!action.contains("id")||!action["id"].is_string()
               ||!action.contains("tween_playback")||!action["tween_playback"].is_object()
               ||(action.contains("command")&&!action["command"].is_string()))
                throw std::runtime_error("Actor Tween mutation is malformed");
        } else if(kind=="clone_graph"||kind=="template_clone") {
            if(!action.contains("source_root")||!action["source_root"].is_string()
               ||!action.contains("source_ids")||!action["source_ids"].is_array()
               ||!action.contains("clone_ids")||!action["clone_ids"].is_array()
               ||!action.contains("node_hashes")||!action["node_hashes"].is_array()
               ||(kind=="template_clone"&&(!action.contains("id")||!action["id"].is_string()
                   ||!action.contains("nodes")||!action["nodes"].is_number_unsigned())))
                throw std::runtime_error("Actor clone graph mutation is malformed");
        } else if(kind=="destroy") {
            if(!action.contains("ids")||!action["ids"].is_array()||action["ids"].empty())
                throw std::runtime_error("Actor destroy mutation is malformed");
            for(const auto& id:action["ids"])if(!id.is_string())
                throw std::runtime_error("Actor destroy mutation contains an invalid ID");
        } else {
            throw std::runtime_error("unsupported Actor mutation kind: "+kind);
        }
    }
}

void apply_actor_mutation_entries(lua_State* L,World& world,const Json& actions,
                                  const std::string* required_prefix) {
    ActorMutationStack stack(L);
    std::unordered_set<std::string> replayed_camera_destroys;
    for(size_t i=0;i<actions.size();++i) {
        const Json& action=actions[i];
        const std::string kind=action.at("kind").get<std::string>();
        try {
            if(kind=="create") {
                actor_mutation_create(L,world,action,required_prefix,replayed_camera_destroys);
            } else if(kind=="clone_graph"||kind=="template_clone") {
                actor_mutation_clone_graph(world,action,required_prefix);
            } else if(kind=="set") {
                if(!required_prefix&&actor_mutation_replay_complete_pose(world,actions,i)) {
                    i+=3;
                    continue;
                }
                actor_mutation_set(L,world,action.at("id").get<std::string>(),
                                   action.at("property").get<std::string>(),action.at("value"));
            } else if(kind=="attribute") {
                actor_mutation_attribute(L,world,action.at("id").get<std::string>(),
                                         action.at("name").get<std::string>(),action.at("value"));
            } else if(kind=="set_tags") {
                actor_mutation_set_tags(world,action.at("id").get<std::string>(),
                                        action.at("tags"),required_prefix!=nullptr);
            } else if(kind=="tween_state") {
                actor_mutation_tween_state(L,world,action,required_prefix);
            } else if(kind=="destroy") {
                std::vector<std::string> ids;
                for(const auto& id:action.at("ids")) {
                    std::string value=id.get<std::string>();
                    Node* node=find_node(world,value);
                    if(node&&node->destroyed&&replayed_camera_destroys.erase(value)>0)continue;
                    if(!node||node->destroyed) {
                        // This branch only consumes root-owned local mutation
                        // journals. A server delta can stream out/remove the
                        // target before the journal reaches this Actor VM;
                        // replaying Instance:Destroy on that now-absent
                        // target is idempotent. Actor-originated destroys
                        // still require a live node in the root world.
                        if(!required_prefix)continue;
                        throw std::runtime_error("destroy target is not present in the local world: id="
                            +value+(node?", class="+node->class_name+", destroyed=true":", class=unknown"));
                    }
                    ids.push_back(std::move(value));
                }
                destroy_roots(world,ids);refresh_current_camera(world);
            }
        } catch(const std::exception& error) {
            throw std::runtime_error("Actor mutation["+std::to_string(i)+"]: "+error.what());
        }
    }
}

void mark_actor_origin(World& world,size_t first,const std::string& actor_id) {
    std::vector<Json> tagged_entries;
    tagged_entries.reserve(world.mutations.size()-first);
    size_t extra=0;
    for(size_t i=first;i<world.mutations.size();++i) {
        Json tagged=world.mutations[i];
        if(tagged.value("_actor_feedback_broadcast",false)) {
            tagged.erase("_actor_feedback_broadcast");
            tagged_entries.push_back(std::move(tagged));
            continue;
        }
        tagged["actor_origin"]=actor_id;
        const size_t old_size=world.mutations[i].dump().size();
        const size_t new_size=tagged.dump().size();
        if(new_size<old_size||new_size-old_size>std::numeric_limits<size_t>::max()-extra)
            throw std::runtime_error("Actor mutation journal byte count overflow");
        extra+=new_size-old_size;tagged_entries.push_back(std::move(tagged));
    }
    if(extra>std::numeric_limits<size_t>::max()-world.mutation_bytes
       ||world.mutation_bytes+extra>kMaxMutationBytes)
        throw std::runtime_error("Actor mutation output limit exceeded");
    for(size_t i=0;i<tagged_entries.size();++i)world.mutations[first+i]=std::move(tagged_entries[i]);
    world.mutation_bytes+=extra;
}

} // namespace

Json apply_actor_mutations(lua_State* L,World& world,const std::string& actor_id,
                           const Json& actions) {
    if(!L||actor_id.empty())throw std::runtime_error("Actor mutation target is invalid");
    validate_actor_mutation_journal(actions);
    if(actions.empty())return Json{{"actor_id",actor_id},{"applied",0},{"root_mutations",0}};
    const std::string prefix=actor_local_prefix(actor_id);
    const size_t first=world.mutations.size();
    apply_actor_mutation_entries(L,world,actions,&prefix);
    const size_t appended=world.mutations.size()-first;
    mark_actor_origin(world,first,actor_id);
    return Json{{"actor_id",actor_id},{"applied",actions.size()},{"root_mutations",appended}};
}

Json apply_local_mutations(lua_State* L,World& world,const Json& actions) {
    if(!L)throw std::runtime_error("local mutation target is invalid");
    validate_actor_mutation_journal(actions);
    if(actions.empty())return Json{{"applied",0}};
    Json saved_mutations=world.mutations;
    const size_t saved_bytes=world.mutation_bytes;
    try {
        apply_actor_mutation_entries(L,world,actions,nullptr);
    } catch(...) {
        world.mutations=std::move(saved_mutations);world.mutation_bytes=saved_bytes;
        throw;
    }
    world.mutations=std::move(saved_mutations);world.mutation_bytes=saved_bytes;
    return Json{{"applied",actions.size()}};
}
