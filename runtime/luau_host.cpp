#include <Luau/Compiler.h>
#include <Luau/Bytecode.h>
#include <lua.h>
#include <lualib.h>
#include "random_compat.hpp"
#include "engine_bindings.hpp"
#include "world_bindings.hpp"

#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <limits>
#include <sstream>
#include <string>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <poll.h>
#include <vector>
#include <set>

namespace {
using Clock = std::chrono::steady_clock;
constexpr size_t kMaxSource = 128 * 1024;
constexpr size_t kMaxResult = 256 * 1024;
constexpr size_t kMaxOutput = 300 * 1024;
constexpr size_t kMaxWorldSnapshot = 8 * 1024 * 1024;

struct Limits {
    size_t cap = 32 * 1024 * 1024;
    size_t used = 0;
    Clock::time_point deadline;
    bool timed_out = false;
};

struct alignas(std::max_align_t) AllocationHeader { size_t size; };

void* capped_alloc(void* ud, void* ptr, size_t /*osize*/, size_t nsize) {
    auto* lim = static_cast<Limits*>(ud);
    if (nsize == 0) {
        if (ptr) {
            auto* header = static_cast<AllocationHeader*>(ptr) - 1;
            lim->used -= header->size;
            std::free(header);
        }
        return nullptr;
    }
    const size_t old = ptr ? (static_cast<AllocationHeader*>(ptr) - 1)->size : 0;
    if (old > lim->used || nsize > lim->cap - (lim->used - old))
        return nullptr;
    auto* header = static_cast<AllocationHeader*>(std::realloc(ptr ? static_cast<void*>(static_cast<AllocationHeader*>(ptr) - 1) : nullptr,
                                                                sizeof(AllocationHeader) + nsize));
    if (!header)
        return nullptr;
    header->size = nsize;
    lim->used = lim->used - old + nsize;
    return header + 1;
}

void interrupt(lua_State* L, int gc) {
    auto* lim = static_cast<Limits*>(lua_callbacks(L)->userdata);
    if (!lim || gc >= 0) return;
    if (Clock::now() >= lim->deadline) {
        lim->timed_out = true;
        if (lua_isyieldable(L)) lua_break(L);
    }
}

std::string json_escape(const std::string& in) {
    std::ostringstream out;
    for (unsigned char c : in) {
        switch (c) {
        case '"': out << "\\\""; break;
        case '\\': out << "\\\\"; break;
        case '\b': out << "\\b"; break;
        case '\f': out << "\\f"; break;
        case '\n': out << "\\n"; break;
        case '\r': out << "\\r"; break;
        case '\t': out << "\\t"; break;
        default:
            if (c < 0x20 || c >= 0x80) {
                const char* hex = "0123456789abcdef";
                out << "\\u00" << hex[c >> 4] << hex[c & 15];
            } else out << static_cast<char>(c);
        }
    }
    return out.str();
}

std::string error_json(const std::string& message, bool timeout = false) {
    return std::string("{\"ok\":false,\"values\":[],\"error\":\"") + json_escape(message) +
           "\",\"timed_out\":" + (timeout ? "true" : "false") + "}\n";
}
std::string world_error_json(world_bindings::World& world, const std::string& message, bool timeout = false) {
    using world_bindings::Json;
    Json result{{"ok",false},{"values",Json::array()},{"error",message},{"timed_out",timeout},
                {"output",world.output},{"output_truncated",world.output_truncated},{"mutations",world.mutations}};
    return result.dump(-1,' ',false,Json::error_handler_t::replace)+"\n";
}

void remove_global(lua_State* L, const char* name) {
    lua_pushnil(L);
    lua_setglobal(L, name);
}

bool emit_value(lua_State* L, int index, std::string& out, size_t& strings) {
    switch (lua_type(L, index)) {
    case LUA_TNIL: out += "null"; return true;
    case LUA_TBOOLEAN: out += lua_toboolean(L, index) ? "true" : "false"; return true;
    case LUA_TNUMBER: {
        const double n = lua_tonumber(L, index);
        if (!std::isfinite(n)) return false;
        std::ostringstream num;
        num.precision(std::numeric_limits<double>::max_digits10);
        num << n;
        out += num.str();
        return true;
    }
    case LUA_TSTRING: {
        size_t len = 0;
        const char* s = lua_tolstring(L, index, &len);
        strings += len;
        if (strings > kMaxResult) return false;
        out += "\"" + json_escape(std::string(s, len)) + "\"";
        return out.size() <= kMaxOutput;
    }
    default: return false;
    }
}

std::string run_vm(const std::string& source, const std::vector<double>& args,
                   bool is_bytecode, int timeout_ms, int memory_mb,
                   const LocalEngineContext* engine_context,
                   const std::string* world_snapshot_text) {
    std::unique_ptr<world_bindings::World> world;
    if (world_snapshot_text) {
        try { world=std::make_unique<world_bindings::World>(world_bindings::parse_snapshot(*world_snapshot_text)); }
        catch(const std::exception&e){return error_json(std::string("invalid world snapshot: ")+e.what());}
    }
    Limits limits;
    limits.cap = static_cast<size_t>(memory_mb) * 1024 * 1024;
    limits.deadline = Clock::now() + std::chrono::milliseconds(timeout_ms);
    lua_State* L = lua_newstate(capped_alloc, &limits);
    if (!L) return error_json("memory limit reached while creating VM");

    lua_Callbacks* cb = lua_callbacks(L);
    cb->userdata = &limits;
    cb->interrupt = interrupt;
    luaL_openlibs(L);
    // Plain Luau libraries here. Extra host APIs need their own setup.
    for (const char* name : {"require", "loadstring", "dofile", "loadfile", "io", "debug"})
        remove_global(L, name);
    lua_getglobal(L, "os");
    if (lua_istable(L, -1)) { lua_pushnil(L); lua_setfield(L, -2, "exit"); }
    lua_pop(L, 1);
    install_random_compat(L);
    if (engine_context) install_local_engine(L, *engine_context);
    remove_global(L, "print"); // stdout is reserved for the JSON result
    if (world) world_bindings::install(L,*world);
    luaL_sandbox(L);

    std::string bytecode;
    if (is_bytecode) {
        bytecode = source;
    } else {
        try {
            bytecode = Luau::compile(source);
        } catch (const std::exception& e) {
            lua_close(L);
            return error_json(std::string("compile failed: ") + e.what());
        }
    }
    lua_State* co = lua_newthread(L);
    luaL_sandboxthread(co);
    if (luau_load(co, "=user-source", bytecode.data(), bytecode.size(), 0) != 0) {
        size_t n = 0;
        const char* e = lua_tolstring(co, -1, &n);
        std::string msg = e ? std::string(e, n) : "Luau load failed";
        lua_close(L);
        return error_json(msg);
    }
    for (double arg : args) lua_pushnumber(co, arg);
    int status = lua_resume(co, L, static_cast<int>(args.size()));
    if (limits.timed_out || Clock::now() >= limits.deadline) {
        lua_close(L);
        return world ? world_error_json(*world,"execution time limit exceeded",true) : error_json("execution time limit exceeded", true);
    }
    if (status != 0) {
        size_t n = 0;
        const char* e = lua_tolstring(co, -1, &n);
        std::string msg = e ? std::string(e, n) : "Luau runtime error";
        lua_close(L);
        return world ? world_error_json(*world,msg) : error_json(msg);
    }
    if(world){
        using world_bindings::Json;
        Json result{{"ok",true},{"values",Json::array()},{"output",world->output},
                    {"output_truncated",world->output_truncated},{"mutations",world->mutations}};
        try { for(int i=1;i<=lua_gettop(co);++i) result["values"].push_back(world_bindings::capture_value(co,*world,i)); }
        catch(const std::exception&e){lua_close(L);return world_error_json(*world,std::string("result capture failed: ")+e.what());}
        std::string out=result.dump(-1,' ',false,Json::error_handler_t::replace)+"\n";
        lua_close(L);return out;
    }
    std::string out = "{\"ok\":true,\"values\":[";
    const int count = lua_gettop(co);
    size_t strings = 0;
    for (int i = 1; i <= count; ++i) {
        if (i > 1) out += ',';
        if (!emit_value(co, i, out, strings)) {
            lua_close(L);
            return error_json("return values must be nil, boolean, finite number, or bounded string");
        }
    }
    out += "]}\n";
    lua_close(L);
    return out;
}

bool parse_num(const char* s, double& value) {
    char* end = nullptr;
    errno = 0;
    value = std::strtod(s, &end);
    return errno == 0 && end != s && *end == '\0' && std::isfinite(value);
}

bool parse_context_file(const std::string& path, LocalEngineContext& context, std::string& error) {
    if (path.empty()) return true;
    std::ifstream f(path, std::ios::binary | std::ios::ate);
    if (!f) { error = "cannot read local context file"; return false; }
    auto size = f.tellg();
    if (size < 0 || size > 4096) { error = "context file exceeds 4 KiB"; return false; }
    std::string contents(static_cast<size_t>(size), '\0');
    f.seekg(0);
    if (!contents.empty() && !f.read(contents.data(), static_cast<std::streamsize>(contents.size()))) {
        error = "cannot read local context file"; return false;
    }
    std::set<std::string> seen;
    bool mode_ok = false, studio_seen = false, job_seen = false;
    bool session_metadata = false;
    size_t offset = 0;
    while (offset < contents.size()) {
        size_t end = contents.find('\n', offset);
        if (end == std::string::npos) end = contents.size();
        std::string line = contents.substr(offset, end - offset);
        offset = end + 1;
        if (line.empty()) continue;
        for (unsigned char c : line) {
            if (c < 0x20 || c > 0x7e) { error = "context file must contain printable ASCII and LF newlines"; return false; }
        }
        size_t eq = line.find('=');
        if (eq == std::string::npos) { error = "invalid context key/value line"; return false; }
        std::string key = line.substr(0, eq), value = line.substr(eq + 1);
        if (!seen.insert(key).second) { error = "duplicate context key"; return false; }
        if (key == "mode") {
            if (value != "offline" && value != "session") {
                error = "context mode must be offline or session"; return false;
            }
            session_metadata = value == "session";
            mode_ok = true;
        } else if (key == "job_id") {
            if (value.empty() || value.size() > 128) {
                error = "invalid job_id length"; return false;
            }
            context.job_id = value; job_seen = true;
        } else if (key == "is_studio") {
            if (value != "true" && value != "false") { error = "is_studio must be true or false"; return false; }
            context.is_studio = value == "true"; studio_seen = true;
        } else if (key == "services") {
            context.restrict_services = true;
            const std::set<std::string> allowed = {"Workspace", "Players", "RunService", "Lighting",
                "ReplicatedStorage", "ReplicatedFirst", "StarterGui", "StarterPlayer", "SoundService",
                "TweenService", "UserInputService", "HttpService", "CollectionService",
                "ContextActionService", "GuiService", "Teams", "TextService", "CoreGui"};
            size_t start = 0;
            while (start < value.size()) {
                size_t comma = value.find(',', start);
                if (comma == std::string::npos) comma = value.size();
                std::string name = value.substr(start, comma - start);
                if (!allowed.count(name) || !context.services.insert(name).second) {
                    error = "unknown or duplicate local service"; return false;
                }
                start = comma + 1;
            }
        } else { error = "unsupported local context key"; return false; }
    }
    if (!mode_ok || !job_seen || !seen.count("services")) {
        error = "context requires mode, job_id, and services"; return false;
    }
    if (session_metadata) {
        // This is an interpreter, so IsStudio stays false.
        if (studio_seen) { error = "session mode cannot override IsStudio"; return false; }
        const std::string& id = context.job_id;
        if (id.size() != 36) { error = "session job_id must be a UUID"; return false; }
        for (size_t i = 0; i < id.size(); ++i) {
            const bool hyphen = i == 8 || i == 13 || i == 18 || i == 23;
            const char c = id[i];
            const bool hex = (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') ||
                             (c >= 'A' && c <= 'F');
            if (hyphen ? c != '-' : !hex) { error = "invalid session job_id"; return false; }
        }
        context.is_studio = false;
    } else if (!studio_seen || context.job_id.rfind("offline-fixture-", 0) != 0) {
        error = "offline context requires synthetic job_id and explicit is_studio"; return false;
    }
    return true;
}

bool validate_bytecode_header(const std::string& bytes, std::string& error) {
    if (bytes.size() < 2) { error = "bytecode header is incomplete"; return false; }
    const uint8_t version = static_cast<uint8_t>(bytes[0]);
    if (version < LBC_VERSION_MIN || version > LBC_VERSION_MAX) {
        error = "unsupported Luau bytecode version or encoded envelope"; return false;
    }
    if (version >= 4) {
        const uint8_t types = static_cast<uint8_t>(bytes[1]);
        if (types < LBC_TYPE_VERSION_MIN || types > LBC_TYPE_VERSION_MAX) {
            error = "unsupported Luau bytecode type version"; return false;
        }
    }
    return true;
}

void write_all(int fd, const char* data, size_t size) {
    while (size) {
        ssize_t n = ::write(fd, data, size);
        if (n > 0) { data += n; size -= static_cast<size_t>(n); }
        else if (n < 0 && errno == EINTR) continue;
        else break;
    }
}

int main_cli(int argc, char** argv) {
    std::string source_path;
    std::string bytecode_path;
    std::string context_path;
    std::string world_snapshot_path;
    int timeout_ms = 250;
    int memory_mb = 32;
    bool args_started = false;
    std::vector<double> args;
    for (int i = 1; i < argc; ++i) {
        if (!args_started && std::strcmp(argv[i], "--") == 0) { args_started = true; continue; }
        if (args_started) {
            double n;
            if (!parse_num(argv[i], n)) { std::cout << error_json("arguments after -- must be finite numbers"); return 2; }
            args.push_back(n);
        } else if (std::strcmp(argv[i], "--source") == 0 && i + 1 < argc) source_path = argv[++i];
        else if (std::strcmp(argv[i], "--bytecode") == 0 && i + 1 < argc) bytecode_path = argv[++i];
        else if (std::strcmp(argv[i], "--timeout-ms") == 0 && i + 1 < argc) timeout_ms = std::atoi(argv[++i]);
        else if (std::strcmp(argv[i], "--memory-mb") == 0 && i + 1 < argc) memory_mb = std::atoi(argv[++i]);
        else if (std::strcmp(argv[i], "--context-file") == 0 && i + 1 < argc) context_path = argv[++i];
        else if (std::strcmp(argv[i], "--world-snapshot") == 0 && i + 1 < argc) world_snapshot_path = argv[++i];
        else { std::cout << error_json("usage: luau-host (--source PATH | --bytecode PATH) [--context-file PATH] [--world-snapshot PATH] [--timeout-ms 1..2000] [--memory-mb 8..128] [-- NUMBER ...]"); return 2; }
    }
    if ((source_path.empty() == bytecode_path.empty()) || timeout_ms < 1 || timeout_ms > 2000 || memory_mb < 8 || memory_mb > 128 || args.size() > 64) {
        std::cout << error_json("invalid source or resource limit"); return 2;
    }
    const bool is_bytecode = !bytecode_path.empty();
    if ((!world_snapshot_path.empty() && (is_bytecode || !context_path.empty()))) {
        std::cout << error_json("world snapshots require source mode and cannot be combined with a context file"); return 2;
    }
    const std::string input_path = is_bytecode ? bytecode_path : source_path;
    std::ifstream f(input_path, std::ios::binary | std::ios::ate);
    if (!f) { std::cout << error_json("cannot read input file"); return 2; }
    auto source_size = f.tellg();
    if (source_size < 0 || static_cast<uint64_t>(source_size) > kMaxSource) {
        std::cout << error_json("input exceeds 128 KiB limit"); return 2;
    }
    std::string source(static_cast<size_t>(source_size), '\0');
    f.seekg(0);
    if (!source.empty() && !f.read(source.data(), static_cast<std::streamsize>(source.size()))) {
        std::cout << error_json("cannot read input file"); return 2;
    }
    if (is_bytecode) {
        std::string bytecode_error;
        if (!validate_bytecode_header(source, bytecode_error)) {
            std::cout << error_json(bytecode_error); return 2;
        }
    }
    LocalEngineContext engine_context;
    std::string context_error;
    if (!context_path.empty() && !parse_context_file(context_path, engine_context, context_error)) {
        std::cout << error_json(context_error); return 2;
    }
    std::string world_snapshot_text;
    if (!world_snapshot_path.empty()) {
        std::ifstream snapshot(world_snapshot_path,std::ios::binary|std::ios::ate);
        if(!snapshot){std::cout<<error_json("cannot read world snapshot");return 2;}
        auto n=snapshot.tellg();if(n<0||static_cast<uint64_t>(n)>kMaxWorldSnapshot){std::cout<<error_json("world snapshot exceeds 8 MiB");return 2;}
        world_snapshot_text.resize(static_cast<size_t>(n));snapshot.seekg(0);
        if(!world_snapshot_text.empty()&&!snapshot.read(world_snapshot_text.data(),static_cast<std::streamsize>(world_snapshot_text.size()))){std::cout<<error_json("cannot read world snapshot");return 2;}
    }

    int pipefd[2];
    if (pipe(pipefd) != 0) { std::cout << error_json("cannot create result pipe"); return 2; }
    pid_t pid = fork();
    if (pid < 0) { close(pipefd[0]); close(pipefd[1]); std::cout << error_json("cannot start VM worker"); return 2; }
    if (pid == 0) {
        close(pipefd[0]);
        std::string result;
        try { result = run_vm(source, args, is_bytecode, timeout_ms, memory_mb, context_path.empty() ? nullptr : &engine_context,
                             world_snapshot_path.empty() ? nullptr : &world_snapshot_text); }
        catch (...) { result = error_json("VM worker failed"); }
        write_all(pipefd[1], result.data(), result.size());
        close(pipefd[1]);
        _exit(0);
    }
    close(pipefd[1]);
    fcntl(pipefd[0], F_SETFL, fcntl(pipefd[0], F_GETFL) | O_NONBLOCK);
    const auto deadline = Clock::now() + std::chrono::milliseconds(timeout_ms + 2000);
    std::string result;
    bool done = false, overflow = false;
    int child_status = 0;
    while (!done) {
        char buf[8192];
        while (true) {
            ssize_t n = read(pipefd[0], buf, sizeof(buf));
            if (n > 0) {
                if (result.size() + static_cast<size_t>(n) > kMaxOutput) overflow = true;
                else result.append(buf, static_cast<size_t>(n));
            } else break;
        }
        pid_t w = waitpid(pid, &child_status, WNOHANG);
        if (w == pid) { done = true; break; }
        if (Clock::now() >= deadline) {
            kill(pid, SIGKILL); waitpid(pid, &child_status, 0); close(pipefd[0]);
            std::cout << error_json("worker wall time limit exceeded", true);
            return 124;
        }
        pollfd pfd{pipefd[0], POLLIN, 0};
        poll(&pfd, 1, 10);
    }
    char buf[8192];
    while (true) {
        ssize_t n = read(pipefd[0], buf, sizeof(buf));
        if (n > 0) {
            if (result.size() + static_cast<size_t>(n) > kMaxOutput) overflow = true;
            else result.append(buf, static_cast<size_t>(n));
        } else break;
    }
    close(pipefd[0]);
    if (overflow) std::cout << error_json("result size limit exceeded");
    else if (result.empty()) std::cout << error_json("VM worker returned no result");
    else std::cout << result;
    return 0;
}
} // namespace

int main(int argc, char** argv) { return main_cli(argc, argv); }
