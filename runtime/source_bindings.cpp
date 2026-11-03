#include "source_bindings.hpp"
#include "script_thread_integrity.hpp"
#include "protected_script_load.hpp"
#include "sha256.hpp"
#include "local_source_trace.hpp"
#include <Luau/Compiler.h>
#include <lualib.h>
#include <chrono>
#include <memory>
#include <string>
#include <vector>
#include <array>
#include <dlfcn.h>
#include <cstdlib>
#include <CommonCrypto/CommonCryptor.h>
#include <compression.h>
#include "lapi.h"
#include "lstate.h"
#include "lgc.h"
#include "lmem.h"
#include "lfunc.h"
#include "json.hpp"

namespace {
int device_tick(lua_State* L) {
    const auto now=std::chrono::system_clock::now().time_since_epoch();
    lua_pushnumber(L,std::chrono::duration<double>(now).count());return 1;
}
int getgenv(lua_State* L) { lua_setsafeenv(L,LUA_GLOBALSINDEX,false); lua_pushvalue(L,LUA_GLOBALSINDEX); return 1; }
int iscclosure(lua_State* L) { luaL_checktype(L,1,LUA_TFUNCTION); lua_pushboolean(L,lua_iscfunction(L,1)); return 1; }
int islclosure(lua_State* L) { luaL_checktype(L,1,LUA_TFUNCTION); lua_pushboolean(L,!lua_iscfunction(L,1)); return 1; }
int loadstring(lua_State* L) {
    size_t length=0;
    const char* source=luaL_checklstring(L,1,&length);
    const char* chunk=luaL_optstring(L,2,"=loadstring");
    if(length>local_test_source_limit(100*1024*1024)) { luaL_error(L,"loadstring source exceeds configured input limit"); return 0; }
    trace_local_source(source,length);
    auto* integrity=static_cast<ScriptThreadIntegrity*>(lua_touserdata(L,lua_upvalueindex(1)));
    if(integrity)integrity->check_load_thread();
    std::string code=Luau::compile(std::string(source,length));
    auto* store=static_cast<ProtectedScriptStore*>(lua_touserdata(L,lua_upvalueindex(2)));
    int status;
    if(store) {
        auto payload=store->make(std::string(source,length),code);
        status=load_protected_script(L,chunk,payload);
    } else status=luau_load(L,chunk,code.data(),code.size(),0);
    if(status!=0) {
        lua_pushnil(L); lua_insert(L,-2); return 2;
    }
    return 1;
}
int codec(lua_State* L) {
    const auto* bindings=static_cast<const SourceBindings*>(lua_touserdata(L,lua_upvalueindex(1)));
    unsigned int operation=static_cast<unsigned int>(lua_tointeger(L,lua_upvalueindex(2)));
    size_t length=0;
    const auto* source=reinterpret_cast<const unsigned char*>(luaL_checklstring(L,1,&length));
    unsigned char* output=nullptr;
    size_t output_length=0;
    int status=bindings->codec(operation,source,length,&output,&output_length);
    if(status!=0) {
        const char* message=status==2
            ?(operation==1?"malformed Base64 input":"malformed or oversized zstd input")
            :"codec input exceeds limit";
        luaL_error(L,"%s",message);return 0;
    }
    // The scoped owner also frees Rust data if pushing to the VM raises OOM.
    auto release=[bindings,output_length](unsigned char* pointer){bindings->release(pointer,output_length);};
    std::unique_ptr<unsigned char,decltype(release)> owner(output,release);
    lua_pushlstring(L,reinterpret_cast<const char*>(output),output_length);
    return 1;
}
std::string crypt_string(lua_State* L,int index) {
    size_t length=0;const char* value=luaL_checklstring(L,index,&length);
    if(length>256*1024)throw std::runtime_error("crypt input exceeds 256 KiB");
    return std::string(value,length);
}
void check_sha256(lua_State* L,int index) {
    size_t length=0;const char* value=luaL_checklstring(L,index,&length);std::string algorithm(value,length);
    if(algorithm!="sha256"&&algorithm!="sha-256")luaL_error(L,"unsupported hash algorithm");
}
#include "extended_crypto.inc"
int crypt_hmac(lua_State* L) {
    try{check_sha256(L,3);auto result=source_crypto::hex(source_crypto::hmac_sha256(crypt_string(L,1),crypt_string(L,2)));lua_pushlstring(L,result.data(),result.size());return 1;}
    catch(const std::exception& e){luaL_error(L,"crypt.hmac failed: %s",e.what());return 0;}
}
void push_codec(lua_State* L, const SourceBindings* bindings, int operation) {
    const char* name=operation==0?"base64.encode":operation==1?"base64.decode"
        :operation==2?"zstdcompress":"zstddecompress";
    lua_pushlightuserdata(L,const_cast<SourceBindings*>(bindings));
    lua_pushinteger(L,operation);
    lua_pushcclosurek(L,codec,name,2,nullptr);
}
}

