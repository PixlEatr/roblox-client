#pragma once
#include "lstate.h"
#include "lgc.h"
#include <algorithm>
#include <atomic>
#include <cstddef>
#include <cstdlib>
#ifndef _WIN32
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
// Opt-in, bounded private diagnostics for caught Luau errors. This observes
// the pre-unwind stack; it never changes the error, stack, or return values.
#include "ltable.h"
#include <unordered_set>
namespace protected_error_trace {
using J=nlohmann::json;
struct Budget {int left=10000;};
inline J value(lua_State* L,const TValue* v,int depth,Budget& budget) {
 if(--budget.left<0)return {{"truncated",true}};
 if(ttisnil(v))return nullptr;
 if(ttisboolean(v))return bvalue(v)!=0;
 if(ttisnumber(v))return nvalue(v);
 if(ttisstring(v)){auto* s=tsvalue(v);return J{{"type","string"},{"length",s->len},{"text",std::string(getstr(s),std::min(size_t(s->len),size_t(400)))}};}
 if(ttisupval(v))return value(L,upvalue(v)->v,depth,budget);
 if(ttisfunction(v)) {
  auto* c=clvalue(v);J out{{"type","function"},{"native",bool(c->isC)}};
  if(!c->isC){out["source"]=c->l.p->source?getstr(c->l.p->source):"";out["name"]=c->l.p->debugname?getstr(c->l.p->debugname):"";}
  return out;
 }
 if(ttistable(v)) {
  auto* t=hvalue(v);J out{{"type","table"},{"id",uintptr_t(t)},{"array_size",t->sizearray},{"hash_size",sizenode(t)}};
  if(depth<=0)return out;
  out["entries"]=J::array();int count=0;
  for(int i=0;i<t->sizearray&&count<256&&budget.left>0;i++)if(!ttisnil(&t->array[i])){++count;out["entries"].push_back({{"key",i+1},{"value",value(L,&t->array[i],depth-1,budget)}});}
  for(int i=0;i<sizenode(t)&&count<256&&budget.left>0;i++)if(!ttisnil(gval(gnode(t,i)))){++count;TValue k;getnodekey(L,&k,gnode(t,i));out["entries"].push_back({{"key",value(L,&k,0,budget)},{"value",value(L,gval(gnode(t,i)),depth-1,budget)}});}
  return out;
 }
 return {{"type_tag",ttype(v)}};
}
inline void capture(lua_State* L) noexcept {
 const char* directory=std::getenv("RCR_ERROR_TRACE_DIRECTORY");if(!directory||!*directory)return;
 static std::atomic<unsigned> count{0};if(count.load(std::memory_order_relaxed)>=8||L->top<=L->base||!ttisstring(L->top-1))return;
 auto* error=tsvalue(L->top-1);std::string message(getstr(error),error->len);
 const char* match=std::getenv("RCR_ERROR_TRACE_MATCH");if(match&&message.find(match)==std::string::npos)return;
 int dir=-1,fd=-1;
 try{
  dir=open(directory,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);struct stat info{};
  if(dir<0||fstat(dir,&info)||info.st_uid!=getuid()||(info.st_mode&0077)){if(dir>=0)close(dir);return;}
  const auto sequence=count.fetch_add(1,std::memory_order_relaxed)+1;
  if(sequence>8){close(dir);return;}
  J out{{"error",message},{"process_id",getpid()},{"frames",J::array()}};Budget budget;
  const int frames=int(std::min(std::ptrdiff_t(6),L->ci-L->base_ci));
  for(int depth=0;depth<frames&&budget.left>0;depth++) {
   auto* frame=L->ci-depth;J row{{"depth",depth}};lua_Debug ar{};
   if(lua_getinfo(L,depth,"sln",&ar)){row["source"]=ar.short_src?ar.short_src:"";row["line"]=ar.currentline;row["name"]=ar.name?ar.name:"";}
   if(isLfunction(frame->func)) {
    auto* c=clvalue(frame->func);row["slots"]=J::object();
    for(int i=0;i<std::min(200,int(frame->top-frame->base))&&budget.left>0;i++)row["slots"][std::to_string(i+1)]=value(L,frame->base+i,i==15?2:0,budget);
    row["upvalues"]=J::object();
    for(int i=0;i<c->nupvalues&&budget.left>0;i++)row["upvalues"][std::to_string(i+1)]=value(L,&c->l.uprefs[i],i==11?1:0,budget);
    if(frame->top-frame->base>3&&ttisnumber(frame->base+3)&&c->nupvalues>5){
     auto* v=&c->l.uprefs[5];if(ttisupval(v))v=upvalue(v)->v;
     if(ttistable(v)){
      int pc=int(nvalue(frame->base+3));auto* t=hvalue(v);
      if(pc>=1&&pc<=t->sizearray){row["instructions"]=J::object();for(int i=std::max(1,pc-16);i<=std::min(t->sizearray,pc+16)&&budget.left>0;i++)row["instructions"][std::to_string(i)]=value(L,luaH_getnum(t,i),1,budget);}
     }
    }
   }
   out["frames"].push_back(std::move(row));
  }
  auto data=out.dump(2,' ',false,J::error_handler_t::replace);if(data.size()>1024*1024){close(dir);return;}
  auto name=std::to_string(getpid())+"-"+std::to_string(sequence)+".json";fd=openat(dir,name.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
  if(fd>=0){size_t offset=0;while(offset<data.size()){auto n=write(fd,data.data()+offset,data.size()-offset);if(n<=0)break;offset+=size_t(n);}close(fd);fd=-1;}
  close(dir);
 }catch(...){if(fd>=0)close(fd);if(dir>=0)close(dir);}
}
}

inline void install_protected_error_trace(lua_State* L) {
 const char* directory=std::getenv("RCR_ERROR_TRACE_DIRECTORY");
 const char* match=std::getenv("RCR_ERROR_TRACE_MATCH");
 auto* callbacks=lua_callbacks(L);
 if(directory&&*directory&&match&&*match&&!callbacks->debugprotectederror)
  callbacks->debugprotectederror=protected_error_trace::capture;
}
#else
inline void install_protected_error_trace(lua_State*) {}
#endif
