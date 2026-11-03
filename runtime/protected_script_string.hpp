#pragma once
#include <cstdint>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include "local_test_limits.hpp"

class ProtectedScriptPayload;
struct ProtectedScriptTestAccess;

// Session-owned immutable script storage. Native 1036d0b98 compares the
// interned first string with its construction input; 1036d0f98 compares that
// handle with the source handle after copying the pair. These checks concern
// script storage, not the network SharedString dictionary or signatures.
class ProtectedScriptStore {
public:
    struct Snapshot {uint64_t constructions,copies,failures;};
    ProtectedScriptPayload make(const std::string& source,const std::string& bytecode);
    Snapshot snapshot() const {
        std::lock_guard<std::mutex> lock(state_->mutex);
        return {state_->constructions,state_->copies,state_->failures};
    }
private:
    struct State {
        std::mutex mutex;
        std::unordered_map<std::string,std::weak_ptr<const std::string>> strings;
        uint64_t constructions=0,copies=0,failures=0;
    };
    std::shared_ptr<State> state_=std::make_shared<State>();
    friend class ProtectedScriptPayload;
    friend struct ProtectedScriptTestAccess;
};

class ProtectedScriptPayload {
public:
    ProtectedScriptPayload(const ProtectedScriptPayload& other)
        :state_(other.state_),source_(other.source_),bytecode_(other.bytecode_) {
        validate_copy(other);
    }
    ProtectedScriptPayload& operator=(const ProtectedScriptPayload&)=delete;
    const std::string& bytecode() const {return *bytecode_;}
private:
    ProtectedScriptPayload(std::shared_ptr<ProtectedScriptStore::State> state,
                           std::shared_ptr<const std::string> source,
                           std::shared_ptr<const std::string> code)
        :state_(std::move(state)),source_(std::move(source)),bytecode_(std::move(code)){}
    void validate_copy(const ProtectedScriptPayload& original) const {
        const bool matches=source_.get()==original.source_.get();
        {
            std::lock_guard<std::mutex> lock(state_->mutex);
            ++state_->copies;
            if(!matches)++state_->failures;
        }
        if(!matches)throw std::runtime_error("protected script string copy mismatch");
    }
    std::shared_ptr<ProtectedScriptStore::State> state_;
    std::shared_ptr<const std::string> source_,bytecode_;
    friend class ProtectedScriptStore;
    friend struct ProtectedScriptTestAccess;
};

inline ProtectedScriptPayload ProtectedScriptStore::make(const std::string& source,const std::string& bytecode) {
    if(source.size()>local_test_source_limit(100*1024*1024) || bytecode.size()>8*1024*1024)
        throw std::runtime_error("protected script storage input exceeds limit");
    std::lock_guard<std::mutex> lock(state_->mutex);
    for(auto it=state_->strings.begin();it!=state_->strings.end();)
        if(it->second.expired())it=state_->strings.erase(it);else ++it;
    auto intern=[&](const std::string& input) {
        auto it=state_->strings.find(input);
        if(it!=state_->strings.end())if(auto existing=it->second.lock())return existing;
        if(state_->strings.size()>=256)throw std::runtime_error("protected script storage limit exceeded");
        auto result=std::make_shared<const std::string>(input);
        state_->strings[input]=result;
        return result;
    };
    auto stored_source=intern(source),stored_bytecode=intern(bytecode);
    ++state_->constructions;
    if(*stored_source!=source) {
        ++state_->failures;
        throw std::runtime_error("protected script string construction mismatch");
    }
    return ProtectedScriptPayload(state_,std::move(stored_source),std::move(stored_bytecode));
}
