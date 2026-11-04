const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor() {this.children=[];this.value='';this.firstChild={textContent:''};this.disabled=false;this.textContent='';}
  append(...items) {this.children.push(...items);}
  remove() {}
  replaceChildren() {this.children=[];}
  addEventListener() {}
}
const elements = new Map();
const get = id => {if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
const executions = [], streams = [], displays = [];
const context = vm.createContext({
  document: {getElementById:id=>get(id.replace(/^script-/,"")),querySelector:()=>({content:'test-key'}),
             createElement:()=>new Element(),addEventListener(){}},
  window: {innerWidth:1280,innerHeight:720,addEventListener(){}},
  localStorage:{getItem:()=>null,setItem(){}},
  navigator:{platform:'Mac'},performance:{now:()=>100},AbortController,
  setTimeout:()=>1,clearTimeout(){},setInterval(){},
  fetch:async(url, options)=>({ok:true,json:async()=>{
    if(url==='/api/runtime/status')return {connected:true};
    if(url==='/api/display') {displays.push(JSON.parse(options.body));return {ok:true};}
    assert.equal(options.headers['X-Console-Key'],'test-key');
    if(url==='/api/runtime/execute') {const item=executions.shift();return typeof item==='function'?item():item;}
    assert.equal(url,'/api/runtime/events');return streams.shift();
  }})
});
const texts = () => get('output').children.map(item=>item.children[1]?.textContent||'');

(async()=>{
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../ui/script-editor.js'),'utf8'),context);
  await new Promise(setImmediate);
  Object.assign(context,context.window.RoveScripts);
  assert.deepEqual(displays,[]);
  executions.push({ok:true,state:'running',completed:false,job_id:7,runtime_sequence:1,
                   values:[],output:'',completed_jobs:[],pending_tasks:1});
  await context.execute();
  assert.equal(get('result-state').textContent,'Running');
  assert(!texts().some(text=>/completed/i.test(text)));
  const failed={ok:true,cursor:2,events:[{sequence:2,ok:true,output:'',pending_tasks:0,
      chat_outcomes:[{error:'chat outcome: response_timeout'}],
      completed_jobs:[{job_id:7,ok:false,completed:true,state:'failed',values:[],
                       error:'SendAsync failed: chat outcome: response_timeout'}]}]};
  streams.push(failed);await context.pollEvents();
  assert.equal(get('result-state').textContent,'Error');
  assert(texts().includes('SendAsync failed: chat outcome: response_timeout'));
  const count=texts().length;
  streams.push(failed);await context.pollEvents();assert.equal(texts().length,count);
  executions.push({ok:true,state:'running',job_id:8,runtime_sequence:3,completed:false,values:[]});
  await context.execute();
  streams.push({ok:true,cursor:4,events:[{sequence:4,ok:true,output:'after reply\n',pending_tasks:0,
      chat_outcomes:[{status:'Success'}],completed_jobs:[{job_id:8,ok:true,completed:true,
                                                       state:'completed',values:['filtered text']}]}]});
  await context.pollEvents();
  assert.equal(get('result-state').textContent,'Complete');
  assert.equal(get('result-state').className,'');
  assert(texts().includes('after reply'));
  assert(texts().includes('Chat: Success'));
  assert(texts().some(text=>text.includes('filtered text')));
  const completedBefore=texts().filter(text=>/completed/i.test(text)).length;
  context.renderEvent({sequence:5,ok:true,output:'background print\n',pending_tasks:1});
  assert.equal(texts().filter(text=>/completed/i.test(text)).length,completedBefore);
  executions.push(()=>{
    context.renderEvent({sequence:7,ok:true,pending_tasks:0,
        completed_jobs:[{job_id:9,ok:false,completed:true,error:'late server rejection',values:[]}]});
    return {ok:true,state:'running',job_id:9,runtime_sequence:6,completed:false,pending_tasks:1,values:[]};
  });
  await context.execute();
  assert.equal(get('result-state').textContent,'Error');
  assert(!texts().some(text=>text==='Script is running (job 9).'));
  console.log('Browser async result checks passed: pending, failure, success, deduplication, background output.');
})().catch(error=>{console.error(error);process.exitCode=1;});
