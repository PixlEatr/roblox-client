#include "legacy_value_bindings.hpp"

#include "vector2_bindings.hpp"

#include <lua.h>
#include <lualib.h>

#include <cmath>
#include <cstring>
#include <cstdint>
#include <limits>
#include <new>
#include <string>
#include <utility>

namespace legacy_value_bindings {
namespace {
constexpr const char* kCellIdMeta = "LocalRuntime.CellId";
constexpr const char* kPluginDragMeta = "LocalRuntime.PluginDrag";

// Recovered from the native CellId layout in the pinned 0.745 arm64 artifact:
// byte 0 is IsNil, floats at 4/8/12 are Location, and the nullable terrain
// shared pointer starts at 16. CellId.new initializes that pointer to null.
struct CellIdValue {
    bool is_nil;
    float x;
    float y;
    float z;
};

// The native PluginDrag constructor stores three strings, two ContentId string
// values, and a Vector2. Its Lua member table exposes all six as read-only.
struct PluginDragValue {
    std::string sender;
    std::string mime_type;
    std::string data;
    std::string mouse_icon;
    std::string drag_icon;
    float hot_spot_x;
    float hot_spot_y;
};

bool has_meta(lua_State* L, int index, const char* name) {
    if (!lua_isuserdata(L, index) || !lua_getmetatable(L, index)) return false;
    luaL_getmetatable(L, name);
    const bool matches = lua_rawequal(L, -1, -2) != 0;
    lua_pop(L, 2);
    return matches;
}

CellIdValue* check_cell_id(lua_State* L, int index) {
    return static_cast<CellIdValue*>(luaL_checkudata(L, index, kCellIdMeta));
}

PluginDragValue* check_plugin_drag(lua_State* L, int index) {
    return static_cast<PluginDragValue*>(luaL_checkudata(L, index, kPluginDragMeta));
}

void destroy_plugin_drag(lua_State*, void* raw) {
    static_cast<PluginDragValue*>(raw)->~PluginDragValue();
}

int push_cell_id(lua_State* L, const CellIdValue& value) {
    auto* out = static_cast<CellIdValue*>(lua_newuserdata(L, sizeof(CellIdValue)));
    new (out) CellIdValue(value);
    luaL_getmetatable(L, kCellIdMeta);
    lua_setmetatable(L, -2);
    return 1;
}

int push_plugin_drag(lua_State* L, PluginDragValue value) {
    auto* out = static_cast<PluginDragValue*>(lua_newuserdatadtor(L, sizeof(PluginDragValue), destroy_plugin_drag));
    new (out) PluginDragValue(std::move(value));
    luaL_getmetatable(L, kPluginDragMeta);
    lua_setmetatable(L, -2);
    return 1;
}

int cell_id_new(lua_State* L) {
    // Native helper sub_1013B30A0 reads an optional bool and three optional
    // numbers, with false/0 defaults, and ignores later arguments.
    const bool is_nil = luaL_optboolean(L, 1, 0) != 0;
    const float x = static_cast<float>(luaL_optnumber(L, 2, 0.0));
    const float y = static_cast<float>(luaL_optnumber(L, 3, 0.0));
    const float z = static_cast<float>(luaL_optnumber(L, 4, 0.0));
    return push_cell_id(L, CellIdValue{is_nil, x, y, z});
}

int cell_id_index(lua_State* L) {
    const auto* value = check_cell_id(L, 1);
    const char* key = luaL_checkstring(L, 2);
    if (std::strcmp(key, "IsNil") == 0) {
        lua_pushboolean(L, value->is_nil);
    } else if (std::strcmp(key, "Location") == 0) {
        // Native Vector3 storage and CellId coordinates are both float32.
        lua_pushvector(L, value->x, value->y, value->z);
    } else if (std::strcmp(key, "TerrainPart") == 0) {
        // The recovered constructor always initializes its shared terrain
        // pointer to null and accepts no TerrainPart parameter.
        lua_pushnil(L);
    } else {
        luaL_error(L, "'%s' is not a valid member of CellId", key);
        return 0;
    }
    return 1;
}

int cell_id_newindex(lua_State* L) {
    const char* key = luaL_checkstring(L, 2);
    if (std::strcmp(key, "IsNil") == 0 || std::strcmp(key, "Location") == 0 ||
        std::strcmp(key, "TerrainPart") == 0)
        luaL_error(L, "%s member of CellId is read-only and cannot be assigned to", key);
    else
        luaL_error(L, "'%s' is not a valid member of CellId", key);
    return 0;
}

int plugin_drag_new(lua_State* L) {
    const int count = lua_gettop(L);
    auto check_string = [L](int index, const char* message) {
        if (lua_type(L, index) != LUA_TSTRING) {
            luaL_error(L, "%s", message);
            return;
        }
    };
    if (count >= 1) check_string(1, "PluginDrag.new first arg should be a string for Sender.");
    if (count >= 2) check_string(2, "PluginDrag.new second arg should be a string for MimeType.");
    if (count >= 3) check_string(3, "PluginDrag.new third arg should be a string for Data.");
    if (count >= 4) check_string(4, "PluginDrag.new fourth arg should be a ContentId for MouseIcon.");
    if (count >= 5) check_string(5, "PluginDrag.new fifth arg should be a ContentId for DragIcon.");

    double hot_spot_x = 0.0, hot_spot_y = 0.0;
    if (count >= 6 && !read_vector2(L, 6, hot_spot_x, hot_spot_y))
    {
        luaL_error(L, "PluginDrag.new sixth arg should be a Vector2 for HotSpot.");
        return 0;
    }

    // Create non-trivial C++ objects only after all Lua checks that can raise
    // an error have completed.
    auto read_string = [L](int index) -> std::string {
        size_t length = 0;
        const char* text = lua_tolstring(L, index, &length);
        return std::string(text, length);
    };
    PluginDragValue value{};
    if (count >= 1) value.sender = read_string(1);
    if (count >= 2) value.mime_type = read_string(2);
    if (count >= 3) value.data = read_string(3);
    if (count >= 4) value.mouse_icon = read_string(4);
    if (count >= 5) value.drag_icon = read_string(5);
    value.hot_spot_x = static_cast<float>(hot_spot_x);
    value.hot_spot_y = static_cast<float>(hot_spot_y);

    return push_plugin_drag(L, std::move(value));
}

int plugin_drag_index(lua_State* L) {
    const auto* value = check_plugin_drag(L, 1);
    const char* key = luaL_checkstring(L, 2);
    if (std::strcmp(key, "Sender") == 0) lua_pushlstring(L, value->sender.data(), value->sender.size());
    else if (std::strcmp(key, "MimeType") == 0) lua_pushlstring(L, value->mime_type.data(), value->mime_type.size());
    else if (std::strcmp(key, "Data") == 0) lua_pushlstring(L, value->data.data(), value->data.size());
    else if (std::strcmp(key, "MouseIcon") == 0) lua_pushlstring(L, value->mouse_icon.data(), value->mouse_icon.size());
    else if (std::strcmp(key, "DragIcon") == 0) lua_pushlstring(L, value->drag_icon.data(), value->drag_icon.size());
    else if (std::strcmp(key, "HotSpot") == 0)
        return push_engine_vector2(L, value->hot_spot_x, value->hot_spot_y);
    else {
        luaL_error(L, "'%s' is not a valid member of PluginDrag", key);
        return 0;
    }
    return 1;
}

int plugin_drag_newindex(lua_State* L) {
    const char* key = luaL_checkstring(L, 2);
    if (std::strcmp(key, "Sender") == 0 || std::strcmp(key, "MimeType") == 0 ||
        std::strcmp(key, "Data") == 0 || std::strcmp(key, "MouseIcon") == 0 ||
        std::strcmp(key, "DragIcon") == 0 || std::strcmp(key, "HotSpot") == 0)
        luaL_error(L, "%s member of PluginDrag is read-only and cannot be assigned to", key);
    else
        luaL_error(L, "'%s' is not a valid member of PluginDrag", key);
    return 0;
}

bool valid_utf8(const char* text, size_t size) {
    size_t i = 0;
    while (i < size) {
        const unsigned char c = static_cast<unsigned char>(text[i]);
        if (c <= 0x7f) { ++i; continue; }
        size_t count = 0;
        uint32_t code = 0;
        if (c >= 0xc2 && c <= 0xdf) { count = 1; code = c & 0x1f; }
        else if (c >= 0xe0 && c <= 0xef) { count = 2; code = c & 0x0f; }
        else if (c >= 0xf0 && c <= 0xf4) { count = 3; code = c & 0x07; }
        else return false;
        if (i + count >= size) return false;
        for (size_t j = 1; j <= count; ++j) {
            const unsigned char next = static_cast<unsigned char>(text[i + j]);
            if ((next & 0xc0) != 0x80) return false;
            code = (code << 6) | (next & 0x3f);
        }
        if ((count == 2 && code < 0x800) || (count == 3 && code < 0x10000) ||
            (code >= 0xd800 && code <= 0xdfff) || code > 0x10ffff) return false;
        i += count + 1;
    }
    return true;
}

std::string bytes_hex(const char* data, size_t size) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string result;
    result.reserve(size * 2);
    for (size_t i = 0; i < size; ++i) {
        const auto byte = static_cast<unsigned char>(data[i]);
        result.push_back(digits[byte >> 4]);
        result.push_back(digits[byte & 0x0f]);
    }
    return result;
}

nlohmann::json encode_string(const std::string& text) {
    if (valid_utf8(text.data(), text.size())) return text;
    return nlohmann::json{{"$bytes_hex", bytes_hex(text.data(), text.size())}};
}

bool decode_string(const nlohmann::json& value, std::string& out) {
    if (value.is_string()) { out = value.get<std::string>(); return true; }
    if (!value.is_object() || value.size() != 1 || !value.contains("$bytes_hex") ||
        !value.at("$bytes_hex").is_string()) return false;
    const auto& hex = value.at("$bytes_hex").get_ref<const std::string&>();
    if (hex.size() % 2 || hex.size() > 2 * 1024 * 1024) return false;
    auto nibble = [](char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    };
    out.reserve(hex.size() / 2);
    for (size_t i = 0; i < hex.size(); i += 2) {
        const int high = nibble(hex[i]), low = nibble(hex[i + 1]);
        if (high < 0 || low < 0) return false;
        out.push_back(static_cast<char>((high << 4) | low));
    }
    return true;
}

nlohmann::json encode_number(double number) {
    if (std::isnan(number)) return nlohmann::json{{"$number", "nan"}};
    if (std::isinf(number)) return nlohmann::json{{"$number", number > 0 ? "inf" : "-inf"}};
    return number;
}

bool decode_number(const nlohmann::json& value, float& out) {
    double number;
    if (value.is_number()) number = value.get<double>();
    else if (value.is_object() && value.size() == 1 && value.contains("$number") &&
             value.at("$number").is_string()) {
        const std::string tag = value.at("$number").get<std::string>();
        if (tag == "nan") number = std::numeric_limits<double>::quiet_NaN();
        else if (tag == "inf") number = std::numeric_limits<double>::infinity();
        else if (tag == "-inf") number = -std::numeric_limits<double>::infinity();
        else return false;
    } else return false;
    out = static_cast<float>(number);
    return true;
}

void set_type(lua_State* L, const char* type) {
    lua_pushstring(L, type);
    lua_setfield(L, -2, "__type");
    lua_pushstring(L, type);
    lua_setfield(L, -2, "__metatable");
    lua_setreadonly(L, -1, true);
    lua_pop(L, 1);
}
} // namespace

