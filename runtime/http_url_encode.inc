// HttpService:UrlEncode, bounded authored-source compatibility.
// Creator Hub shows UTF-8 percent escapes and `%20` for spaces. Its example
// also escapes a period (`%2E`); accordingly this leaves ASCII letters and
// digits literal and percent-encodes every other input byte.
int http_url_encode(lua_State* L) {
    Handle* service = checked_handle(L, 1);
    if (service->node->class_name != "HttpService") {
        luaL_error(L, "UrlEncode requires HttpService");
        return 0;
    }
    if (lua_type(L, 2) != LUA_TSTRING) {
        luaL_argerror(L, 2, "string expected");
        return 0;
    }

    size_t length = 0;
    const char* input = lua_tolstring(L, 2, &length);
    constexpr size_t kMaxUrlEncodeBytes = 128 * 1024;
    if (length > kMaxUrlEncodeBytes) {
        luaL_argerror(L, 2, "input exceeds 128 KiB");
        return 0;
    }

    static constexpr char kHex[] = "0123456789ABCDEF";
    std::string encoded;
    encoded.reserve(length * 3);
    for (size_t i = 0; i < length; ++i) {
        const unsigned char byte = static_cast<unsigned char>(input[i]);
        const bool ascii_alphanumeric =
            (byte >= 'A' && byte <= 'Z') ||
            (byte >= 'a' && byte <= 'z') ||
            (byte >= '0' && byte <= '9');
        if (ascii_alphanumeric) {
            encoded.push_back(static_cast<char>(byte));
        } else {
            encoded.push_back('%');
            encoded.push_back(kHex[byte >> 4]);
            encoded.push_back(kHex[byte & 0x0f]);
        }
    }
    lua_pushlstring(L, encoded.data(), encoded.size());
    return 1;
}
