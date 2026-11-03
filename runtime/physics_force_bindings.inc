int local_apply_impulse(lua_State* L) {
    auto* h = checked_handle(L, 1);
    const char* method = lua_tostring(L, lua_upvalueindex(1));
    if (h->node->destroyed) {
        luaL_error(L, "force receiver is destroyed");
        return 0;
    }
    World& world = *h->world;
    const bool part = class_is_a(world, h->node->class_name, "BasePart");
    if (!part) {
        luaL_error(L, "impulse method requires a BasePart receiver");
        return 0;
    }
    movement_bindings::enqueue_impulse(L, h->node->id,
                                       std::strcmp(method, "ApplyImpulseAtPosition") == 0,
                                       character_motion_root_id(world,*h->node));
    movement_bindings::tag_latest_mutation_cursor(L,static_cast<int>(world.mutations.size()));
    return 0;
}
