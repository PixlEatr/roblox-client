// Local input backend state, separate from server-replicated properties. A
// headless session starts with no attached input devices. A frontend may supply
// capabilities at initialization or through a host update when devices change.
// Event delivery is implemented by input_bindings.inc.
constexpr const char* kInputCapabilityNames[] = {
    "TouchEnabled", "KeyboardEnabled", "MouseEnabled", "GamepadEnabled",
    "AccelerometerEnabled", "GyroscopeEnabled", "VREnabled"
};

bool input_capability_key(const std::string& key) {
    for (const char* name : kInputCapabilityNames) if (key == name) return true;
    return false;
}

Json validated_input_capabilities(const Json& supplied) {
    if (!supplied.is_object() || supplied.size() > 7)
        throw std::runtime_error("input capabilities must be a bounded object");
    for (auto it = supplied.begin(); it != supplied.end(); ++it)
        if (!input_capability_key(it.key()) || !it.value().is_boolean())
            throw std::runtime_error("invalid local input capability");
    return supplied;
}

bool input_capability(const World& world, const std::string& key) {
    if(key=="GamepadEnabled"&&!world.input.gamepads.empty())return true;
    return world.input_capabilities.value(key, false);
}

int preferred_input(const World& world) {
    const bool keyboard_mouse=input_capability(world,"KeyboardEnabled")||input_capability(world,"MouseEnabled");
    const bool gamepad=input_capability(world,"GamepadEnabled");
    if(gamepad&&(!keyboard_mouse||(world.input.preferred_input_type>=12&&world.input.preferred_input_type<20)))return 1;
    if(keyboard_mouse)return 0;
    if(input_capability(world,"TouchEnabled"))return 2;
    // The desktop headless backend has no device attached until a frontend
    // supplies one. PreferredInput has no None member; keep the desktop mode
    // without claiming KeyboardEnabled or MouseEnabled.
    return 0;
}

void property_events(World& world, Node& node, const std::string& key);
void cancel_disabled_inputs(World& world);
void context_sync_button(World& world,const std::shared_ptr<ContextActionBinding>& binding);
void update_input_capabilities(World& world, const Json& supplied) {
    auto patch = validated_input_capabilities(supplied);
    const int old_preferred=preferred_input(world);
    std::vector<std::string> changed;
    for (auto it = patch.begin(); it != patch.end(); ++it)
        if (input_capability(world, it.key()) != it.value().get<bool>()) changed.push_back(it.key());
    for (auto it = patch.begin(); it != patch.end(); ++it) world.input_capabilities[it.key()] = it.value();
    for (auto& [id, node] : world.nodes)
        if (node.class_name == "UserInputService" && !node.destroyed) {
            for (const auto& key : changed) property_events(world, node, key);
            if(old_preferred!=preferred_input(world))property_events(world,node,"PreferredInput");
        }
    cancel_disabled_inputs(world);
    for(auto& binding:world.context_actions.bindings)
        if(binding->active)context_sync_button(world,binding);
}
