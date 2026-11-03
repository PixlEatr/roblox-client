// Players membership signals are tied to direct Player children of the
// replicated Players service. Keep this independent of general hierarchy
// listeners: USSI and ordinary scripts may subscribe to these signals alone.
void player_membership_events(World& w, Node& node,
                              const std::string& old_parent,
                              const std::string& new_parent) {
    if (old_parent == new_parent || !w.player_membership_events_observed ||
        node.class_name != "Player")
        return;

    const auto service = w.services.find("Players");
    if (service == w.services.end()) return;
    Node* players = find_node(w, service->second);
    if (!players || players->class_name != "Players") return;

    const Json player_ref{{"$ref", node.id}};
    if (old_parent == players->id && new_parent != players->id)
        emit(w, *players, "PlayerRemoving", Json::array({player_ref}));
    if (new_parent == players->id && old_parent != players->id)
        emit(w, *players, "PlayerAdded", Json::array({player_ref}));
}
