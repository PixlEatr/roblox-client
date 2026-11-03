#pragma once
#include <lua.h>
#include <cstddef>
#include <string>

// All callbacks finish before Lua can raise an error. No Lua state crosses
// into Rust, and Rust-owned result buffers use the matching Rust release.
struct SourceBindings {
    int (*codec)(unsigned int operation, const unsigned char* input, size_t length,
                 unsigned char** output, size_t* output_length);
    void (*release)(unsigned char* output, size_t length);
};
class ScriptThreadIntegrity;
class ProtectedScriptStore;
std::string source_host_io(lua_State* L,const std::string& command);
void install_source_bindings(lua_State* L, const SourceBindings* bindings,
                             ScriptThreadIntegrity* thread_integrity=nullptr,
                             ProtectedScriptStore* protected_scripts=nullptr);
