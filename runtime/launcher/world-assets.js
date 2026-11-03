import * as T from 'three';

export class WorldAssets {
    constructor(load,onChange=()=>{}){this.load=load;this.onChange=onChange;this.entries=new Map();this.queue=[];this.active=0;this.disposed=false;this.bytes=0;}
    get(kind,reference){
        if(typeof reference!=='string'||!reference||!this.load)return null;
        const key=kind+':'+reference,entry=this.entries.get(key);
        if(entry)return entry.value;
        if(this.entries.size>=256)return null;
        this.entries.set(key,{value:null,status:'pending'});this.queue.push({key,kind,reference});this.pump();return null;
    }
    pump(){while(!this.disposed&&this.active<3&&this.queue.length){const task=this.queue.shift();this.active++;void this.resolve(task).finally(()=>{this.active--;this.pump();});}}
    async resolve({key,kind,reference}){
        try{
            const response=await this.load(kind,reference);if(this.disposed)return;
            let value,size=0;
            if(kind==='mesh'){
                const mesh=response.mesh;
                if(!mesh||mesh.positions.length>300000||mesh.indices.length>600000)throw Error('Mesh budget exceeded');
                value=new T.BufferGeometry();value.setAttribute('position',new T.Float32BufferAttribute(mesh.positions,3));
                value.setAttribute('normal',new T.Float32BufferAttribute(mesh.normals,3));value.setAttribute('uv',new T.Float32BufferAttribute(mesh.uv,2));value.setIndex(mesh.indices);
                value.computeBoundingBox();value.computeBoundingSphere();value.userData.skinned=mesh.skinned;
                size=mesh.positions.length*8+mesh.uv.length*4+mesh.indices.length*4;
            }else{
                const bytes=Uint8Array.from(atob(response.data),c=>c.charCodeAt(0));
                const bitmap=await createImageBitmap(new Blob([bytes],{type:response.mime}));
                if(this.disposed){bitmap.close();return;}
                if(bitmap.width>8192||bitmap.height>8192){bitmap.close();throw Error('Texture dimensions exceed budget');}
                const ratio=Math.min(1,2048/bitmap.width,2048/bitmap.height),canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(bitmap.width*ratio));canvas.height=Math.max(1,Math.round(bitmap.height*ratio));
                const context=canvas.getContext('2d');context.drawImage(bitmap,0,0,canvas.width,canvas.height);bitmap.close();
                const pixels=context.getImageData(0,0,canvas.width,canvas.height);
                value=new T.DataTexture(pixels.data,canvas.width,canvas.height);value.colorSpace=T.SRGBColorSpace;value.flipY=true;
                value.magFilter=T.LinearFilter;value.minFilter=T.LinearMipmapLinearFilter;value.generateMipmaps=true;value.needsUpdate=true;size=pixels.data.byteLength;
            }
            if(this.disposed||this.bytes+size>64*1024*1024){value.dispose();throw Error('Visual asset memory budget reached');}
            this.bytes+=size;this.entries.set(key,{status:'ready',value});this.onChange();
        }catch(error){if(!this.disposed){this.entries.set(key,{status:'failed',value:null,error:error.message});this.onChange();}}
    }
    get stats(){let pending=0,failed=0,ready=0;for(const entry of this.entries.values()){pending+=entry.status==='pending';failed+=entry.status==='failed';ready+=entry.status==='ready';}return {pending,failed,ready};}
    dispose(){this.disposed=true;this.queue=[];for(const entry of this.entries.values())entry.value?.dispose();this.entries.clear();}
}
