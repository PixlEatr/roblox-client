const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const context=vm.createContext({window:{}});vm.runInContext(fs.readFileSync(path.join(__dirname,'../../ui/text-layout.js'),'utf8'),context);
const {fitSize,editorOffset,truncateText}=context.window.RoveText;
test('text truncation distinguishes word and grapheme boundaries and preserves run style',()=>{
    const source='Alpha beta gamma',fits=choice=>choice.text.length<=11;
    assert.equal(truncateText(source,null,1,fits).text,'Alpha...');
    assert.equal(truncateText(source,null,2,fits).text,'Alpha be...');
    assert.equal(truncateText('abcdefgh',null,1,fits).text,'abcdefg...');
    const rich=[{text:'A',style:{}},{text:'👨‍👩‍👧‍👦BC',style:{weight:700}}];
    const choice=truncateText(rich.map(run=>run.text).join(''),rich,2,value=>value.text.length<=15);
    assert.equal(choice.text,'A👨‍👩‍👧‍👦...');
    assert.equal(choice.runs.at(-1).style.weight,700);
    assert.equal(choice.runs.at(-1).marker,true);
    assert.equal(truncateText('wide',null,2,()=>false).text,'');
});
test('TextBox offsets convert UTF-16, UTF-8, and textarea CRLF normalization',()=>{
    const source='aé🙂\r\nz';
    assert.deepEqual([0,1,2,3,4,5,6].map(index=>editorOffset(source,index,true)),[0,1,3,3,7,9,10]);
    assert.deepEqual([0,1,2,3,4,7,8,9,10].map(index=>editorOffset(source,index,false)),[0,1,1,2,2,4,4,5,6]);
});
test('fitting uses measured width and height and picks the largest fitting integer size',()=>{
    let calls=0;const measure=size=>{calls++;return {width:size*7,height:size*1.2};};
    assert.equal(fitSize(measure,1,100,210,80),30);assert(calls<=8);
    assert.equal(fitSize(measure,1,100,210,24),20);
});
test('narrow and wide glyph measurements of the same character count fit differently',()=>{
    const narrow=fitSize(s=>({width:8*s*.2,height:s}),1,100,100,100);
    const wide=fitSize(s=>({width:8*s*.9,height:s}),1,100,100,100);
    assert.equal(narrow,62);assert.equal(wide,13);assert(narrow>wide);
});
test('wrapping measurements, fixed constraints, and overflowing minimums stay bounded',()=>{
    const wrap=s=>({width:100,height:Math.ceil(12*s/100)*s});
    const result=fitSize(wrap,12,36,100,80);assert(result>=12&&result<=36);
    assert(wrap(result).height<=80.25);assert(wrap(result+1).height>80.25);
    assert.equal(fitSize(()=>({width:1000,height:1000}),18,36,100,80),18);
    assert.equal(fitSize(()=>({width:0,height:0}),1,100,0,0),100);
    assert.equal(fitSize(()=>({width:1,height:1}),20,20,100,80),20);
});
test('live layout caches measurements across frames and grapheme reveals',()=>{
    let reads=0;
    class Element{
        constructor(tag='div'){this.tagName=tag.toUpperCase();this.style={fontFamily:'LoadedFont',fontWeight:'400',fontStyle:'normal'};this.dataset={};this.children=[];this.textContent='';}
        setAttribute(){}append(child){child.parentElement=this;this.children.push(child);}
        replaceChildren(...children){this.children=[];for(const child of children)this.append(child);}
        cloneNode(){const clone=new Element(this.tagName);Object.assign(clone.style,this.style);return clone;}
        remove(){this.parentElement=null;}
        getBoundingClientRect(){reads++;const size=parseFloat(this.style.fontSize)||18;
            const width=this.style.width==='max-content'?this.textContent.length*size*.5:parseFloat(this.style.width)||491.5;
            const lines=this.style.whiteSpace==='pre-wrap'?Math.max(1,Math.ceil(this.textContent.length*size*.5/width)):1;
            return {width,height:lines*size*(parseFloat(this.style.lineHeight)||1)};
        }
        get scrollWidth(){return Math.ceil(this.getBoundingClientRect().width);}
        get scrollHeight(){return Math.ceil(this.getBoundingClientRect().height);}
    }
    const document={body:new Element(),createElement:tag=>new Element(tag),activeElement:null};
    const local=vm.createContext({window:{},document});vm.runInContext(fs.readFileSync(path.join(__dirname,'../../ui/text-layout.js'),'utf8'),local);
    const text=local.window.RoveText,entry={element:new Element()};
    const frame={class:'TextLabel',text:'Measured text sample',box:[0,0,491.5,116,0],textScaled:true,textSizeConstraint:[12,36]};
    text.apply(entry,frame);assert.equal(entry.element.dataset.textSize,'36');assert.equal(entry.element.dataset.textFits,'true');
    const initial=reads;text.apply(entry,frame);assert.equal(reads,initial,'unchanged frames must not repeat browser layout reads');
    entry.element.style.fontFamily='NewlyLoadedFont';text.apply(entry,frame);assert(reads>initial,'font loading must invalidate measured layout');
    frame.richRuns=[{text:'Ae',style:{}},{text:'\u0301👨‍👩‍👧‍👦!',style:{underline:true}}];
    frame.contentText='Ae\u0301👨‍👩‍👧‍👦!';
    frame.maxVisibleGraphemes=2;
    text.apply(entry,frame);
    const runs=()=>entry.textElement.children.map(span=>[span.textContent,span.style.visibility||'visible']);
    assert.deepEqual(runs(),[['Ae','visible'],['\u0301','visible'],['👨‍👩‍👧‍👦!','hidden']]);
    const measured=reads;
    frame.maxVisibleGraphemes=3;
    text.apply(entry,frame);
    assert.deepEqual(runs(),[['Ae','visible'],['\u0301👨‍👩‍👧‍👦','visible'],['!','hidden']]);
    assert.equal(reads,measured,'reveal changes must reuse the full text measurement');
    frame.maxVisibleGraphemes=0;
    text.apply(entry,frame);
    assert(runs().every(([,visibility])=>visibility==='hidden'));
    frame.maxVisibleGraphemes=-1;
    text.apply(entry,frame);
    assert(runs().every(([,visibility])=>visibility==='visible'));
    assert.equal(reads,measured);
    text.dispose();
});
