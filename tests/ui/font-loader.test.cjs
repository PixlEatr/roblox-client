const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../ui/font-loader.js'),'utf8');
const font=(family='BuilderSans',weight=400,style=0)=>({family:'rbxasset://fonts/families/'+family+'.json',weight,style});
const response=()=>({ok:true,mime:'font/otf',data:Buffer.from('test-font').toString('base64'),weight:400,style:0});
const element=()=>({style:{},dataset:{}}),flush=()=>new Promise(setImmediate);
function fixture(load){
    let now=1000;const faces=new Set(),made=[];
    class FontFace{constructor(name,bytes,options){this.name=name;this.bytes=bytes;this.options=options;made.push(this);}async load(){return this;}}
    const context=vm.createContext({window:{},document:{fonts:{add:f=>faces.add(f),delete:f=>faces.delete(f)}},FontFace,atob,Uint8Array,Date:{now:()=>now}});
    vm.runInContext(source,context);const cache=new context.window.RoveFonts.FontCache(load);
    return {cache,faces,made,advance:n=>now+=n};
}
test('loaded font bytes and actual descriptors become a DOM font family',async()=>{
    let calls=0;const f=fixture(async(kind,ref,w,s)=>{calls++;assert.equal(kind,'font');assert.equal(w,700);assert.equal(s,1);return response();});
    const e=element(),d=font('BuilderSans',700,1);f.cache.apply(e,d);assert.equal(e.dataset.fontStatus,'pending');
    await flush();f.cache.apply(e,d);
    assert.equal(calls,1);assert.equal(e.dataset.fontStatus,'ready');assert.match(e.style.fontFamily,/RoveFont/);
    assert.equal(e.style.fontWeight,'700');assert.equal(e.style.fontStyle,'italic');
    assert.equal(f.made[0].options.weight,'400');assert.equal(f.made[0].options.style,'normal');assert.equal(f.faces.size,1);
    f.cache.apply(element(),d);await flush();assert.equal(calls,1);
});
test('invalid or missing choices clear a previous font without requesting assets',()=>{
    const f=fixture(()=>{throw Error('No request expected');}),e=element();e.style.fontFamily='OldFont';
    for(const d of [undefined,{},font('BuilderSans',450),font('BuilderSans',400,true)]){
        f.cache.apply(e,d);assert.equal(e.dataset.fontStatus,'unavailable');assert.equal(e.style.fontFamily,'Arial, sans-serif');
    }
});
test('only two loads run concurrently and invisible fonts are released',async()=>{
    const waiting=[];const f=fixture(()=>new Promise(resolve=>waiting.push(resolve)));
    const fonts=[font('A'),font('B'),font('C')];for(const d of fonts)f.cache.apply(element(),d);
    assert.equal(waiting.length,2);assert.equal(f.cache.active,2);
    waiting[0](response());await flush();assert.equal(waiting.length,3);
    waiting[1](response());waiting[2](response());await flush();assert.equal(f.faces.size,3);assert(f.cache.bytes>0);
    f.cache.observe([{fontFace:fonts[1]}]);assert.equal(f.faces.size,1);assert.equal(f.cache.entries.size,1);
    f.cache.clear();assert.equal(f.faces.size,0);assert.equal(f.cache.bytes,0);
});
test('a response from a previous session cannot install its font',async()=>{
    let complete;const f=fixture(()=>new Promise(resolve=>complete=resolve));f.cache.apply(element(),font());
    f.cache.clear();complete(response());await flush();assert.equal(f.faces.size,0);assert.equal(f.cache.entries.size,0);
    f.cache.dispose();f.cache.apply(element(),font());assert.equal(f.cache.entries.size,0);
});
test('failed requests retry with a bounded delay then stop',async()=>{
    let calls=0;const f=fixture(async()=>{calls++;throw Error('temporary failure');}),e=element(),d=font();
    f.cache.apply(e,d);await flush();f.cache.apply(e,d);assert.equal(e.dataset.fontStatus,'failed');assert.equal(calls,1);
    for(const elapsed of [1000,2000]){f.advance(elapsed);f.cache.apply(e,d);await flush();}
    f.advance(100000);f.cache.apply(e,d);await flush();assert.equal(calls,3);assert.equal(e.dataset.fontStatus,'failed');
});
test('malformed responses never enter the browser font set',async()=>{
    for(const bad of [{}, {...response(),mime:'image/png'}, {...response(),style:3}, {...response(),weight:450}]){
        const f=fixture(async()=>bad),e=element();f.cache.apply(e,font());await flush();f.cache.apply(e,font());
        assert.equal(e.dataset.fontStatus,'failed');assert.equal(f.faces.size,0);
    }
});