void install(lua_State* L) {
    luaL_newmetatable(L, kCellIdMeta);
    lua_pushcfunction(L, cell_id_index, "CellId.__index"); lua_setfield(L, -2, "__index");
    lua_pushcfunction(L, cell_id_newindex, "CellId.__newindex"); lua_setfield(L, -2, "__newindex");
    set_type(L, "CellId");
    lua_createtable(L, 0, 1);
    lua_pushcfunction(L, cell_id_new, "CellId.new"); lua_setfield(L, -2, "new");
    lua_setreadonly(L, -1, true);
    lua_setglobal(L, "CellId");

    luaL_newmetatable(L, kPluginDragMeta);
    lua_pushcfunction(L, plugin_drag_index, "PluginDrag.__index"); lua_setfield(L, -2, "__index");
    lua_pushcfunction(L, plugin_drag_newindex, "PluginDrag.__newindex"); lua_setfield(L, -2, "__newindex");
    set_type(L, "PluginDrag");
    lua_createtable(L, 0, 1);
    lua_pushcfunction(L, plugin_drag_new, "PluginDrag.new"); lua_setfield(L, -2, "new");
    lua_setreadonly(L, -1, true);
    lua_setglobal(L, "PluginDrag");
}

bool is_value(lua_State* L, int index) {
    return has_meta(L, index, kCellIdMeta) || has_meta(L, index, kPluginDragMeta);
}

