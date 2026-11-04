const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const context=vm.createContext({window:{}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../../ui/gui-scroll.js'),'utf8'),context);
const {wheelDelta,create}=context.window.RoveGuiScroll;
const event=(extra={})=>({isTrusted:true,clientX:150,clientY:90,deltaX:0,deltaY:1,deltaMode:0,preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;},...extra});
const plain=x=>JSON.parse(JSON.stringify(x));
const tick=()=>new Promise(setImmediate);
function harness(send){
    let listener;const surface={getBoundingClientRect:()=>({left:100,top:40}),addEventListener(n,fn,options){assert.equal(n,'wheel');assert.equal(options.passive,false);listener=fn;},removeEventListener(){listener=null;}};
    const element={parentElement:surface,classList:{toggle(){}}},errors=[];
    let enabled=true;
    const scroll=create({surface,send,enabled:()=>enabled,onError:e=>errors.push(e.message)});
    scroll.update(element,{id:'child',scrollTarget:'inner',scrollViewport:[200,100]});
    return {scroll,errors,element,setEnabled:v=>{enabled=v;},wheel(extra={}){const e=event({target:element,...extra});listener?.(e);return e;}};
}
test('normalizes pixels, lines, pages, horizontal trackpad and shift wheel',()=>{
    assert.deepEqual(plain(wheelDelta(event({deltaX:1.5,deltaY:-2.25}),[200,100])),[1.5,-2.25]);
    assert.deepEqual(plain(wheelDelta(event({deltaMode:1,deltaY:3}),[200,100])),[0,48]);
    assert.deepEqual(plain(wheelDelta(event({deltaMode:2,deltaX:1,deltaY:-1}),[200,100])),[200,-100]);
    assert.deepEqual(plain(wheelDelta(event({shiftKey:true,deltaY:30}),[200,100])),[30,0]);
    assert.deepEqual(plain(wheelDelta(event({deltaY:99999}),[200,100])),[0,4096]);
    for(const extra of [{isTrusted:false},{ctrlKey:true},{metaKey:true},{deltaY:NaN},{deltaX:Infinity},{deltaMode:4},{deltaY:0}])
        assert.equal(wheelDelta(event(extra),[200,100]),null);
});
test('handles unfocused canvas input, stops world wheel, and serializes bursts',async()=>{
    const calls=[],resolves=[];const h=harness(action=>{calls.push(plain(action));return new Promise(resolve=>resolves.push(resolve));});
    const e=h.wheel({deltaY:20});assert(e.prevented&&e.stopped);
    h.wheel({deltaY:30});h.wheel({deltaY:40});
    assert.equal(calls.length,1);
    resolves.shift()();await tick();assert.equal(calls.length,2);
    assert.deepEqual(calls[1],{id:'child',kind:'wheel',delta:[0,70],position:[50,50],steps:-2});
    resolves.shift()();await tick();h.scroll.destroy();
});
test('opposite direction and target changes retain request ordering',async()=>{
    const calls=[],resolves=[];const h=harness(a=>{calls.push(plain(a));return new Promise(r=>resolves.push(r));});
    h.wheel({deltaY:10});h.wheel({deltaY:50});h.wheel({deltaY:-30});
    h.scroll.update(h.element,{id:'other-child',scrollTarget:'outer',scrollViewport:[200,100]});h.wheel({deltaY:20});
    for(let i=0;i<4;i++){resolves.shift()();await tick();}
    assert.deepEqual(calls.map(a=>[a.id,a.delta[1]]),[['child',10],['child',50],['child',-30],['other-child',20]]);
});
test('failure drops queued deltas without retrying an uncertain action',async()=>{
    const calls=[];let reject;const h=harness(a=>{calls.push(a);return new Promise((r,j)=>{reject=j;});});
    h.wheel();h.wheel();reject(new Error('disconnected'));await tick();
    assert.equal(calls.length,1);assert.deepEqual(h.errors,['disconnected']);
});
test('session reset drops queued inputs and old element metadata',async()=>{
    const calls=[];let resolve;const h=harness(a=>{calls.push(a);return new Promise(r=>{resolve=r;});});
    h.wheel();h.wheel();h.scroll.reset();
    const e=h.wheel();assert(!e.prevented);resolve();await tick();assert.equal(calls.length,1);
    h.setEnabled(false);h.scroll.update(h.element,{id:'new-child',scrollTarget:'new',scrollViewport:[200,100]});
    assert(!h.wheel().prevented);h.scroll.destroy();
});
test('unhandled scroll and browser zoom pass through without requests',()=>{
    const h=harness(()=>assert.fail('unexpected request'));
    assert(!h.wheel({ctrlKey:true}).prevented);
    h.scroll.update(h.element,{});assert(!h.wheel().prevented);h.scroll.destroy();
});
test('plain wheel listeners receive position and direction without a scroller',async()=>{
    const calls=[];const h=harness(async a=>{calls.push(plain(a));});
    h.scroll.update(h.element,{id:'plain',wheelTarget:true,box:[0,0,200,100,0]});
    h.wheel({clientX:190,clientY:130,deltaY:-40});await tick();
    assert.deepEqual(calls,[{id:'plain',kind:'wheel',delta:[0,-40],position:[90,90],steps:1}]);
    h.wheel({clientX:NaN});assert.equal(calls.length,1);h.scroll.destroy();
});
test('cursor movement cannot collapse two different wheel event positions',async()=>{
    const calls=[],resolves=[];const h=harness(a=>{calls.push(plain(a));return new Promise(r=>resolves.push(r));});
    h.wheel();h.wheel({clientX:160});h.wheel({clientX:170});
    for(let i=0;i<3;i++){resolves.shift()();await tick();}
    assert.deepEqual(calls.map(a=>a.position),[[50,50],[60,50],[70,50]]);h.scroll.destroy();
});
