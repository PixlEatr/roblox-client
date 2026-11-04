// Offline DOM harness only. No Roblox connection or live input is generated.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
class Element {
  constructor() { this.children=[];this.value='';this.firstChild={textContent:''};this.disabled=false;this.listeners={}; }
  addEventListener(name, fn) {this.listeners[name]=fn;}
  append(...nodes) {this.children.push(...nodes);}
  remove() {}
  replaceChildren() {this.children=[];}
}
const elements=new Map(), posts=[], displays=[];
const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
get('chat-channel').value='RBXGeneral';
const event=(trusted=true, extra={})=>({isTrusted:trusted,preventDefault(){},...extra});
const context=vm.createContext({window:{}});
const input=(text,trusted=true)=>{get('chat-text').value=text;get('chat-text').listeners.input(event(trusted));};
(async()=>{
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../ui/chat.js'),'utf8'),context);
  context.window.createChatControls({input:get('chat-text'),button:get('chat-send'),form:get('chat-form'),channel:get('chat-channel'),
    submit:async data=>{posts.push(JSON.parse(JSON.stringify(data)));return {ok:true};}});
  input('chat test',false);
  await get('chat-send').listeners.click(event());
  assert.equal(posts.length,0);
  input('chat test');
  await get('chat-send').listeners.click(event(false));
  assert.equal(posts.length,0);
  get('chat-text').value='changed without input';
  await get('chat-send').listeners.click(event());
  assert.equal(posts.length,0);
  input('chat test');
  get('chat-form').listeners.submit(event());
  assert.equal(posts.length,0,'programmatic requestSubmit must not post');
  await get('chat-send').listeners.click(event());
  assert.deepEqual(posts,[{text:'chat test',channel:'RBXGeneral'}]);
  await get('chat-send').listeners.click(event());
  assert.equal(posts.length,1,'no automatic repeat after a single input edit');
  input('next');
  get('chat-text').listeners.keydown(event(true,{key:'Enter',isComposing:true}));
  assert.equal(posts.length,1,'IME composition is not submission');
  input('x'.repeat(201));
  await get('chat-send').listeners.click(event());
  assert.equal(posts.length,1);
  input('global test');
  get('chat-channel').value='RBXGlobal';
  await get('chat-send').listeners.click(event());
  assert.deepEqual(posts[1],{text:'global test',channel:'RBXGlobal'});
  console.log('Typed chat UI checks passed: route selection, input provenance, explicit send, no retry, IME, length.');
})().catch(error=>{console.error(error);process.exitCode=1;});
