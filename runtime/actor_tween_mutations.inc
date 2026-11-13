// Replay engine-created Tween instances and their owner-published state.
// Included from actor_mutations.inc after actor_mutation_engine_local_node.

TweenPlaybackState actor_tween_state_from_record(const Json& value,World& world) {
    if(!value.is_object()||value.value("version",0)!=1
       ||!value.contains("target")||!value["target"].is_string()
       ||!value.contains("goals")||!value["goals"].is_object()
       ||!value.contains("starts")||!value["starts"].is_object()
       ||!value.contains("duration")||!value["duration"].is_number()
       ||!value.contains("delay")||!value["delay"].is_number()
       ||!value.contains("elapsed")||!value["elapsed"].is_number()
       ||!value.contains("style")||!value["style"].is_number_integer()
       ||!value.contains("direction")||!value["direction"].is_number_integer()
       ||!value.contains("repeats")||!value["repeats"].is_number_integer()
       ||!value.contains("reverses")||!value["reverses"].is_boolean()
       ||!value.contains("state")||!value["state"].is_number_integer())
        throw std::runtime_error("Tween playback metadata is malformed");
    TweenPlaybackState result;
    result.target=value["target"].get<std::string>();
    result.goals=value["goals"];result.starts=value["starts"];
    result.duration=value["duration"].get<double>();result.delay=value["delay"].get<double>();
    result.elapsed=value["elapsed"].get<double>();result.style=value["style"].get<int>();
    result.direction=value["direction"].get<int>();result.repeats=value["repeats"].get<int>();
    result.reverses=value["reverses"].get<bool>();result.state=value["state"].get<int>();
    if(result.target.empty()||result.target.size()>256||!result.goals.is_object()
       ||result.goals.size()>kTweenPropertyLimit||!result.starts.is_object()
       ||result.starts.size()>result.goals.size()||!std::isfinite(result.duration)
       ||result.duration<0||result.duration>86400||!std::isfinite(result.delay)
       ||result.delay<0||result.delay>86400||!std::isfinite(result.elapsed)
       ||result.elapsed<0||result.style<0||result.style>10||result.direction<0
       ||result.direction>2||result.repeats < -1||result.state<0||result.state>5)
        throw std::runtime_error("Tween playback metadata is outside supported limits");
    Node* target=find_node(world,result.target);
    if(!target)
        throw std::runtime_error("Tween playback target is absent from this world");
    for(auto it=result.goals.begin();it!=result.goals.end();++it) {
        if(it.key().empty()||it.key().size()>128)
            throw std::runtime_error("Tween playback contains an invalid goal name");
        const Json* current=property_value(world,*target,it.key());
        if(!current||!tween_shape(*current,it.value()))
            throw std::runtime_error("Tween playback goal differs from its target property");
        if(const Json* descriptor=property_descriptor(*target,it.key()))
            if(!descriptor->value("writable",false))
                throw std::runtime_error("Tween playback goal targets a read-only property");
    }
    for(auto it=result.starts.begin();it!=result.starts.end();++it) {
        auto goal=result.goals.find(it.key());
        if(goal==result.goals.end()||!tween_shape(it.value(),goal.value()))
            throw std::runtime_error("Tween playback start value has no matching goal");
    }
    result.updated=std::chrono::steady_clock::now();
    return result;
}

void actor_mutation_tween_create(World& world,const Node& expected,const Json& action,
                                 const std::string* required_prefix) {
    if(expected.class_name!="Tween"||expected.name!="Tween"||!expected.parent.empty()
       ||expected.properties!=Json{{"Archivable",true}}
       ||expected.origins!=Json{{"Archivable","class_default"},{"Name","local"},{"Parent","local"}}
       ||expected.attributes.size()!=0||!expected.attributes.known()
       ||!expected.collection_tags.empty()||expected.collection_tags_known
       ||expected.server_owned||expected.destroyed||expected.constructor_defaults_known
       ||!expected.revisions.empty()
       ||!actor_mutation_engine_local_node(expected,action,required_prefix)
       ||!action.contains("tween_playback"))
        throw std::runtime_error("Tween create differs from its local engine producer");
    TweenPlaybackState state=actor_tween_state_from_record(action.at("tween_playback"),world);
    if(Node* existing=find_node(world,expected.id)) {
        auto found=world.tweens.find(expected.id);
        if(!actor_mutation_same_node(*existing,expected)||found==world.tweens.end())
            throw std::runtime_error("Tween ID conflicts with an existing node or playback state");
        const auto& old=found->second;
        if(old.target!=state.target||old.goals!=state.goals||old.duration!=state.duration
           ||old.delay!=state.delay||old.style!=state.style||old.direction!=state.direction
           ||old.repeats!=state.repeats||old.reverses!=state.reverses)
            throw std::runtime_error("Tween ID conflicts with different engine playback state");
        return;
    }
    if(world.tweens.size()>=kTweenLimit)
        throw std::runtime_error("Tween instance limit exceeded during Actor replay");
    const auto [prefix,number]=split_local_id(expected.id);
    const std::string old_prefix=world.local_id_prefix;
    const uint64_t old_next=world.next_local_id;
    world.local_id_prefix=prefix;world.next_local_id=number;
    try {
        Node* created=tween_engine_node(world,state);
        if(!created||!actor_mutation_same_node(*created,expected))
            throw std::runtime_error("Tween create differs from its local engine record");
        auto found=world.tweens.find(expected.id);
        if(found==world.tweens.end())throw std::runtime_error("Tween playback state was not installed");
    } catch(...) {
        world.local_id_prefix=old_prefix;world.next_local_id=old_next;throw;
    }
    world.local_id_prefix=old_prefix;world.next_local_id=old_next;
}