#include "local_compat.inc"
#include "vm_debug_compat.inc"
#include "network_compat.inc"
#include "host_io_compat.inc"

void install_source_bindings(lua_State* L, const SourceBindings* bindings,
                             ScriptThreadIntegrity* thread_integrity,ProtectedScriptStore* protected_scripts) {
    lua_pushcfunction(L,device_tick,"tick");lua_setglobal(L,"tick");
    lua_pushcfunction(L,getgenv,"getgenv"); lua_setglobal(L,"getgenv");
    lua_pushcfunction(L,iscclosure,"iscclosure"); lua_setglobal(L,"iscclosure");
    lua_pushcfunction(L,islclosure,"islclosure"); lua_setglobal(L,"islclosure");
    lua_pushlightuserdata(L,thread_integrity);
    lua_pushlightuserdata(L,protected_scripts);
    lua_pushcclosurek(L,loadstring,"loadstring",2,nullptr); lua_setglobal(L,"loadstring");
    lua_newtable(L); lua_setglobal(L,"shared");
    lua_newtable(L); lua_setglobal(L,"_G");
    lua_newtable(L);
    push_codec(L,bindings,0);lua_setfield(L,-2,"encode");
    push_codec(L,bindings,1);lua_setfield(L,-2,"decode");
    lua_setreadonly(L,-1,true);
    lua_pushvalue(L,-1);lua_setglobal(L,"base64");
    lua_newtable(L);
    lua_pushvalue(L,-2);lua_setfield(L,-2,"base64");
    for(const char* name:{"base64encode","base64_encode"}) {
        push_codec(L,bindings,0);lua_pushvalue(L,-1);lua_setglobal(L,name);lua_setfield(L,-2,name);
    }
    for(const char* name:{"base64decode","base64_decode"}) {
        push_codec(L,bindings,1);lua_pushvalue(L,-1);lua_setglobal(L,name);lua_setfield(L,-2,name);
    }
    push_codec(L,bindings,2);lua_pushvalue(L,-1);lua_setglobal(L,"zstdcompress");lua_setfield(L,-2,"zstdcompress");
    push_codec(L,bindings,3);lua_pushvalue(L,-1);lua_setglobal(L,"zstddecompress");lua_setfield(L,-2,"zstddecompress");
    lua_pushcfunction(L,crypt_hash,"crypt.hash");lua_setfield(L,-2,"hash");
    for(auto item:{std::pair<const char*,lua_CFunction>{"encrypt",crypt_encrypt},{"decrypt",crypt_decrypt},{"generatebytes",crypt_generatebytes},{"generatekey",crypt_generatekey}}){
        lua_pushlightuserdata(L,const_cast<SourceBindings*>(bindings));lua_pushcclosurek(L,item.second,item.first,1,nullptr);lua_setfield(L,-2,item.first);
    }
    lua_pushcfunction(L,crypt_hmac,"crypt.hmac");lua_pushvalue(L,-1);lua_setglobal(L,"hmac");lua_setfield(L,-2,"hmac");
    lua_setreadonly(L,-1,true);lua_setglobal(L,"crypt");lua_pop(L,1);
    install_local_compat(L);
    install_vm_debug(L);
    install_network_compat(L,bindings);
    install_host_io(L,bindings);
    lua_pushcfunction(L,lz4_compress,"lz4compress");lua_setglobal(L,"lz4compress");
    lua_pushcfunction(L,lz4_decompress,"lz4decompress");lua_setglobal(L,"lz4decompress");
}
