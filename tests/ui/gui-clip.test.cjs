const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const document={activeElement:null},context=vm.createContext({window:{},document});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../../ui/gui-clip.js'),'utf8'),context);
const {clipping,apply}=context.window.RoveGuiClip;
test('ancestor bounds become a local clip without changing child dimensions',()=>{
    const result=clipping([100,90,80,40,0],[30,40,100,80]);
    assert.equal(result.path,'polygon(-70px -50px, 30px -50px, 30px 30px, -70px 30px)');
    assert.equal(result.visible,true);
});
test('empty intersections and edge contact are invisible',()=>{
    for(const clip of [[0,0,0,0],[200,200,20,20],[180,90,20,40],[0,0,NaN,10]])
        assert.equal(clipping([100,90,80,40,0],clip).visible,false);
    assert.equal(clipping([100,90,80,40,0],[179.5,90,20,40]).visible,true);
});
test('rotated child uses inverse transform for the browser hit-test polygon',()=>{
    const result=clipping([0,0,100,100,90],[0,0,50,100]);
    const numbers=result.path.match(/-?[\d.]+(?:e[+-]?\d+)?(?=px)/g).map(Number);
    const expected=[0,100,0,50,100,50,100,100];
    numbers.forEach((n,i)=>assert(Math.abs(n-expected[i])<1e-9));
    assert.equal(result.visible,true);
    assert.equal(clipping([0,0,100,100,45],[-20,-20,5,5]).visible,false);
});
test('fully hidden controls lose focus and recover when clipping is removed',()=>{
    let blurred=0;const element={style:{},blur(){blurred++;document.activeElement=null;}};
    document.activeElement=element;
    apply(element,{box:[0,0,100,100,0],clip:[200,200,10,10]});
    assert.equal(element.inert,true);assert.equal(element.style.visibility,'hidden');assert.equal(blurred,1);
    apply(element,{box:[0,0,100,100,0]});
    assert.equal(element.inert,false);assert.equal(element.style.visibility,'');assert.equal(element.style.clipPath,'');
});
