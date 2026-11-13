#include "audio_asset.hpp"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#ifdef __APPLE__
#include <AudioToolbox/AudioToolbox.h>
#include <dlfcn.h>
#endif

namespace audio_asset {
#ifdef __APPLE__
namespace {
struct API {
    void* library=dlopen("/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox",RTLD_NOW|RTLD_LOCAL);
    template<class T>T symbol(const char* name) {
        auto value=reinterpret_cast<T>(library?dlsym(library,name):nullptr);
        if(!value)throw std::runtime_error("native audio decoder unavailable");
        return value;
    }
    decltype(&AudioFileOpenWithCallbacks) open=symbol<decltype(open)>("AudioFileOpenWithCallbacks");
    decltype(&AudioFileClose) close=symbol<decltype(close)>("AudioFileClose");
    decltype(&AudioFileGetProperty) property=symbol<decltype(property)>("AudioFileGetProperty");
    decltype(&ExtAudioFileWrapAudioFileID) wrap=symbol<decltype(wrap)>("ExtAudioFileWrapAudioFileID");
    decltype(&ExtAudioFileDispose) dispose=symbol<decltype(dispose)>("ExtAudioFileDispose");
    decltype(&ExtAudioFileGetProperty) get=symbol<decltype(get)>("ExtAudioFileGetProperty");
    decltype(&ExtAudioFileSetProperty) set=symbol<decltype(set)>("ExtAudioFileSetProperty");
    decltype(&ExtAudioFileRead) read=symbol<decltype(read)>("ExtAudioFileRead");
};
struct View {const char* data;size_t size;};
OSStatus read_bytes(void* context,SInt64 offset,UInt32 requested,void* output,UInt32* actual) {
    const auto& view=*static_cast<View*>(context);*actual=0;
    if(offset<0||uint64_t(offset)>view.size)return kAudioFilePositionError;
    *actual=UInt32(std::min<size_t>(requested,view.size-size_t(offset)));
    if(*actual)std::memcpy(output,view.data+offset,*actual);
    return noErr;
}
SInt64 byte_count(void* context){return SInt64(static_cast<View*>(context)->size);}
struct File {
    API& api;AudioFileID file=nullptr;ExtAudioFileRef extended=nullptr;
    ~File(){if(extended)api.dispose(extended);if(file)api.close(file);}
};
}
#endif
Info inspect(const char* data,size_t size) {
    if(!data||size<12||size>100*1024*1024)throw std::runtime_error("invalid audio asset size");
#ifdef __APPLE__
    static API api;View view{data,size};File file{api};
    if(api.open(&view,read_bytes,nullptr,byte_count,nullptr,0,&file.file)!=noErr)
        throw std::runtime_error("unrecognized audio asset");
    AudioStreamBasicDescription format{};UInt32 bytes=sizeof(format);
    if(api.property(file.file,kAudioFilePropertyDataFormat,&bytes,&format)!=noErr||
       !std::isfinite(format.mSampleRate)||format.mSampleRate<=0||format.mChannelsPerFrame==0||format.mChannelsPerFrame>32)
        throw std::runtime_error("invalid audio stream format");
    if(api.wrap(file.file,false,&file.extended)!=noErr)throw std::runtime_error("audio decoder initialization failed");
    SInt64 frames=0;bytes=sizeof(frames);
    if(api.get(file.extended,kExtAudioFileProperty_FileLengthFrames,&bytes,&frames)!=noErr||frames<=0)
        throw std::runtime_error("audio duration unavailable");
    AudioStreamBasicDescription pcm{};
    pcm.mSampleRate=format.mSampleRate;pcm.mFormatID=kAudioFormatLinearPCM;
    pcm.mFormatFlags=kAudioFormatFlagIsFloat|kAudioFormatFlagIsPacked|kAudioFormatFlagsNativeEndian;
    pcm.mChannelsPerFrame=format.mChannelsPerFrame;pcm.mBitsPerChannel=32;
    pcm.mFramesPerPacket=1;pcm.mBytesPerFrame=pcm.mBytesPerPacket=4*pcm.mChannelsPerFrame;
    if(api.set(file.extended,kExtAudioFileProperty_ClientDataFormat,sizeof(pcm),&pcm)!=noErr)
        throw std::runtime_error("audio PCM conversion unavailable");
    std::vector<float> sample(128*pcm.mChannelsPerFrame);
    AudioBufferList buffers{};buffers.mNumberBuffers=1;
    buffers.mBuffers[0]={pcm.mChannelsPerFrame,UInt32(sample.size()*sizeof(float)),sample.data()};
    UInt32 requested=128;
    if(api.read(file.extended,&requested,&buffers)!=noErr||requested==0)
        throw std::runtime_error("audio sample decoding failed");
    const double duration=double(frames)/format.mSampleRate;
    if(!std::isfinite(duration)||duration<=0||duration>86400)throw std::runtime_error("audio duration exceeds playback limit");
    return {duration,format.mSampleRate,format.mChannelsPerFrame,uint64_t(frames),std::chrono::steady_clock::now()};
#else
    throw std::runtime_error("audio decoding is unavailable on this platform");
#endif
}

namespace {
class DecodeQueue {
    struct Job { std::string bytes; std::promise<Info> result; };
    std::mutex mutex;
    std::condition_variable ready;
    std::deque<Job> jobs;
    size_t pending_bytes=0,pending_count=0;
    bool stopping=false;
    std::thread worker;
    void run() {
        for(;;) {
            Job job;
            {
                std::unique_lock<std::mutex> lock(mutex);
                ready.wait(lock,[&](){return stopping||!jobs.empty();});
                if(stopping&&jobs.empty())return;
                job=std::move(jobs.front());jobs.pop_front();
            }
            try {job.result.set_value(inspect(job.bytes.data(),job.bytes.size()));}
            catch(...) {job.result.set_exception(std::current_exception());}
            std::lock_guard<std::mutex> lock(mutex);
            pending_bytes-=job.bytes.size();--pending_count;
        }
    }
public:
    DecodeQueue():worker([this](){run();}){}
    ~DecodeQueue() {
        {std::lock_guard<std::mutex> lock(mutex);stopping=true;}
        ready.notify_one();worker.join();
    }
    std::shared_future<Info> add(const char* data,size_t size) {
        std::lock_guard<std::mutex> lock(mutex);
        if(pending_count>=16||size>128*1024*1024-pending_bytes)
            throw std::runtime_error("local audio decode queue is full");
        Job job;job.bytes.assign(data,size);
        auto result=job.result.get_future().share();
        jobs.push_back(std::move(job));pending_bytes+=size;++pending_count;
        ready.notify_one();return result;
    }
};
}
std::shared_future<Info> prepare(const char* data,size_t size) {
    if(!data||size<12||size>100*1024*1024)throw std::runtime_error("invalid audio asset size");
    static DecodeQueue queue;
    return queue.add(data,size);
}
}
