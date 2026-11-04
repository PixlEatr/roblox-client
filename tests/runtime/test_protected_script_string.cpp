#include "protected_script_string.hpp"
#include <cassert>

// Native-only fault injection; no runtime or Luau API can access these hooks.
struct ProtectedScriptTestAccess {
    static void corrupt_intern(ProtectedScriptStore& store,const std::string& key,
                               const std::shared_ptr<const std::string>& wrong) {
        store.state_->strings[key]=wrong;
    }
    static bool same_source(const ProtectedScriptPayload& a,const ProtectedScriptPayload& b) {
        return a.source_==b.source_;
    }
    static void corrupt_copy(ProtectedScriptPayload& dest,const ProtectedScriptPayload& expected,
                             const ProtectedScriptPayload& wrong) {
        dest.source_=wrong.source_;
        dest.validate_copy(expected);
    }
};

int main() {
    ProtectedScriptStore store,other;
    auto first=store.make("return 42",std::string("binary\0code",11));
    auto second=store.make("return 42","another code");
    assert(ProtectedScriptTestAccess::same_source(first,second));
    auto copied=first;
    assert(copied.bytecode()==std::string("binary\0code",11));
    assert(store.snapshot().constructions==2 && store.snapshot().copies==1);
    auto separate=other.make("return 42","code");
    assert(!ProtectedScriptTestAccess::same_source(first,separate));
    bool rejected=false;
    try {ProtectedScriptTestAccess::corrupt_copy(copied,first,separate);}
    catch(const std::runtime_error&){rejected=true;}
    assert(rejected && store.snapshot().failures==1 && other.snapshot().failures==0);
    auto wrong=std::make_shared<const std::string>("wrong contents");
    ProtectedScriptTestAccess::corrupt_intern(store,"expected contents",wrong);
    rejected=false;
    try {(void)store.make("expected contents","code");}
    catch(const std::runtime_error&){rejected=true;}
    assert(rejected && store.snapshot().failures==2);
    for(int i=0;i<1000;++i){auto temporary=store.make(std::to_string(i),"inert");}
    assert(store.snapshot().failures==2);
}
