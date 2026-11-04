const {test}=require('node:test'), assert=require('node:assert/strict');
const vm=require('node:vm'), fs=require('node:fs'), path=require('node:path');
const context=vm.createContext({window:{}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../../ui/gui-image.js'),'utf8'),context);
const {layout}=context.window.RoveImage;
const atlas={box:[0,0,120,120,0],imageRect:{$vector2:[40,20]},imageOffset:{$vector2:[10,20]}};
const resolve=(frame)=>JSON.parse(JSON.stringify(layout(frame,100,80)));
class Element{
    constructor(tag){this.tag=tag;this.attributes={};this.children=[];this.style={};this.events={};}
    setAttribute(name,value){this.attributes[name]=value;}
    append(...children){
        for(const child of children){child.parent=this;this.children.push(child);}
    }
    replaceChildren(...children){
        for(const child of this.children)child.parent=null;
        this.children=[];
        this.append(...children);
    }
    remove(){
        if(!this.parent)return;
        this.parent.children.splice(this.parent.children.indexOf(this),1);
        this.parent=null;
    }
    addEventListener(name,callback){this.events[name]=callback;}
}
test('image modes size and center the selected source rectangle',()=>{
    assert.deepEqual(resolve(atlas).regions,[{source:[10,20,40,20],target:[0,0,120,120]}]);
    assert.deepEqual(resolve({...atlas,imageMode:3}).regions[0].target,[0,30,120,60]);
    assert.deepEqual(resolve({...atlas,imageMode:4}).regions[0].target,[-60,0,240,120]);
    const tiled=resolve({...atlas,imageMode:2,imageTile:[35,20]});
    assert.deepEqual(tiled,{tile:[35,20],regions:[{source:[10,20,40,20],target:[0,0,35,20]}]});
    assert.deepEqual(resolve({...atlas,imageRect:{$vector2:[40,0]}}).regions[0].source,[0,0,100,80]);
});
test('nine-slice scaling keeps the corners and fits a box smaller than the borders',()=>{
    const frame={...atlas,imageMode:1,imageSlice:{$rect:[{$vector2:[5,4]},{$vector2:[30,16]}]},imageSliceScale:2};
    const {regions}=resolve(frame);
    assert.equal(regions.length,9);
    assert.deepEqual(regions[0],{source:[10,20,5,4],target:[0,0,10,8]});
    assert.deepEqual(regions[4],{source:[15,24,25,12],target:[10,8,90,104]});
    const small=resolve({...frame,box:[0,0,20,10,0]}).regions;
    assert.equal(small.length,4);
    assert(small.every(({target:[x,y,w,h]})=>x>=0&&y>=0&&x+w<=20&&y+h<=10));
});
test('a gradient multiplies the assembled image and disabling it restores the tint filter',()=>{
    context.document={
        createElementNS:(_,tag)=>new Element(tag),
        createElement:tag=>Object.assign(new Element(tag),{naturalWidth:100,naturalHeight:80}),
    };
    const entry={element:new Element('owner')};
    const gradient={type:2};
    const frame={...atlas,image:'rbxassetid://1',imageMode:2,imageTile:[20,20],
        imageColor:{$color3:[1,.75,1]},imageTransparency:.25,backgroundGradient:gradient};
    let paints=0;
    const gradientFilter=(layer,paint,width,height,color)=>{
        assert.equal(layer.tag,'svg');
        assert.equal(paint,gradient);
        assert.equal(width,120);
        assert.equal(height,120);
        assert.equal(color.$color3[1],.75);
        paints++;
        return 'url(#gradient)';
    };
    const {apply}=context.window.RoveImage;
    const find=(tag,element=entry.element)=>element.tag===tag?element:
        element.children.map(child=>find(tag,child)).find(Boolean);
    apply(entry,frame,gradientFilter);
    assert.equal(paints,1);
    assert.equal(find('svg').style.opacity,'0.75');
    assert.equal(find('g').attributes.filter,'url(#gradient)');
    assert.equal(find('pattern').attributes.width,'20');
    apply(entry,frame,gradientFilter);
    assert.equal(paints,1,'unchanged frames reuse the assembled image');
    apply(entry,{...frame,backgroundGradient:null},gradientFilter);
    assert.match(find('g').attributes.filter,/-tint/);
    assert.match(find('feColorMatrix').attributes.values,/0\.75/);
});
test('browser reports a decoded image once and drops stale loads after token changes',()=>{
    const loaders=[];
    context.document={
        createElementNS:(_,tag)=>new Element(tag),
        createElement:tag=>{
            const loader=new Element(tag);
            loaders.push(loader);
            return loader;
        },
    };
    const entry={element:new Element('owner')},receipts=[];
    const send=action=>{receipts.push(action);return Promise.resolve(true);};
    const {apply}=context.window.RoveImage;
    const original={...atlas,id:'icon',image:'rbxassetid://1',imageLoadToken:'old'};
    const replacement={...original,imageLoadToken:'new'};
    apply(entry,original,null,send);
    apply(entry,replacement,null,send);
    assert.deepEqual(receipts,[]);
    for(const loader of loaders)Object.assign(loader,{naturalWidth:100,naturalHeight:80});
    loaders[0].events.load();
    assert.deepEqual(receipts,[]);
    loaders[1].events.load();
    apply(entry,replacement,null,send);
    assert.deepEqual(receipts.map(receipt=>receipt.token),['new']);
});
test('alternate image loads and replacement leave the base image and receipt intact',()=>{
    const loaders=[];
    context.document={
        createElementNS:(_,tag)=>new Element(tag),
        createElement:tag=>{
            const element=new Element(tag);
            if(tag==='img')loaders.push(element);
            return element;
        },
    };
    const entry={element:new Element('owner')},receipts=[];
    const send=action=>{receipts.push(action.token);return Promise.resolve(true);};
    const {apply}=context.window.RoveImage;
    const frame={...atlas,id:'icon',image:'rbxassetid://1',imageLoadToken:'base',
        imageHover:'rbxassetid://2',imagePressed:'rbxassetid://3'};
    const load=loader=>{
        Object.assign(loader,{naturalWidth:100,naturalHeight:80});
        loader.events.load();
    };
    const layers=()=>entry.element.children.map(child=>child.className);
    apply(entry,frame,null,send);
    assert.deepEqual(layers(),[]);
    load(loaders[1]);
    load(loaders[2]);
    assert.deepEqual(layers(),['gui-image-layer gui-image-hover','gui-image-layer gui-image-pressed']);
    assert.deepEqual(receipts,[]);
    load(loaders[0]);
    apply(entry,{...frame,imageHover:'rbxassetid://4'},null,send);
    load(loaders[1]);
    assert.deepEqual(layers(),['gui-image-layer gui-image-pressed','gui-image-layer gui-image-base']);
    assert.deepEqual(receipts,['base']);
});
