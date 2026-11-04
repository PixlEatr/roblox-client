/* Font bytes come from the local bridge, scoped to currently displayed text. */
window.RoveFonts=(()=>{
    let serial=0;
    function descriptor(font){
        if(!font||typeof font.family!=='string'||font.family.length>512||!Number.isInteger(font.weight)||font.weight<100||font.weight>900||font.weight%100||![0,1].includes(font.style))return null;
        return {family:font.family,weight:font.weight,style:font.style};
    }
    const key=font=>JSON.stringify([font.family,font.weight,font.style]);
    class FontCache{
        constructor(load,onChange=()=>{}){this.load=load;this.onChange=onChange;this.entries=new Map();this.queue=[];this.active=0;this.epoch=0;this.disposed=false;this.bytes=0;}
        observe(gui){
            const visible=new Set();
            for(const node of gui||[]){
                for(const item of [node,...(Array.isArray(node.richRuns)?node.richRuns:[])]){
                    const font=descriptor(item.fontFace);
                    if(font)visible.add(key(font));
                }
            }
            for(const [id,entry] of this.entries)if(!visible.has(id)){
                if(entry.face)document.fonts.delete(entry.face);this.bytes-=entry.bytes||0;this.entries.delete(id);
            }
        }
        apply(element,input){
            const font=descriptor(input);
            element.style.fontWeight=String(font?.weight||400);element.style.fontStyle=font?.style?'italic':'normal';
            element.style.fontFamily='Arial, sans-serif';
            if(!font){element.dataset.fontStatus='unavailable';return;}
            const id=key(font);let entry=this.entries.get(id);
            if(!entry&&!this.disposed&&this.entries.size<64){entry={font,status:'pending',attempts:0};this.entries.set(id,entry);this.queue.push({id,entry,epoch:this.epoch});this.pump();}
            if(entry?.status==='failed'&&entry.attempts<3&&Date.now()>=entry.retryAt&&!this.disposed){entry.status='pending';this.queue.push({id,entry,epoch:this.epoch});this.pump();}
            element.dataset.fontStatus=entry?.status||'limited';
            if(entry?.status==='ready')element.style.fontFamily='"'+entry.name+'", Arial, sans-serif';
        }
        pump(){
            while(!this.disposed&&this.active<2&&this.queue.length){
                const task=this.queue.shift();if(task.epoch!==this.epoch||this.entries.get(task.id)!==task.entry)continue;
                this.active++;void this.resolve(task).finally(()=>{this.active--;this.pump();});
            }
        }
        async resolve({id,entry,epoch}){
            entry.attempts++;
            const current=()=>!this.disposed&&epoch===this.epoch&&this.entries.get(id)===entry;
            try{
                if(typeof FontFace!=='function'||!document.fonts)throw Error('Browser font loading unavailable');
                const response=await this.load('font',entry.font.family,entry.font.weight,entry.font.style);
                if(!current())return;
                if(!response?.ok||!['font/ttf','font/otf'].includes(response.mime)||typeof response.data!=='string'||response.data.length>12*1024*1024||!Number.isInteger(response.weight)||response.weight<100||response.weight>900||response.weight%100||![0,1].includes(response.style))throw Error('Invalid font response');
                const bytes=Uint8Array.from(atob(response.data),c=>c.charCodeAt(0));
                if(bytes.byteLength>8*1024*1024||this.bytes+bytes.byteLength>32*1024*1024)throw Error('Font memory budget reached');
                const name='RoveFont'+(++serial);
                const face=new FontFace(name,bytes,{weight:String(response.weight),style:response.style?'italic':'normal'});
                await face.load();if(!current())return;
                if(this.bytes+bytes.byteLength>32*1024*1024)throw Error('Font memory budget reached');
                document.fonts.add(face);this.bytes+=bytes.byteLength;Object.assign(entry,{status:'ready',face,name,bytes:bytes.byteLength});this.onChange();
            }catch(error){if(current()){Object.assign(entry,{status:'failed',retryAt:Date.now()+1000*2**(entry.attempts-1)});this.onChange();}}
        }
        clear(){this.epoch++;this.queue=[];for(const entry of this.entries.values())if(entry.face)document.fonts.delete(entry.face);this.entries.clear();this.bytes=0;}
        dispose(){this.clear();this.disposed=true;}
    }
    return {FontCache};
})();