bool capture_value(lua_State* L, int index, nlohmann::json& value) {
    if (has_meta(L, index, kCellIdMeta)) {
        const auto* v = check_cell_id(L, index);
        value = {{"$cell_id", {{"is_nil", v->is_nil},
                                {"location", {encode_number(v->x), encode_number(v->y), encode_number(v->z)}},
                                {"terrain_part", nullptr}}}};
        return true;
    }
    if (has_meta(L, index, kPluginDragMeta)) {
        const auto* v = check_plugin_drag(L, index);
        value = {{"$plugin_drag", {{"sender", encode_string(v->sender)},
                                    {"mime_type", encode_string(v->mime_type)},
                                    {"data", encode_string(v->data)},
                                    {"mouse_icon", encode_string(v->mouse_icon)},
                                    {"drag_icon", encode_string(v->drag_icon)},
                                    {"hot_spot", {encode_number(v->hot_spot_x), encode_number(v->hot_spot_y)}}}}};
        return true;
    }
    return false;
}

bool push_value(lua_State* L, const nlohmann::json& value) {
    if (!value.is_object() || value.size() != 1) return false;
    if (value.contains("$cell_id")) {
        const auto& data = value.at("$cell_id");
        if (!data.is_object() || data.size() != 3 || !data.contains("is_nil") ||
            !data.contains("location") || !data.contains("terrain_part") ||
            !data.at("is_nil").is_boolean() || !data.at("terrain_part").is_null() ||
            !data.at("location").is_array() || data.at("location").size() != 3)
            return false;
        float xyz[3];
        for (int i = 0; i < 3; ++i)
            if (!decode_number(data.at("location").at(i), xyz[i])) return false;
        push_cell_id(L, CellIdValue{data.at("is_nil").get<bool>(), xyz[0], xyz[1], xyz[2]});
        return true;
    }
    if (value.contains("$plugin_drag")) {
        const auto& data = value.at("$plugin_drag");
        static const char* fields[] = {"sender", "mime_type", "data", "mouse_icon", "drag_icon", "hot_spot"};
        if (!data.is_object() || data.size() != 6) return false;
        for (const char* field : fields) if (!data.contains(field)) return false;
        const auto& spot = data.at("hot_spot");
        if (!spot.is_array() || spot.size() != 2) return false;
        float x, y;
        if (!decode_number(spot.at(0), x) || !decode_number(spot.at(1), y)) return false;
        std::string strings[5];
        for (int i = 0; i < 5; ++i)
            if (!decode_string(data.at(fields[i]), strings[i])) return false;
        PluginDragValue drag{std::move(strings[0]), std::move(strings[1]), std::move(strings[2]),
                             std::move(strings[3]), std::move(strings[4]), x, y};
        push_plugin_drag(L, std::move(drag));
        return true;
    }
    return false;
}
} // namespace legacy_value_bindings
