// Defaults from the copied 0.745 Player constructor, not inferred from an
// omitted packet field. Completed received creations may use these just as
// local construction does; explicit wire values always take precedence.
const Json* player_constructor_property(const Node& node,const std::string& key) {
    if(node.class_name!="Player")return nullptr;
    // 0x1046eb234: 0.5f at Player+0x32c. 0x1046eb22c: integer default
    // at 0x106abf3c8 (400), converted to float, at Player+0x328.
    static const Json minimum{{"type","number"},{"engine_type","float"},{"writable",true},{"value",0.5}};
    static const Json maximum{{"type","number"},{"engine_type","float"},{"writable",true},{"value",400.0}};
    // Player data constructor 0x1046ebab8 sets w20=1, then stores it at
    // data+0x2ee (Player+0x386), the DevEnableMouseLock getter's field.
    static const Json mouse_lock{{"type","boolean"},{"engine_type","bool"},{"writable",true},{"value",true}};
    if(key=="CameraMinZoomDistance")return &minimum;
    if(key=="CameraMaxZoomDistance")return &maximum;
    if(key=="DevEnableMouseLock")return &mouse_lock;
    // Reflection getters 0x100abd498/abdab8/abdc14/abdd70/abdecc/abe028
    // read data+0x298/2a4/2a8/2ac/2b0/2b4. The data constructor loads
    // CameraMode=0 from 0x105897170 and zeroes the other five words at
    // 0x1046ebbb0-0x1046ebbb4. These are constructor defaults, not an
    // inference from the absence of a replicated property.
    static const Json controls{
        {"CameraMode",{{"type","Enum"},{"enum_type","CameraMode"},{"writable",true},{"value",0}}},
        {"DevCameraOcclusionMode",{{"type","Enum"},{"enum_type","DevCameraOcclusionMode"},{"writable",true},{"value",0}}},
        {"DevComputerCameraMode",{{"type","Enum"},{"enum_type","DevComputerCameraMovementMode"},{"writable",true},{"value",0}}},
        {"DevTouchCameraMode",{{"type","Enum"},{"enum_type","DevTouchCameraMovementMode"},{"writable",true},{"value",0}}},
        {"DevComputerMovementMode",{{"type","Enum"},{"enum_type","DevComputerMovementMode"},{"writable",true},{"value",0}}},
        {"DevTouchMovementMode",{{"type","Enum"},{"enum_type","DevTouchMovementMode"},{"writable",true},{"value",0}}}
    };
    auto control=controls.find(key);
    if(control!=controls.end())return &*control;
    return nullptr;
}
