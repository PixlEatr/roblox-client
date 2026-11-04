// Preferences owned by this headless client, independent of account settings.
// Enum identities come from the pinned public version-745 API metadata.
constexpr const char* kUserGameSettingsId="__world_snapshot_user_game_settings__";
const Json& client_setting_defaults() {
    static const Json values=[](){
        Json out={{"MouseSensitivity",1.0},{"GamepadCameraSensitivity",1.0},
            {"CameraYInverted",false},{"IsUsingCameraYInverted",false},
            {"IsUsingGamepadCameraSensitivity",false},{"VignetteEnabled",true},
            {"VRSmoothRotationEnabled",false},{"VRThirdPersonFollowCamEnabled",false},
            {"Fullscreen",false},{"MasterVolume",1.0}};
        for(const char* name:{"ComputerCameraMovementMode","ComputerMovementMode","TouchCameraMovementMode","TouchMovementMode","ControlMode","RotationType"})
            out[name]=Json{{"$enum",{{"type",name},{"value",0}}}};
        return out;
    }();
    return values;
}
const Json* client_setting_descriptor(const Node& node,const std::string& key) {
    if(node.class_name!="UserGameSettings")return nullptr;
    static const Json descriptors=[](){
        Json out=Json::object();
        for(auto it=client_setting_defaults().begin();it!=client_setting_defaults().end();++it) {
            const auto& value=it.value();
            Json field{{"writable",it.key()!="IsUsingCameraYInverted"&&it.key()!="IsUsingGamepadCameraSensitivity"},
                {"type",value.is_boolean()?"boolean":value.is_number()?"number":"Enum"}};
            if(value.is_object())field["enum_type"]=value["$enum"]["type"];
            out[it.key()]=std::move(field);
        }
        return out;
    }();
    auto found=descriptors.find(key);return found==descriptors.end()?nullptr:&*found;
}