void record_tween_state(World& world,Node& node,const TweenPlaybackState& state,
                        const char* command) {
    // Actor Worlds do not run Tween clocks. Only explicit API methods travel
    // back to the root; local mirror side effects are corrected by its reply.
    if(!world.actor_id.empty()&&!command)return;
    if(world.actor_id.empty()
       &&(!world.actor_runtime||world.actor_runtime->actor_count()==0))return;
    Json entry{{"kind","tween_state"},{"id",node.id},
               {"tween_playback",tween_playback_record(state)}};
    if(!world.actor_id.empty())entry["command"]=command;
    std::vector<Json> entries{std::move(entry)};
    check_mutation_budget(world,entries);
    append_mutations(world,std::move(entries));
}

void actor_mutation_tween_state(lua_State* L,World& world,const Json& action,
                                const std::string* required_prefix) {
    if(!action.contains("id")||!action["id"].is_string()
       ||!action.contains("tween_playback"))
        throw std::runtime_error("Tween state mutation is malformed");
    const std::string id=action["id"].get<std::string>();
    Node* node=find_node(world,id);
    auto found=world.tweens.find(id);
    if(!node||node->destroyed||node->class_name!="Tween"||found==world.tweens.end())
        throw std::runtime_error("Tween state target is not a live engine Tween");
    if(required_prefix) {
        if(!action.contains("command")||!action["command"].is_string())
            throw std::runtime_error("Actor Tween update must carry a Tween method command");
        const std::string method=action["command"].get<std::string>();
        if(method!="Play"&&method!="Pause"&&method!="Cancel")
            throw std::runtime_error("Actor Tween command is unsupported");
        const size_t first=world.mutations.size();
        lua_pushlstring(L,method.data(),method.size());
        lua_pushcclosure(L,tween_method,"Actor Tween method",1);
        push_node(L,world,node);
        actor_mutation_lua_call(L,1,"Actor Tween method");
        bool has_canonical_state=false;
        for(size_t i=first;i<world.mutations.size();++i)
            if(world.mutations[i].value("kind",std::string())=="tween_state"
               &&world.mutations[i].value("id",std::string())==id
               &&!world.mutations[i].contains("command"))has_canonical_state=true;
        if(!has_canonical_state) {
            auto current=world.tweens.find(id);
            if(current==world.tweens.end())throw std::runtime_error("Tween disappeared while applying its command");
            record_tween_state(world,*node,current->second);
        }
        // These writes and the resulting state are authoritative root engine
        // feedback. The source Actor did not advance its Tween, so it must
        // receive its own root updates alongside the other Actors.
        for(size_t i=first;i<world.mutations.size();++i)
            world.mutations[i]["_actor_feedback_broadcast"]=true;
        return;
    }
    if(action.contains("command"))
        throw std::runtime_error("root Tween feedback cannot contain an Actor command");
    TweenPlaybackState next=actor_tween_state_from_record(action.at("tween_playback"),world);
    TweenPlaybackState& current=found->second;
    if(next.target!=current.target||next.goals!=current.goals||next.duration!=current.duration
       ||next.delay!=current.delay||next.style!=current.style||next.direction!=current.direction
       ||next.repeats!=current.repeats||next.reverses!=current.reverses)
        throw std::runtime_error("Tween state mutation changes immutable Create parameters");
    const int previous=current.state;
    current.elapsed=next.elapsed;current.starts=std::move(next.starts);
    current.state=next.state;current.updated=std::chrono::steady_clock::now();
    if(previous!=current.state) {
        property_events(world,*node,"PlaybackState");
        if(current.state==4||current.state==5)
            tween_finished(L,world,*node,current,current.state,false);
    }
}

Json actor_tween_snapshot_internal(const World& world) {
    Json rows=Json::array();
    if(world.tweens.size()>kTweenLimit)throw std::runtime_error("Tween snapshot exceeds the instance limit");
    for(const auto& [id,state]:world.tweens) {
        auto node=world.nodes.find(id);
        if(node==world.nodes.end()||node->second.destroyed)continue;
        rows.push_back(Json{{"id",id},{"tween_playback",tween_playback_record(state)}});
    }
    return rows;
}

void actor_tween_restore_internal(World& world,const Json& snapshot) {
    if(!snapshot.is_array()||snapshot.size()>kTweenLimit)
        throw std::runtime_error("Actor Tween snapshot must be a bounded array");
    std::unordered_set<std::string> seen;
    for(const auto& row:snapshot) {
        if(!row.is_object()||!row.contains("id")||!row["id"].is_string()
           ||!row.contains("tween_playback"))
            throw std::runtime_error("Actor Tween snapshot entry is malformed");
        const std::string id=row["id"].get<std::string>();
        if(id.empty()||!seen.insert(id).second)
            throw std::runtime_error("Actor Tween snapshot contains a duplicate ID");
        Node* node=find_node(world,id);
        if(!node||node->class_name!="Tween"||node->server_owned
           ||node->constructor_defaults_known)
            throw std::runtime_error("Actor Tween snapshot does not match an engine-created Tween node");
        TweenPlaybackState state=actor_tween_state_from_record(row.at("tween_playback"),world);
        state.updated=std::chrono::steady_clock::now();
        if(!world.tweens.emplace(id,std::move(state)).second)
            throw std::runtime_error("Actor Tween snapshot collides with existing playback state");
    }
}
