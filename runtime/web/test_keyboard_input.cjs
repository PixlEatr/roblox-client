// DOM transport tests. Real browser trusted events are tested separately.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto').webcrypto;
const source = fs.readFileSync(path.join(__dirname, 'input.js'), 'utf8');
const codes = JSON.parse(fs.readFileSync(path.join(__dirname, 'keyboard-codes.json')));
const flush = async () => { for (let i=0;i<8;i++) await new Promise(setImmediate); };
function harness() {
    const requests=[], timers=new Map(); let next=1, pause=null, failure=false;
    const held=new Set(); let attached=false,focused=false;
    class Target {
        constructor() {this.listeners=new Map();this.classList={toggle(){}};this.textContent='';}
        addEventListener(type,fn) {if(!this.listeners.has(type))this.listeners.set(type,[]);this.listeners.get(type).push(fn);}
        removeEventListener(type,fn) {this.listeners.set(type,(this.listeners.get(type)||[]).filter(x=>x!==fn));}
        emit(type,fields={}) {const event={target:this,isTrusted:true,preventDefault(){this.prevented=true;},...fields};for(const fn of this.listeners.get(type)||[])fn(event);return event;}
        setAttribute(){} contains(target){return target===this;}
        focus(){document.activeElement=this;this.emit('focus');}
        blur(){if(document.activeElement===this){document.activeElement=null;this.emit('blur');}}
    }
    const document=new Target();document.visibilityState='visible';document.activeElement=null;
    const window=new Target(),surface=new Target(),label=new Target(),editor=new Target();
    const context=vm.createContext({document,window,crypto,Uint8Array,AbortController,
        setTimeout:()=>1,clearTimeout(){},setInterval:fn=>{const id=next++;timers.set(id,fn);return id;},clearInterval:id=>timers.delete(id),
        fetch:async(url,options)=>{
            assert.equal(url,'/api/input');assert.equal(options.headers['X-Console-Key'],'fixture-key');
            const request=JSON.parse(options.body);requests.push(request);
            if(pause && pause.action===request.action){const gate=pause;pause=null;await gate.promise;}
            if(request.action==='attach'){attached=true;focused=true;}
            if(request.action==='events')for(const item of request.events){
                const identity=item.input_type||item.key_code;
                if(item.phase==='begin'){assert(!held.has(identity));held.add(identity);}
                else if(item.phase==='end'||item.phase==='cancel'){assert(held.has(identity));held.delete(identity);}
            }
            if(request.action==='release'){held.clear();focused=false;}
            if(request.action==='detach'){held.clear();attached=false;focused=false;}
            if(request.action==='events'&&failure){failure=false;throw new TypeError('fixture transport failed');}
            return {ok:true,json:async()=>({ok:true,lease:'fixture_keyboard_lease'})};
        }});
    vm.runInContext(source,context);
    const controls=context.createKeyboardControls({surface,label,key:'fixture-key',codes});
    const key=(type,code,extra={})=>document.emit(type,{target:surface,code,...extra});
    const gate=action=>{let resolve;const promise=new Promise(r=>resolve=r);pause={action,promise};return resolve;};
    return {controls,requests,held,surface,label,document,window,editor,key,gate,
        connect(){controls.setConnected(true);surface.focus();},
        fail(){failure=true;},tick(){for(const fn of timers.values())fn();},
        get attached(){return attached;},get focused(){return focused;}};
}
module.exports={harness,flush};
if(require.main===module)(async()=>{
    const a=harness();await a.controls.ready;
    a.surface.focus();a.key('keydown','KeyW');await flush();assert.equal(a.requests.length,0);
    a.connect();await flush();assert(a.controls.active);
    a.key('keydown','KeyW',{isTrusted:false});await flush();assert.equal(a.requests.length,1);
    a.key('keydown','KeyW');a.key('keydown','KeyW',{repeat:true});await flush();
    assert.deepEqual([...a.held],['W']);assert.equal(a.requests.filter(r=>r.action==='events').length,1);
    a.key('keyup','KeyW',{isTrusted:false});await flush();assert(a.held.has('W'));
    a.key('keyup','KeyW');await flush();assert.equal(a.held.size,0);
    a.document.emit('keydown',{target:a.editor,code:'KeyA'});await flush();assert.equal(a.held.size,0);
    a.key('keydown','ShiftLeft');a.key('keydown','KeyW');await flush();
    a.surface.blur();await flush();assert.equal(a.held.size,0);assert(!a.controls.active);assert(a.attached);
    a.tick();await flush();assert.equal(a.requests.at(-1).action,'heartbeat');
    a.controls.destroy();await flush();assert(!a.attached);

    const b=harness();const resume=b.gate('attach');b.connect();
    b.key('keydown','KeyW');b.key('keyup','KeyW');resume();await flush();
    const batches=b.requests.filter(r=>r.action==='events');
    assert.deepEqual(batches.map(r=>r.events),[[{phase:'begin',key_code:'W'}],[{phase:'end',key_code:'W'}]]);
    assert(b.requests.every((r,i)=>!i||r.sequence>b.requests[i-1].sequence));b.controls.destroy();await flush();

    for(const code of ['Escape','MetaLeft']){
        const h=harness();h.connect();await flush();h.key('keydown','KeyW');await flush();
        h.key('keydown',code,{metaKey:code==='MetaLeft'});await flush();
        assert(!h.controls.active);assert.equal(h.held.size,0);
        assert(!h.requests.some(r=>r.events?.some(e=>e.key_code===code)));h.controls.destroy();await flush();
    }

    const c=harness();c.connect();await flush();c.fail();c.key('keydown','KeyW');await flush();
    assert(!c.controls.active);assert.equal(c.held.size,0);assert(!c.attached);
    assert.equal(c.requests.filter(r=>r.action==='events').length,1,'uncertain presses must not be retried');
    assert.match(c.label.textContent,/fixture transport failed/);c.controls.destroy();

    const d=harness();d.connect();await flush();const finish=d.gate('events');d.key('keydown','KeyW');
    d.document.visibilityState='hidden';d.document.emit('visibilitychange');finish();await flush();
    assert(!d.controls.active);assert(!d.attached,'hidden tab must detach even if a press was in flight');
    assert.equal(d.held.size,0);d.controls.destroy();

    const e=harness();const finishAttach=e.gate('attach');e.connect();e.controls.setConnected(false);
    finishAttach();await flush();assert(!e.attached,'a late attach must not resurrect a disconnected backend');
    assert(!e.controls.active);e.controls.destroy();

    const f=harness();f.connect();await flush();f.key('keydown','KeyW');await flush();
    f.window.emit('pagehide');await flush();assert(!f.attached);assert.equal(f.held.size,0);
    f.controls.destroy();
    console.log('Keyboard DOM checks passed: trusted focus, repeats, modifiers, ordered delivery, release, heartbeat, failure, hidden/in-flight cleanup, disconnect and unload.');
})().catch(error=>{console.error(error);process.exitCode=1;});
