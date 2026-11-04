#include "source_scheduler.hpp"
#include <Luau/Compiler.h>
#include <lua.h>
#include <lualib.h>
#include <cassert>
#include <chrono>
#include <string>
#include <thread>
#include <cstdlib>
#include <vector>

using Clock = std::chrono::steady_clock;

static std::string string_at(lua_State* L, int index) {
    size_t size=0;
    const char* value=lua_tolstring(L,index,&size);
    return value?std::string(value,size):std::string();
}

int main() {
    lua_State* main=luaL_newstate();
    assert(main);
    luaL_openlibs(main);
    luaL_sandbox(main);

    lua_State* environment=lua_newthread(main);
    luaL_sandboxthread(environment);
    SourceScheduler scheduler(main,Clock::now()+std::chrono::seconds(2),true);
    scheduler.install(environment);

    lua_State* root=lua_newthread(environment);
    const std::string source=R"(
order={}
task.defer(function() table.insert(order,"callback-1") end)
task.defer(function() table.insert(order,"callback-2") end)
task.delay(0.001,function() table.insert(order,"timer") end)
)";
    const std::string bytecode=Luau::compile(source);
    assert(luau_load(root,"=scheduler-fairness",bytecode.data(),bytecode.size(),0)==0);
    scheduler.start(root);
    lua_pop(environment,1);

    // Ensure the timer is expired before step() enters its expensive pump.
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    const auto first_deadline=Clock::now()+std::chrono::milliseconds(20);
    bool pumped=false;
    scheduler.step(first_deadline,[&] {
        if(!pumped) {
            pumped=true;
            std::this_thread::sleep_until(first_deadline);
        }
    });

    lua_getglobal(environment,"order");
    assert(lua_istable(environment,-1));
    lua_rawgeti(environment,-1,1);
    assert(string_at(environment,-1)=="callback-1");
    lua_pop(environment,1);
    lua_rawgeti(environment,-1,2);
    assert(string_at(environment,-1)=="callback-2");
    lua_pop(environment,1);
    lua_rawgeti(environment,-1,3);
    assert(string_at(environment,-1)=="timer");
    lua_pop(environment,2);

    // Ready callbacks and the expired timer all ran before the expensive
    // pump. Another step must not repeat any of them.
    scheduler.step(Clock::now()+std::chrono::milliseconds(20));
    lua_getglobal(environment,"order");
    assert(lua_objlen(environment,-1)==3);lua_pop(environment,1);

    // A large authored job gets a bounded larger turn; callbacks still get
    // their short turn, and both are capped by the transaction deadline.
    setenv("RCR_LUAU_LARGE_TEST","1",1);
    lua_State* authored=lua_newthread(environment);
    const auto authored_code=Luau::compile("task.wait(.001);task.defer(function() callbackRan=true end);authoredRan=true");
    assert(luau_load(authored,"=authored-quantum",authored_code.data(),authored_code.size(),0)==0);
    scheduler.start(authored);lua_pop(environment,1);
    std::this_thread::sleep_for(std::chrono::milliseconds(3));
    std::vector<double> deadlines;
    const auto budget=Clock::now()+std::chrono::milliseconds(50);
    scheduler.step(budget,{},256,[&](Clock::time_point next){
        assert(next<=budget);
        deadlines.push_back(std::chrono::duration<double,std::milli>(next-Clock::now()).count());
    });
    assert(deadlines.size()==4);
    assert(deadlines[0]>4&&deadlines[0]<=8.1);
    assert(deadlines[2]>0&&deadlines[2]<=2.1);
    lua_getglobal(environment,"authoredRan");assert(lua_toboolean(environment,-1));lua_pop(environment,1);
    lua_getglobal(environment,"callbackRan");assert(lua_toboolean(environment,-1));lua_pop(environment,1);

    // Even an expensive pump must not wake a timer created in this frame.
    // Otherwise two authored poses separated by task.wait collapse into the
    // same outbound mutation batch and physics only sees the final pose.
    scheduler.begin_frame();
    lua_State* pulse=lua_newthread(environment);
    const auto pulse_code=Luau::compile(R"(
phases={1}
task.wait(0)
table.insert(phases,2)
task.delay(0,function() delayedFrame=true end)
task.wait(.001)
table.insert(phases,3)
)");
    assert(luau_load(pulse,"=frame-boundaries",pulse_code.data(),pulse_code.size(),0)==0);
    scheduler.start(pulse);lua_pop(environment,1);
    auto slow_pump=[](){std::this_thread::sleep_for(std::chrono::milliseconds(5));};
    scheduler.step(Clock::now()+std::chrono::milliseconds(50),slow_pump);
    lua_getglobal(environment,"phases");assert(lua_objlen(environment,-1)==1);lua_pop(environment,1);
    scheduler.step(Clock::now()+std::chrono::milliseconds(50),slow_pump);
    lua_getglobal(environment,"phases");assert(lua_objlen(environment,-1)==2);lua_pop(environment,1);
    lua_getglobal(environment,"delayedFrame");assert(lua_isnil(environment,-1));lua_pop(environment,1);
    scheduler.step(Clock::now()+std::chrono::milliseconds(50),slow_pump);
    lua_getglobal(environment,"phases");assert(lua_objlen(environment,-1)==3);lua_pop(environment,1);
    lua_getglobal(environment,"delayedFrame");assert(lua_toboolean(environment,-1));lua_pop(environment,1);

    lua_close(main);
}
