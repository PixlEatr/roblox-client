#include <lua.h>
#include <lualib.h>
#include <Luau/Compiler.h>
#include "lstate.h"
#include <cstdio>
#include <string>
#include "memory_categories.inc"

int memory_probe(lua_State* L) {
    lua_pushinteger(L,L->activememcat);
    lua_pushnumber(L,double(lua_totalbytes(L,L->activememcat)));
    return 2;
}
int main() {
    lua_State* L=luaL_newstate();luaL_openlibs(L);
    lua_getglobal(L,"debug");install_memory_category_bindings(L);lua_pop(L,1);
    lua_pushcfunction(L,memory_probe,"memoryProbe");lua_setglobal(L,"memoryProbe");
    const char* source=R"LUA(
debug.resetmemorycategory();assert(memoryProbe()==0)
debug.setmemorycategory("Worker")
local worker,before=memoryProbe();assert(worker~=0)
local retained=table.create(8192,1)
local id,after=memoryProbe();assert(id==worker and after-before>32768)
local child=coroutine.create(function()
 assert(memoryProbe()==worker)
 debug.setmemorycategory("Child");assert(memoryProbe()~=worker)
 debug.resetmemorycategory();assert(memoryProbe()==0)
end)
assert(coroutine.resume(child));assert(memoryProbe()==worker)
debug.setmemorycategory("Worker");assert(memoryProbe()==worker)
assert(not pcall(debug.setmemorycategory,true));assert(memoryProbe()==worker)
assert(not pcall(debug.setmemorycategory,string.rep("x",257)));assert(memoryProbe()==worker)
debug.setmemorycategory(17);local numeric=memoryProbe()
debug.setmemorycategory("17");assert(memoryProbe()==numeric)
debug.resetmemorycategory();assert(memoryProbe()==0)
assert(retained[8192]==1)
local accepted,rejected=0,0
for i=1,300 do
 if pcall(debug.setmemorycategory,"Budget"..i)then accepted+=1 else rejected+=1 end
end
assert(accepted>100 and rejected>0)
debug.setmemorycategory("Worker");assert(memoryProbe()==worker)
debug.resetmemorycategory();assert(memoryProbe()==0)
)LUA";
    std::string code=Luau::compile(source);
    int status=luau_load(L,"=memory-category-native-test",code.data(),code.size(),0);
    if(!status)status=lua_pcall(L,0,0,0);
    if(status)std::fprintf(stderr,"%s\n",lua_tostring(L,-1));
    lua_close(L);
    if(!status)std::puts("native memory allocation attribution passed");
    return status?1:0;
}
