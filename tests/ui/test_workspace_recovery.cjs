const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');

class Element {
  constructor() {
    this.hidden=false;this.value='follow';this.children=[];this.style={};this.dataset={};
    this.clientWidth=900;this.clientHeight=600;this.textContent='';
    this.classList={add(){},remove(){},toggle(){}};
  }
  addEventListener(){} setAttribute(){} append(...items){this.children.push(...items);}
  remove(){} replaceChildren(...items){this.children=items;} querySelector(){return new Element();}
}
const elements=new Map(),events=new Map(),timers=[],frames=[],requests=[];
const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
let now=10000,online=true;
const frame={parts:[{id:'part'}],gui:[],players:[],skippedParts:0};
const timeout=Object.assign(new Error('signal timed out'),{name:'TimeoutError'});
const replies=[{ok:true,pending:true},{ok:true,frame,sample:1},timeout,
               {ok:true,frame:{...frame,parts:[{id:'new-part'}]},sample:2}];
const context=vm.createContext({
  document:{getElementById:get,querySelector:()=>({content:'test-key'}),createElement:()=>new Element(),
            addEventListener(){},hidden:false,visibilityState:'visible',activeElement:null},
  window:{addEventListener:(name,fn)=>events.set(name,fn),dispatchEvent(){}},
  location:{search:''},crypto:webcrypto,URLSearchParams,AbortSignal,Event,
  performance:{now:()=>now},Date:{now:()=>now},
  setTimeout:fn=>{timers.push(fn);return timers.length;},setInterval(){},
  createKeyboardControls:()=>({setConnected(){},destroy(){}}),createChatControls(){},
  RoveWorld:{mount:()=>({frame:f=>frames.push(f),setMode(){},dispose(){},backend:'webgl',fps:60,
    stats:{ready:0,pending:0,failed:0,meshBounds:0}})},
  fetch:async url=>{
    requests.push(url);
    if(url==='/api/runtime/status')return {ok:true,json:async()=>({connected:online,session:online?'one':null})};
    if(url==='/api/runtime/display')return new Promise(()=>{});
    assert.equal(url,'/api/runtime/render');
    const reply=replies.shift();assert(reply,'Unexpected frame request');
    if(reply instanceof Error)throw reply;
    return {ok:true,json:async()=>reply};
  }
});
const flush=()=>new Promise(setImmediate);
const next=async()=>{now+=200;const tick=timers.shift();assert(tick);await tick();await flush();};

(async()=>{
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../ui/font-loader.js'),'utf8'),context);
  context.RoveFonts=context.window.RoveFonts;
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../ui/text-layout.js'),'utf8'),context);
  context.RoveText=context.window.RoveText;
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../ui/gui-scroll.js'),'utf8'),context);
  context.RoveGuiScroll=context.window.RoveGuiScroll;
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../ui/workspace.js'),'utf8'),context);
  events.get('rove:workspace')();await flush();
  assert.equal(get('game-connection').textContent,'Preparing game view');
  assert.equal(requests.filter(p=>p==='/api/runtime/display').length,1);
  assert(requests.includes('/api/runtime/render'),'A pending resize must not block frames');
  await next();
  assert.equal(get('game-waiting').hidden,true);
  assert.equal(frames.at(-1).parts[0].id,'part');
  const count=frames.length;
  await next();
  assert.equal(frames.length,count,'A timeout must preserve the visible frame');
  assert.equal(get('game-waiting').hidden,true);
  assert(!get('game-waiting-text').textContent.includes('signal timed out'));
  await next();assert.equal(frames.at(-1).parts[0].id,'new-part');
  assert.equal(get('game-connection').textContent,'● Live · same session');
  online=false;now+=2000;await next();
  assert.equal(frames.at(-1).parts.length,0,'A disconnected session must clear the old world');
  assert.equal(get('game-waiting').hidden,false);
  console.log('Workspace recovery: slow resize, pending frame, timeout, recovery and disconnect passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
