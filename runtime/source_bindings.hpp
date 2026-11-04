#pragma once
#include <lua.h>
#include <cstddef>
#include <string>

// All callbacks finish before Lua can raise an error. No Lua state crosses
// into Rust, and Rust-owned result buffers use the matching Rust release.
struct SourceBindings {
    int (*apply_codec)(unsigned int operation, const unsigned char* input, size_t length,
                       unsigned char** output, size_t* output_length, int parameter);
    void (*release)(unsigned char* output, size_t length);
    int codec(unsigned int operation,const unsigned char* input,size_t length,
              unsigned char** output,size_t* output_length,int parameter=0) const {
        return apply_codec(operation,input,length,output,output_length,parameter);
    }
};
enum class EncodingCodec : unsigned int {
    Base64Encode=0, Base64Decode=1, Compress=2, DecompressedSize=4, Decompress=5,
    BufferHash=6, StringHash=7,
};
int source_encoding_codec(lua_State* L,EncodingCodec operation,int input_index,int parameter);
class ScriptThreadIntegrity;
class ProtectedScriptStore;
std::string source_host_io(lua_State* L,const std::string& command);
void install_source_bindings(lua_State* L, const SourceBindings* bindings,
                             ScriptThreadIntegrity* thread_integrity=nullptr,
                             ProtectedScriptStore* protected_scripts=nullptr);
