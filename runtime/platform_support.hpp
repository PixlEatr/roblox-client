#pragma once
#include <cstddef>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <bcrypt.h>
inline int host_wide_main(int argc,wchar_t** argv,int (*entry)(int,char**)) {
    std::vector<std::string> values;
    std::vector<char*> pointers;
    values.reserve(argc); pointers.reserve(argc+1);
    for(int i=0;i<argc;i++) {
        int size=WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,argv[i],-1,nullptr,0,nullptr,nullptr);
        if(size<=0)return 2;
        values.emplace_back(static_cast<size_t>(size),'\0');
        if(!WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,argv[i],-1,values.back().data(),size,nullptr,nullptr))return 2;
        values.back().resize(size-1); pointers.push_back(values.back().data());
    }
    pointers.push_back(nullptr);
    return entry(argc,pointers.data());
}
constexpr int RTLD_LOCAL=0, RTLD_NOW=0;
inline void* dlopen(const char* path,int) { return reinterpret_cast<void*>(LoadLibraryA(path)); }
inline void* dlsym(void* handle,const char* name) {
    return reinterpret_cast<void*>(GetProcAddress(reinterpret_cast<HMODULE>(handle),name));
}
#else
#include <dlfcn.h>
#endif

inline bool host_entropy(void* data,size_t size) {
#ifdef _WIN32
    return size<=0xffffffffu && BCryptGenRandom(nullptr,static_cast<PUCHAR>(data),
        static_cast<ULONG>(size),BCRYPT_USE_SYSTEM_PREFERRED_RNG)==0;
#else
    std::ifstream source("/dev/urandom",std::ios::binary);
    return source && bool(source.read(static_cast<char*>(data),static_cast<std::streamsize>(size)));
#endif
}
