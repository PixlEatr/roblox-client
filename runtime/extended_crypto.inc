// Host cryptographic/compression providers; no substitute digests or PRNGs.
std::string crypto_base64(lua_State* L,const std::string& input,bool decode) {
    auto* bindings=static_cast<const SourceBindings*>(lua_touserdata(L,lua_upvalueindex(1)));
    unsigned char* data=nullptr;size_t length=0;
    int code=bindings->codec(decode?1:0,reinterpret_cast<const unsigned char*>(input.data()),input.size(),&data,&length);
    if(code)throw std::runtime_error("invalid or oversized Base64 data");
    auto release=[&](unsigned char* p){bindings->release(p,length);};
    std::unique_ptr<unsigned char,decltype(release)> owner(data,release);
    return std::string(reinterpret_cast<const char*>(data),length);
}
std::string secure_bytes(size_t size){
    std::string value(size,'\0');
    if(!host_entropy(value.data(),size))
        throw std::runtime_error("OS entropy unavailable");
    return value;
}
void* crypto_library() {
    static void* library=[](){
        for(const char* name:{"/opt/homebrew/opt/openssl@3/lib/libcrypto.dylib",
                "/usr/local/opt/openssl@3/lib/libcrypto.dylib","libcrypto.3.dylib","libcrypto.so.3",
                "libcrypto-3-x64.dll","libcrypto-3.dll","libcrypto-3-arm64.dll"})
            if(void* handle=dlopen(name,RTLD_LOCAL|RTLD_NOW))return handle;
        return static_cast<void*>(nullptr);
    }();
    return library;
}
int crypt_generatebytes(lua_State* L) {
    int size=luaL_checkinteger(L,1);if(size<0||size>128*1024){luaL_error(L,"random byte count must be 0..131072");return 0;}
    try{auto result=crypto_base64(L,secure_bytes(size),false);lua_pushlstring(L,result.data(),result.size());return 1;}
    catch(const std::exception& e){luaL_error(L,"crypt.generatebytes: %s",e.what());return 0;}
}
int crypt_generatekey(lua_State* L) {
    try{auto result=crypto_base64(L,secure_bytes(32),false);lua_pushlstring(L,result.data(),result.size());return 1;}
    catch(const std::exception& e){luaL_error(L,"crypt.generatekey: %s",e.what());return 0;}
}
struct CryptoEvpApi {
    using NewContext=void*(*)();
    using FreeContext=void(*)(void*);
    using CipherByName=const void*(*)(const char*);
    using Init=int(*)(void*,const void*,void*,const unsigned char*,const unsigned char*);
    using Update=int(*)(void*,unsigned char*,int*,const unsigned char*,int);
    using Final=int(*)(void*,unsigned char*,int*);
    using Control=int(*)(void*,int,int,void*);
    void* library=nullptr;
    NewContext new_context=nullptr;
    FreeContext free_context=nullptr;
    CipherByName cipher_by_name=nullptr;
    Init encrypt_init=nullptr,decrypt_init=nullptr;
    Update encrypt_update=nullptr,decrypt_update=nullptr;
    Final encrypt_final=nullptr,decrypt_final=nullptr;
    Control control=nullptr;
    bool ready()const{return new_context&&free_context&&cipher_by_name&&encrypt_init&&decrypt_init&&
        encrypt_update&&decrypt_update&&encrypt_final&&decrypt_final&&control;}
};
const CryptoEvpApi& crypto_evp_api() {
    static const CryptoEvpApi api=[](){
        CryptoEvpApi result;
        result.library=crypto_library();
        if(!result.library)return result;
        result.new_context=reinterpret_cast<CryptoEvpApi::NewContext>(dlsym(result.library,"EVP_CIPHER_CTX_new"));
        result.free_context=reinterpret_cast<CryptoEvpApi::FreeContext>(dlsym(result.library,"EVP_CIPHER_CTX_free"));
        result.cipher_by_name=reinterpret_cast<CryptoEvpApi::CipherByName>(dlsym(result.library,"EVP_get_cipherbyname"));
        result.encrypt_init=reinterpret_cast<CryptoEvpApi::Init>(dlsym(result.library,"EVP_EncryptInit_ex"));
        result.decrypt_init=reinterpret_cast<CryptoEvpApi::Init>(dlsym(result.library,"EVP_DecryptInit_ex"));
        result.encrypt_update=reinterpret_cast<CryptoEvpApi::Update>(dlsym(result.library,"EVP_EncryptUpdate"));
        result.decrypt_update=reinterpret_cast<CryptoEvpApi::Update>(dlsym(result.library,"EVP_DecryptUpdate"));
        result.encrypt_final=reinterpret_cast<CryptoEvpApi::Final>(dlsym(result.library,"EVP_EncryptFinal_ex"));
        result.decrypt_final=reinterpret_cast<CryptoEvpApi::Final>(dlsym(result.library,"EVP_DecryptFinal_ex"));
        result.control=reinterpret_cast<CryptoEvpApi::Control>(dlsym(result.library,"EVP_CIPHER_CTX_ctrl"));
        return result;
    }();
    return api;
}
std::string crypto_normalize_iv(const std::string& input,size_t size) {
    if(input.size()==size)return input;
    std::string output(size,'\0');
    for(size_t i=0;i<size&&!input.empty();++i)output[i]=input[i%input.size()];
    return output;
}
int crypto_cipher(lua_State* L,bool decrypt) {
    try {
        auto input=crypt_string(L,1),key=crypto_base64(L,crypt_string(L,2),true);
        if(key.size()!=16&&key.size()!=24&&key.size()!=32)throw std::runtime_error("AES key must decode to 16, 24, or 32 bytes");
        std::string mode=luaL_optstring(L,4,"CBC");
        if(mode!="CBC"&&mode!="CFB"&&mode!="OFB"&&mode!="CTR"&&mode!="ECB"&&mode!="GCM")throw std::runtime_error("unsupported AES mode");
        // CBC retains UNC's AES-128/192 compatibility; Opium's other modes are AES-256.
        if(mode!="CBC"&&key.size()!=32)throw std::runtime_error("expected a 32-byte AES key (base64-decoded)");
        const bool gcm=mode=="GCM";
        const size_t iv_size=mode=="ECB"?0:gcm?12:16;
        std::string iv;
        if(iv_size) {
            if(lua_isnoneornil(L,3)) {
                if(decrypt)throw std::runtime_error("decryption requires an IV");
                iv=secure_bytes(iv_size);
            } else {
                iv=crypto_base64(L,crypt_string(L,3),true);
                if(!decrypt&&iv.empty())iv=secure_bytes(iv_size);
                else iv=crypto_normalize_iv(iv,iv_size);
            }
        }
        if(decrypt)input=crypto_base64(L,input,true);
        std::string tag;
        if(decrypt&&gcm) {
            if(input.size()<16)throw std::runtime_error("ciphertext is too short for GCM authentication tag");
            tag=input.substr(input.size()-16);input.resize(input.size()-16);
        }
        const auto& api=crypto_evp_api();
        if(!api.ready())throw std::runtime_error("OpenSSL 3 EVP ciphers are required for AES");
        std::string algorithm="aes-"+std::to_string(key.size()*8)+"-";
        for(char c:mode)algorithm+=char(c>='A'&&c<='Z'?c+('a'-'A'):c);
        const void* cipher=api.cipher_by_name(algorithm.c_str());
        if(!cipher)throw std::runtime_error("requested AES cipher is unavailable");
        std::unique_ptr<void,decltype(api.free_context)> context(api.new_context(),api.free_context);
        if(!context)throw std::runtime_error("failed to create cipher context");
        auto init=decrypt?api.decrypt_init:api.encrypt_init;
        auto update=decrypt?api.decrypt_update:api.encrypt_update;
        auto final=decrypt?api.decrypt_final:api.encrypt_final;
        constexpr int set_iv_length=0x9,get_tag=0x10,set_tag=0x11;
        const unsigned char* iv_pointer=iv_size?reinterpret_cast<const unsigned char*>(iv.data()):nullptr;
        if(gcm) {
            if(init(context.get(),cipher,nullptr,nullptr,nullptr)!=1||
                api.control(context.get(),set_iv_length,static_cast<int>(iv_size),nullptr)!=1||
                init(context.get(),nullptr,nullptr,reinterpret_cast<const unsigned char*>(key.data()),iv_pointer)!=1)
                throw std::runtime_error("failed to initialize AES-GCM");
        } else if(init(context.get(),cipher,nullptr,reinterpret_cast<const unsigned char*>(key.data()),iv_pointer)!=1)
            throw std::runtime_error("failed to initialize AES-"+mode);
        std::string output(input.size()+16,'\0');int length=0,final_length=0;
        if(update(context.get(),reinterpret_cast<unsigned char*>(output.data()),&length,
            reinterpret_cast<const unsigned char*>(input.data()),static_cast<int>(input.size()))!=1)
            throw std::runtime_error("AES-"+mode+" update failed");
        if(decrypt&&gcm&&api.control(context.get(),set_tag,16,tag.data())!=1)
            throw std::runtime_error("failed to set GCM authentication tag");
        if(final(context.get(),reinterpret_cast<unsigned char*>(output.data())+length,&final_length)!=1) {
            if(decrypt&&gcm)throw std::runtime_error("AES-GCM authentication failed");
            throw std::runtime_error("AES-"+mode+" operation failed (invalid ciphertext or padding)");
        }
        if(length<0||final_length<0||static_cast<size_t>(length+final_length)>output.size())throw std::runtime_error("invalid cipher output length");
        output.resize(static_cast<size_t>(length+final_length));
        if(!decrypt&&gcm) {
            std::string authentication(16,'\0');
            if(api.control(context.get(),get_tag,16,authentication.data())!=1)throw std::runtime_error("failed to get GCM authentication tag");
            output+=authentication;
        }
        if(!decrypt)output=crypto_base64(L,output,false);
        lua_pushlstring(L,output.data(),output.size());
        if(decrypt)return 1;
        auto encoded=crypto_base64(L,iv,false);lua_pushlstring(L,encoded.data(),encoded.size());return 2;
    }catch(const std::exception& e){luaL_error(L,"crypt cipher: %s",e.what());return 0;}
}
int crypt_encrypt(lua_State* L){return crypto_cipher(L,false);}
int crypt_decrypt(lua_State* L){return crypto_cipher(L,true);}
int crypt_hash(lua_State* L) {
    try {
        auto input=crypt_string(L,1),algorithm=crypt_string(L,2);
        if(algorithm=="sha-256")algorithm="sha256";
        if(algorithm=="sha256"){auto result=source_crypto::hex(source_crypto::sha256(input));lua_pushlstring(L,result.data(),result.size());return 1;}
        const std::array<const char*,8> allowed={"sha1","sha384","sha512","md5","sha256","sha3-224","sha3-256","sha3-512"};
        bool known=false;for(auto name:allowed)if(algorithm==name)known=true;
        if(!known)throw std::runtime_error("unsupported hash algorithm");
        void* library=crypto_library();
        if(!library)throw std::runtime_error("OpenSSL 3 is required for extended hash algorithms");
        using Digest=int(*)(void*,const char*,const char*,const void*,size_t,unsigned char*,size_t*);
        auto digest=reinterpret_cast<Digest>(dlsym(library,"EVP_Q_digest"));
        if(!digest)throw std::runtime_error("OpenSSL 3 EVP_Q_digest unavailable");
        unsigned char bytes[64];size_t length=0;
        if(digest(nullptr,algorithm.c_str(),nullptr,input.data(),input.size(),bytes,&length)!=1||length>64)throw std::runtime_error("digest operation failed");
        static constexpr char hex[]="0123456789abcdef";std::string result;result.reserve(length*2);
        for(size_t i=0;i<length;++i){result+=hex[bytes[i]>>4];result+=hex[bytes[i]&15];}
        lua_pushlstring(L,result.data(),result.size());return 1;
    }catch(const std::exception& e){luaL_error(L,"crypt.hash: %s",e.what());return 0;}
}
#ifdef __APPLE__
using CompressionFunction=size_t(*)(uint8_t*,size_t,const uint8_t*,size_t,void*,compression_algorithm);
CompressionFunction compression_function(const char* name) {
    static void* library=dlopen("/usr/lib/libcompression.dylib",RTLD_LOCAL|RTLD_NOW);
    if(!library)throw std::runtime_error("macOS compression library unavailable");
    auto f=reinterpret_cast<CompressionFunction>(dlsym(library,name));
    if(!f)throw std::runtime_error("macOS compression function unavailable");return f;
}
#endif
int lz4_compress(lua_State* L) {
    try {
        auto input=crypt_string(L,1);if(input.empty()){lua_pushliteral(L,"");return 1;}
        std::string output(input.size()+input.size()/255+32,'\0');
#ifdef __APPLE__
        auto count=compression_function("compression_encode_buffer")(reinterpret_cast<uint8_t*>(output.data()),output.size(),reinterpret_cast<const uint8_t*>(input.data()),input.size(),nullptr,COMPRESSION_LZ4_RAW);
#else
        auto count=LZ4_compress_default(input.data(),output.data(),static_cast<int>(input.size()),static_cast<int>(output.size()));
#endif
        if(!count){
            // Apple's encoder declines some short/incompressible inputs. A
            // literal-only block is a valid interoperable raw LZ4 encoding.
            output.assign(1,char(std::min<size_t>(15,input.size())<<4));
            if(input.size()>=15){size_t remaining=input.size()-15;while(remaining>=255){output+=char(255);remaining-=255;}output+=char(remaining);}
            output+=input;count=output.size();
        }
        lua_pushlstring(L,output.data(),count);return 1;
    }catch(const std::exception& e){luaL_error(L,"lz4compress: %s",e.what());return 0;}
}
constexpr size_t kLz4OutputLimit=256*1024;
constexpr size_t kLz4InputLimit=kLz4OutputLimit+kLz4OutputLimit/255+32;
size_t lz4_decoded_size(const std::string& input) {
    if(input.empty())return 0;
    size_t cursor=0,decoded=0,last_match_start=0;bool saw_match=false;
    auto extended_length=[&](size_t base) {
        size_t length=base;
        while(true) {
            if(cursor>=input.size())throw std::runtime_error("truncated LZ4 length");
            const size_t extension=static_cast<unsigned char>(input[cursor++]);
            if(extension>kLz4OutputLimit-length)throw std::runtime_error("LZ4 output exceeds 256 KiB");
            length+=extension;
            if(extension!=255)return length;
        }
    };
    while(cursor<input.size()) {
        const unsigned char token=static_cast<unsigned char>(input[cursor++]);
        const size_t literal_length=(token>>4)==15?extended_length(15):size_t(token>>4);
        if(literal_length>input.size()-cursor)throw std::runtime_error("truncated LZ4 literals");
        if(literal_length>kLz4OutputLimit-decoded)throw std::runtime_error("LZ4 output exceeds 256 KiB");
        decoded+=literal_length;cursor+=literal_length;
        if(cursor==input.size()) {
            if(saw_match&&(literal_length<5||decoded-last_match_start<12))
                throw std::runtime_error("LZ4 block violates terminal literal constraints");
            return decoded;
        }
        if(input.size()-cursor<2)throw std::runtime_error("truncated LZ4 match offset");
        const size_t offset=static_cast<unsigned char>(input[cursor])|
            (size_t(static_cast<unsigned char>(input[cursor+1]))<<8);
        cursor+=2;
        if(offset==0||offset>decoded)throw std::runtime_error("invalid LZ4 match offset");
        saw_match=true;last_match_start=decoded;
        const size_t low_nibble=token&15;
        const size_t match_length=low_nibble==15?extended_length(19):low_nibble+4;
        if(match_length>kLz4OutputLimit-decoded)throw std::runtime_error("LZ4 output exceeds 256 KiB");
        decoded+=match_length;
    }
    throw std::runtime_error("LZ4 block is missing its final literal sequence");
}
int lz4_decompress(lua_State* L) {
    try {
        size_t input_length=0;const char* input_data=luaL_checklstring(L,1,&input_length);
        if(input_length>kLz4InputLimit)throw std::runtime_error("LZ4 input exceeds limit");
        std::string input(input_data,input_length);
        const size_t decoded_size=lz4_decoded_size(input);
        int size;
        if(lua_isnoneornil(L,2))size=static_cast<int>(decoded_size);
        else {
            size=luaL_checkinteger(L,2);
            if(size<0||size>int(kLz4OutputLimit))throw std::runtime_error("decompressed size must be 0..262144");
            if(size!=decoded_size)throw std::runtime_error("malformed LZ4 data or incorrect decompressed size");
        }
        if(size==0){lua_pushliteral(L,"");return 1;}
        // One extra byte detects declared sizes smaller than actual output.
        std::string output(size+1,'\0');
#ifdef __APPLE__
        auto count=compression_function("compression_decode_buffer")(reinterpret_cast<uint8_t*>(output.data()),output.size(),reinterpret_cast<const uint8_t*>(input.data()),input.size(),nullptr,COMPRESSION_LZ4_RAW);
#else
        auto count=LZ4_decompress_safe(input.data(),output.data(),static_cast<int>(input.size()),static_cast<int>(output.size()));
#endif
        if(!count||count!=size)throw std::runtime_error("malformed LZ4 data or incorrect decompressed size");
        lua_pushlstring(L,output.data(),size);return 1;
    }catch(const std::exception& e){luaL_error(L,"lz4decompress: %s",e.what());return 0;}
}
