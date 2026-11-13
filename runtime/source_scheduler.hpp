#pragma once
#include <lua.h>
#include <chrono>
#include <deque>
#include <functional>
#include <utility>
#include <unordered_map>
#include <vector>
#include <string>
#include <optional>
#include <cstdint>

// A bounded cooperative scheduler shared by authored source jobs and callbacks.
class SourceScheduler {
public:
    using Clock=std::chrono::steady_clock;
    SourceScheduler(lua_State* main,Clock::time_point deadline,bool persistent=false):main_(main),deadline_(deadline),persistent_(persistent){}
    ~SourceScheduler();
    static SourceScheduler* for_thread(lua_State* thread);
    bool wait_external(lua_State* thread,std::function<std::optional<std::string>()> poll,std::function<void()> discard,
                       std::function<void()> claim={});
    bool wait_socket_read(lua_State* thread,std::function<std::optional<std::string>()> poll);
    bool external_continuation_entered(lua_State* thread,std::string& payload);
    void install(lua_State* thread);
    int run(lua_State* root,int arguments,std::function<void()> pump={});
    lua_State* callback(lua_State* from,int reference,const std::function<int(lua_State*)>& arguments,std::function<bool()> guard={},const std::string& owner="",int identity=-1,int creator_reference=LUA_NOREF,bool response_ready=false);
    lua_State* defer_response(lua_State* from,int function_index);
    bool in_external_response_continuation(lua_State* thread) const;
    // Starts a callback immediately until it yields, completes, or exhausts
    // its scheduling slice. Leaves the child thread on the caller's stack.
    lua_State* callback_now(lua_State* from,int reference,const std::function<int(lua_State*)>& arguments,const std::string& owner,int identity=-1,int creator_reference=LUA_NOREF);
    void wait_signal(lua_State* thread);
    void wake_signal(lua_State* thread,const std::function<int(lua_State*)>& arguments,bool response_ready=false);
    const std::vector<std::string>& errors() const{return errors_;}
    // Opt in only for authored root tasks owned by a persistent live client.
    // Standalone root jobs retain their one-request CPU timeout by default.
    void start(lua_State* root,int arguments=0,bool resume_across_frames=false);
    int completion(lua_State* root) const;
    const char* task_state(lua_State* thread) const;
    struct Progress {size_t resumes=0,yields=0,interruptions=0;double execution_ms=0;bool response_priority_active=false;};
    Progress task_progress(lua_State* thread) const;
    struct ScriptMainDiagnostic {lua_State* thread=nullptr;std::string owner,state;Progress progress;};
    // Read-only view of active authored script entry tasks. Does not expose
    // source, arguments, environments, or mutate scheduler state.
    std::vector<ScriptMainDiagnostic> script_main_diagnostics() const;
    // Persistent hosts open a frame before executing authored code. Timers
    // created in it cannot resume until a later frame has begun.
    void begin_frame(){++frame_;frame_open_=true;}
    // should_yield is a side-effect-free pending-output check. When it is
    // true, the host must drain that output before calling step again.
    // Unfinished tasks and invocations remain queued across this handoff.
    void step(Clock::time_point deadline,std::function<void()> pump={},size_t max_resumes=256,
              std::function<void(Clock::time_point)> set_interrupt_deadline={},
              std::function<void()> progress_invocations={},
              std::function<bool()> should_yield={});
    void release_root(lua_State* root);
    size_t pending() const;
    size_t callback_capacity();
    size_t invocation_capacity();
    void clear_errors(){errors_.clear();}
    std::string owner(lua_State* thread) const;
    static int identity(lua_State* thread);
    static void set_identity(lua_State* thread,int identity);
    static bool has_capability(lua_State* thread,const std::string& capability);
    lua_State* spawn_owned(lua_State* from,int function_index,const std::string& owner,int environment_index=0);
    // Cancel all work for an owner. When deferred_main is the exact active
    // script main thread, preserve it only while ready from a VM CPU break;
    // return true while that continuation is still being preserved.
    bool cancel_owner(const std::string& owner,lua_State* deferred_main=nullptr);
    // BaseScript disable transition: preserve only its exact top-level main
    // while it is actively executing or at an internal CPU slice.
    bool disable_script_owner(const std::string& owner);
    lua_State* deferred_script_main(const std::string& owner) const;
    void set_lifecycle_cleanup(const void* owner,std::function<void(const std::string&)> cleanup,
                              std::function<bool(const std::string&)> grace_is_valid={});
    void clear_lifecycle_cleanup(const void* owner);
    lua_State* invoke_callback(lua_State* from,int reference,const std::function<int(lua_State*)>& arguments,const std::string& owner,int identity=-1,int creator_reference=LUA_NOREF);
    void release_invocation(lua_State* thread);
    struct QueueState {
        struct ReadyOwnerCount {std::string owner;size_t ready_tasks=0;};
        size_t tracked_tasks=0,pending_tasks=0;
        size_t ready_tasks=0,sleeping_tasks=0,waiting_tasks=0,running_tasks=0;
        size_t done_tasks=0,cancelled_tasks=0,ready_queue_entries=0;
        size_t unowned_ready_tasks=0,unmapped_queue_entries=0,non_ready_queue_entries=0,omitted_ready_owner_tasks=0;
        std::vector<ReadyOwnerCount> ready_owners;
        size_t callback_task_limit=0,callback_ready_queue_limit=0;
    };
    // Snapshot scheduler bookkeeping without reclaiming tasks or changing queue state.
    QueueState queue_state() const;
private:
    // Thread provenance survives ordinary coroutine.resume and scheduler
    // bookkeeping cleanup. It is inherited at VM thread creation, rather
    // than inferred from script globals or a hook's closure environment.
    struct ThreadOwner {
        SourceScheduler* scheduler;std::string owner;
        // A coroutine.resume/wrap frame retains this child on its VM stack
        // while propagating LUA_BREAK. Resume the child before that frame.
        lua_State* interrupted_child=nullptr;
        int identity=8;
    };
    static void thread_lifecycle(lua_State* parent,lua_State* thread);
    static void coroutine_interrupt(lua_State* parent,lua_Debug* debug);
    enum State{Running,Ready,Sleeping,Waiting,Done,Cancelled};
    enum class ExternalWaitKind:uint8_t{None,NetworkJob,SocketRead};
    static constexpr size_t kTaskLimit=2048;
    static constexpr size_t kForegroundTaskReserve=128;
    static constexpr size_t kCallbackTaskLimit=kTaskLimit-kForegroundTaskReserve;
    static constexpr size_t kCallbackReadyQueueLimit=128;
    struct Task {
        lua_State* thread=nullptr;int reference=LUA_NOREF;State state=Ready;int arguments=0;
        Clock::time_point due{},started{};bool elapsed=false;bool root=false;
        bool resume_across_frames=false;
        bool legacy_callback=false;
        bool script_main=false;
        bool cancel_after_resume=false;
        std::function<bool()> guard;
        int result=LUA_YIELD;
        size_t queued=0;
        std::string owner;
        Clock::duration uninterrupted_cpu{};
        Progress progress;
        bool retained=false;
        bool response_ready=false;
        Clock::duration response_priority_cpu{};
        bool report_errors=true;
        size_t sleeping_frame=0;
        bool deferred_lifecycle_stop=false;
        bool callback_created=false;
        bool callback_started=false;
        std::function<std::optional<std::string>()> external_poll;
        std::function<void()> external_discard;
        std::function<void()> external_claim;
        Clock::time_point external_next_poll{};
        std::string external_payload;
        bool external_payload_ready=false;
        bool external_delivery_in_progress=false;
        ExternalWaitKind external_kind=ExternalWaitKind::None;
        uint64_t external_sequence=0;
    };
    void set_owner(Task& task,const std::string& owner);
    lua_State* main_;
    Clock::time_point deadline_;
    Clock::time_point started_=Clock::now();
    bool persistent_;
    std::unordered_map<lua_State*,Task> tasks_;
    std::deque<Task*> ready_;
    std::unordered_map<std::string,uint64_t> owner_last_turn_;
    uint64_t scheduler_turn_=0;
    bool prefer_authored_=true;
    bool prefer_response_=true;
    size_t frame_=0;
    bool frame_open_=false;
    const void* lifecycle_cleanup_owner_=nullptr;
    std::function<void(const std::string&)> lifecycle_cleanup_;
    std::function<bool(const std::string&)> lifecycle_grace_is_valid_;
    std::vector<std::string> errors_;
    int root_status_=0;
    uint64_t next_external_sequence_=0;
    uint64_t external_poll_cursor_=0;
    Task& track(lua_State* thread);
    void resume(Task& task,lua_State* from);
    int resume_interrupted(lua_State* thread,lua_State* from,int arguments);
    void collect_finished();
    void enqueue(Task& task);
    Task* dequeue();
    static int task_api(lua_State* L);
    static int identity_api(lua_State* L);
    static int task_api_inner(lua_State* L);
    static int legacy_wait_api(lua_State* L);
    static int legacy_wait_continue(lua_State* L,int status);
    static int elapsed_time_api(lua_State* L);
    static int time_api(lua_State* L);
    static int legacy_schedule_api(lua_State* L);
    void wake_timer(Task& task,Clock::time_point now);
    void cancel_external(Task& task);
    void progress_external_waits(Clock::time_point budget_deadline);
};
