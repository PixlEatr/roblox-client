#pragma once
#include "sha256.hpp"
#include <cstdlib>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

// Optional private debugging output. No Luau binding can enable this. Never
// change compilation semantics when the trace is unavailable or exhausted.
inline void trace_local_source(const char* source,size_t length) noexcept {
    const char* directory=std::getenv("RCR_TRACE_LOADED_SOURCES");
    if(!directory||!*directory)return;
    static size_t written=0,files=0;
    constexpr size_t limit=128*1024*1024;
    if(files>=256||length>limit-written)return;
    int dir=-1,fd=-1;
    try {
        dir=open(directory,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
        struct stat info{};
        if(dir<0||fstat(dir,&info)||info.st_uid!=getuid()||(info.st_mode&0077)) {
            if(dir>=0)close(dir);return;
        }
        const std::string name=source_crypto::hex(source_crypto::sha256(
            reinterpret_cast<const uint8_t*>(source),length))+".luau";
        fd=openat(dir,name.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
        if(fd>=0) {
            size_t offset=0;
            while(offset<length) {
                auto n=write(fd,source+offset,length-offset);
                if(n<=0)break;
                offset+=static_cast<size_t>(n);
            }
            close(fd);fd=-1;
            if(offset==length){written+=length;++files;}
            else unlinkat(dir,name.c_str(),0);
        }
        close(dir);
    } catch(...) {if(fd>=0)close(fd);if(dir>=0)close(dir);}
}
