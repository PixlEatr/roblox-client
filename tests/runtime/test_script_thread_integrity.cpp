#include "script_thread_integrity.hpp"
#include <cassert>
#include <thread>

int main() {
    ScriptThreadIntegrity first,second;
    first.check_load_thread();
    auto state=first.snapshot();
    assert(state.checks==1 && state.failures==0);
    bool rejected=false;
    std::thread worker([&] {
        try {first.check_load_thread();}
        catch(const std::runtime_error&) {rejected=true;}
    });
    worker.join();
    assert(rejected);
    first.check_load_thread();
    state=first.snapshot();
    assert(state.checks==3 && state.failures==1);
    state=second.snapshot();
    assert(state.checks==0 && state.failures==0);
}
