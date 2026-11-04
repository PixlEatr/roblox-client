const assert=require('node:assert/strict');
const {harness,flush}=require('./test_keyboard_input.cjs');
function point(h,type,x,y,extra={}) {
    return h.document.emit(type,{target:h.surface,pointerType:'mouse',clientX:x,clientY:y,...extra});
}
const events=h=>h.requests.filter(r=>r.action==='events').flatMap(r=>r.events);
(async()=>{
 const a=harness();a.controls.setConnected(true);
 a.surface.emit('pointerdown',{pointerType:'mouse',button:0,clientX:10,clientY:20});await flush();
 point(a,'pointerup',10,20,{button:0});await flush();
 assert.equal(events(a).length,0,'activating the control surface must not click through');
 for(const [button,name] of [[0,'MouseButton1'],[1,'MouseButton3'],[2,'MouseButton2']]){
  a.surface.emit('pointerdown',{pointerType:'mouse',button,clientX:80,clientY:90});await flush();
  assert(a.held.has(name));point(a,'pointerup',125,140,{button,target:a.editor});await flush();assert(!a.held.has(name));
  const own=events(a).filter(e=>e.input_type===name);assert.deepEqual(own.map(e=>e.phase),['begin','end']);
  assert.deepEqual(own[0].position,[80,90,0]);assert.deepEqual(own[1].position,[125,140,0]);
 }
 assert.equal(a.surface.emit('contextmenu').prevented,true);
 a.surface.blur();await flush();assert(!a.surface.emit('contextmenu').prevented);
 a.controls.destroy();await flush();

 const b=harness();b.connect();await flush();const finish=b.gate('events');
 point(b,'pointermove',10,20);
 for(let i=1;i<=100;i++)point(b,'pointermove',10+i,20+2*i);
 finish();await flush();
 const moves=events(b).filter(e=>e.input_type==='MouseMovement');
 assert.equal(moves.length,2);assert.deepEqual(moves[0].delta,[0,0,0]);
 assert.deepEqual(moves[1].delta,[100,200,0]);assert.deepEqual(moves[1].position,[110,220,0]);
 b.surface.emit('wheel',{clientX:110,clientY:220,deltaY:-120});await flush();
 b.surface.emit('wheel',{clientX:110,clientY:220,deltaY:120});await flush();
 assert.deepEqual(events(b).filter(e=>e.input_type==='MouseWheel').map(e=>e.position[2]),[1,-1]);
 const before=events(b).length;
 b.surface.emit('wheel',{clientX:110,clientY:220,deltaY:1,ctrlKey:true});
 point(b,'pointermove',900,800,{target:b.editor});
 point(b,'pointermove',100,100,{pointerType:'touch'});
 point(b,'pointermove',100,100,{isTrusted:false});await flush();assert.equal(events(b).length,before);
 b.controls.destroy();await flush();

 const c=harness();c.connect();await flush();
 c.surface.emit('pointerdown',{pointerType:'mouse',button:2,clientX:10,clientY:20});await flush();
 point(c,'pointermove',40,60,{target:c.editor,buttons:2});await flush();
 assert.deepEqual(events(c).at(-1).delta,[30,40,0]);
 c.window.emit('blur');await flush();assert.equal(c.held.size,0);assert(!c.controls.active);
 c.controls.destroy();await flush();

 const d=harness();d.connect();await flush();
 for(const button of [0,2])d.surface.emit('pointerdown',{pointerType:'mouse',button,clientX:10,clientY:20});
 await flush();point(d,'pointercancel',10,20,{button:-1});await flush();
 assert.equal(d.held.size,0,'pointer cancellation without a button must cancel the full pointer');
 d.controls.destroy();await flush();

 const e=harness();e.connect();await flush();
 e.surface.emit('pointerdown',{pointerType:'mouse',button:2,clientX:10,clientY:20});await flush();
 point(e,'pointermove',20,30,{buttons:0});await flush();
 assert(!e.held.has('MouseButton2'),'reported button mask must recover a missed pointerup');
 e.controls.destroy();await flush();
 console.log('Mouse DOM checks passed: click activation, three buttons, outside releases, drag, motion coalescing, wheel, trust, focus and lost-pointer cleanup.');
})().catch(error=>{console.error(error);process.exitCode=1;});
