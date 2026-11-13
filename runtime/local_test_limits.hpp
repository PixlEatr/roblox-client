#pragma once
#include <cstdlib>
#include <cstring>
#include <cstddef>
// Explicit compatibility-test process profile. Normal limits stay unchanged.
inline bool large_local_test() {
    const char* value=std::getenv("RCR_LUAU_LARGE_TEST");return value&&std::strcmp(value,"1")==0;
}
inline bool unlimited_local_session() {
    const char* value=std::getenv("RCR_LUAU_UNLIMITED_SESSION");
    return large_local_test()&&value&&std::strcmp(value,"1")==0;
}
inline size_t local_test_source_limit(size_t normal){return normal;}
inline int local_test_time_limit(){return large_local_test()?30000:2000;}
inline int local_test_memory_limit(){return 512;}
