// Received class ancestry lookup for reflected signals. Keep this helper
// isolated from click/connection compatibility code.
const Json* received_signal_signature(World& world,const std::string& class_name,
                                      const std::string& event_name) {
    std::string current=class_name;
    std::unordered_set<std::string> seen;
    while(seen.insert(current).second) {
        auto cls=world.class_event_signatures.find(current);
        if(cls!=world.class_event_signatures.end()&&cls->is_object()) {
            auto signature=cls->find(event_name);
            if(signature!=cls->end())return &*signature;
        }
        auto parent=world.class_parents.find(current);
        if(parent==world.class_parents.end()||!parent->is_string())break;
        current=parent->get<std::string>();
    }
    return nullptr;
}
