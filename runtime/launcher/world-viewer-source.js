import * as T from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {createWorldRenderer} from './world-renderer-factory.js';
import {WorldAssets} from './world-assets.js';
import {clothingFaces} from './world-clothing.js';
import {finite,clamp,vector,enumValue,materialName,partOpacity,partShape,primitiveGeometries,materialTexture,makeMaterial,decalMaterial,transformOf,PoseTrack} from './world-appearance.js';

export function mount(container,{forceSoftware=false,onBackendChange=()=>{},loadAsset=null}={}) {
    let renderer=createWorldRenderer({forceSoftware});
    const stage=document.createElement('div');stage.className='world-stage';stage.style.cssText='position:absolute;inset:0;z-index:0';container.prepend(stage);
    const scene=new T.Scene(),camera=new T.PerspectiveCamera(70,1,.1,10000);camera.position.set(12,10,18);
    const controls=new OrbitControls(camera,stage);controls.enableDamping=true;controls.dampingFactor=.12;controls.maxDistance=3000;controls.minDistance=.5;
    controls.mouseButtons={LEFT:null,MIDDLE:T.MOUSE.DOLLY,RIGHT:T.MOUSE.ROTATE};controls.maxPolarAngle=Math.PI*.96;
    const hemisphere=new T.HemisphereLight(0xdceaff,0x7a746e,1.2),sun=new T.DirectionalLight(0xfff3dc,2);
    sun.castShadow=true;sun.shadow.mapSize.set(2048,2048);sun.shadow.normalBias=.035;sun.shadow.bias=-.00015;sun.shadow.camera.near=.5;sun.shadow.camera.far=500;
    Object.assign(sun.shadow.camera,{left:-70,right:70,top:70,bottom:-70});sun.shadow.camera.updateProjectionMatrix();scene.add(hemisphere,sun,sun.target);
    const geometry=primitiveGeometries();geometry.decal=new T.PlaneGeometry(1,1);
    const batches=new Map(),tracks=new Map(),textures=new Map(),matrix=new T.Matrix4(),color=new T.Color(),offset=new T.Vector3(),size=new T.Vector3(),rotation=new T.Quaternion();
    let destroyed=false,mode='follow',lastFocus=null,focusTrack=null,ready=false,lastFrame=null,dirty=false,lightingKey='',sky=null,pmrem=null,environment=null;
    let last=performance.now(),frames=0,fps=0,clock=14,shadowEnabled=true,meshBounds=0,skinnedMeshes=0;
    const assets=new WorldAssets(loadAsset,()=>{dirty=true;});
    const stats=()=>({...assets.stats,meshBounds,skinnedMeshes});
    function setupRenderer(){
        renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.outputColorSpace=T.SRGBColorSpace;
        if(renderer.roveBackend==='webgl'){renderer.shadowMap.enabled=shadowEnabled;renderer.shadowMap.type=T.PCFShadowMap;renderer.toneMapping=T.ACESFilmicToneMapping;renderer.toneMappingExposure=1;pmrem=new T.PMREMGenerator(renderer);}
        stage.replaceChildren(renderer.domElement);renderer.domElement.setAttribute('aria-label','Replicated game world and character');renderer.domElement.dataset.renderer=renderer.roveBackend;onBackendChange(renderer.roveBackend);
    }
    setupRenderer();
    const resize=()=>{const w=container.clientWidth,h=container.clientHeight;renderer.setSize(w,h,false);camera.aspect=w/Math.max(h,1);camera.updateProjectionMatrix();};
    const observer=new ResizeObserver(resize);observer.observe(container);resize();
    const rgb=(value,fallback)=>new T.Color().setRGB(...(vector(value?.$color3,fallback)),T.SRGBColorSpace);
    function lighting(data={}) {
        const signature=JSON.stringify(data);if(signature===lightingKey)return;lightingKey=signature;
        clock=finite(data.ClockTime,14);if(typeof data.TimeOfDay==='string'&&data.ClockTime===undefined){const values=data.TimeOfDay.split(':').map(Number);if(values.length>=2&&values.every(Number.isFinite))clock=values[0]+values[1]/60;}
        const angle=(clock-6)/24*Math.PI*2,day=clamp(Math.sin(angle)*2,0,1);
        const top=new T.Color().setRGB(.035+.30*day,.045+.53*day,.09+.72*day,T.SRGBColorSpace),horizon=new T.Color().setRGB(.08+.7*day,.09+.77*day,.15+.72*day,T.SRGBColorSpace);
        scene.userData.sky={top:top.clone(),horizon:horizon.clone()};scene.background=horizon.clone();
        const skyData=new Uint8Array(128*64*4),tmp=new T.Color();
        for(let y=0;y<64;y++)for(let x=0;x<128;x++){const t=Math.pow(clamp(y/32,0,1),.7);tmp.copy(top).lerp(horizon,t).convertLinearToSRGB();const i=(y*128+x)*4;skyData[i]=tmp.r*255;skyData[i+1]=tmp.g*255;skyData[i+2]=tmp.b*255;skyData[i+3]=255;}
        sky?.dispose();sky=new T.DataTexture(skyData,128,64);sky.colorSpace=T.SRGBColorSpace;sky.mapping=T.EquirectangularReflectionMapping;sky.magFilter=T.LinearFilter;sky.needsUpdate=true;
        if(renderer.roveBackend==='webgl'){scene.background=sky;environment?.dispose();environment=pmrem.fromEquirectangular(sky);scene.environment=environment.texture;}
        hemisphere.color.copy(rgb(data.OutdoorAmbient,[.5,.5,.5]));hemisphere.groundColor.copy(rgb(data.Ambient,[.25,.25,.25]));hemisphere.intensity=1.35;
        sun.color.copy(rgb(data.ColorShift_Top,[1,.96,.88]));if(sun.color.r+sun.color.g+sun.color.b<.05)sun.color.set(0xfff4df);
        sun.intensity=clamp(finite(data.Brightness,2),0,10)*day;shadowEnabled=data.GlobalShadows!==false;
        sun.castShadow=shadowEnabled;sun.shadow.radius=1+clamp(finite(data.ShadowSoftness,.2),0,1)*3;
        if(renderer.shadowMap)renderer.shadowMap.enabled=shadowEnabled;
        renderer.toneMappingExposure=Math.pow(2,clamp(finite(data.ExposureCompensation),-4,4));
        const fogStart=clamp(finite(data.FogStart,1000),0,100000),fogEnd=clamp(finite(data.FogEnd,100000),fogStart+1,100001);
        scene.fog=new T.Fog(rgb(data.FogColor,[.75,.82,.9]),fogStart,fogEnd);
        scene.userData.sunDirection=new T.Vector3(Math.cos(angle),Math.max(.05,Math.sin(angle)),.35).normalize();
        scene.userData.sunIntensity=sun.intensity;
    }
    lighting();
    function frame(data){
        if(destroyed)return;lastFrame=data;dirty=false;lighting(data.lighting);
        const now=performance.now(),seen=new Set(),next=new Map();meshBounds=0;skinnedMeshes=0;
        const add=(key,g,p,material,extra={})=>{if(!next.has(key))next.set(key,{geometry:g,part:p,material,entries:[]});next.get(key).entries.push({part:p,...extra});};
        for(const part of (data.parts||[]).slice(0,1800)){
            if(!vector(part.position,null)||!vector(part.size,null))continue;
            seen.add(part.id);const pose=transformOf(part);
            if(tracks.has(part.id))tracks.get(part.id).update(pose,now);else tracks.set(part.id,new PoseTrack(pose,now));
            if(partOpacity(part)<.001)continue;
            const shape=partShape(part),file=part.mesh?.id?assets.get('mesh',part.mesh.id):null;
            const map=part.mesh?.texture?assets.get('texture',part.mesh.texture):null;
            let g=file||geometry[shape]||geometry.box;
            if((part.class==='MeshPart'||part.class==='UnionOperation'||shape==='file')&&!file)meshBounds++;
            if(file?.userData.skinned)skinnedMeshes++;
            const name=materialName(part.material);
            if(!textures.has(name))textures.set(name,materialTexture(name));
            const key=[file?'mesh:'+part.mesh.id:shape,name,partOpacity(part).toFixed(3),finite(part.reflectance).toFixed(2),part.castShadow!==false,map?part.mesh.texture:''].join('|');
            add(key,g,part,()=>makeMaterial(part,map,textures.get(name)),{file:!!file});
            let layer=0;
            for(const decal of [...(part.decals||[]),...clothingFaces(part)]){
                const texture=assets.get('texture',decal.texture);if(!texture)continue;
                let uvRect=[1,1,0,0];const repeat=decal.class==='Texture';
                if(decal.crop){const [x,y,w,h]=decal.crop;uvRect=[w/585,h/559,x/585,1-(y+h)/559];}
                else if(repeat){const s=vector(part.size,[1,1,1]),axis=(enumValue(decal.face)??5)%3,u=axis===0?s[2]:s[0],v=axis===1?s[2]:s[1];uvRect=[u/Math.max(.01,finite(decal.tileU,2)),v/Math.max(.01,finite(decal.tileV,2)),0,0];}
                const mapKey=decal.texture+'|'+JSON.stringify(uvRect)+'|'+repeat;
                const opacity=partOpacity(part)*(1-clamp(finite(decal.transparency),0,1));if(opacity<.001)continue;
                const key='decal|'+mapKey+'|'+opacity.toFixed(3);
                add(key,geometry.decal,part,()=>decalMaterial(texture,opacity,uvRect,repeat),{decal:{...decal,layer:layer++}});
            }
        }
        for(const id of tracks.keys())if(!seen.has(id))tracks.delete(id);
        for(const [key,batch] of batches)if(!next.has(key)){scene.remove(batch.mesh);batch.mesh.dispose();batch.mesh.material.dispose();batches.delete(key);}
        for(const [key,nextBatch] of next){
            let batch=batches.get(key);
            if(!batch||batch.mesh.instanceMatrix.count<nextBatch.entries.length||batch.mesh.geometry!==nextBatch.geometry){
                if(batch){scene.remove(batch.mesh);batch.mesh.dispose();batch.mesh.material.dispose();}
                const mesh=new T.InstancedMesh(nextBatch.geometry,nextBatch.material(),Math.max(4,nextBatch.entries.length));mesh.instanceMatrix.setUsage(T.DynamicDrawUsage);mesh.frustumCulled=false;
                mesh.castShadow=nextBatch.part.castShadow!==false&&!key.startsWith('decal')&&partOpacity(nextBatch.part)>.8;mesh.receiveShadow=true;scene.add(mesh);batch={mesh};batches.set(key,batch);
            }
            batch.entries=nextBatch.entries;batch.mesh.count=batch.entries.length;
            batch.entries.forEach(({part,decal},i)=>{color.copy(rgb(decal?.color||part.color,[.64,.64,.64]));if(!decal&&part.mesh?.vertexColor)color.multiply(new T.Color(...vector(part.mesh.vertexColor,[1,1,1])));batch.mesh.setColorAt(i,color);});batch.mesh.instanceColor.needsUpdate=true;
        }
        const focus=vector(data.focus,null);
        if(focus){const pose=transformOf({position:focus,size:[1,1,1]});if(focusTrack)focusTrack.update(pose,now);else focusTrack=new PoseTrack(pose,now);}
        else if(!data.parts?.length){focusTrack=null;lastFocus=null;ready=false;}
        if(mode==='script'&&data.camera?.frame?.$cframe){const cf=data.camera.frame.$cframe,pose=transformOf({position:cf.position,frame:data.camera.frame});camera.position.copy(pose.position);camera.quaternion.copy(pose.rotation);camera.fov=clamp(finite(data.camera.fov,70),1,120);camera.updateProjectionMatrix();}
        updatePoses(now);
    }
    function updatePoses(now){
        const poses=new Map();for(const [id,track] of tracks)poses.set(id,track.sample(now));
        for(const batch of batches.values()){
            batch.entries.forEach(({part,file,decal},i)=>{
                const pose=poses.get(part.id);if(!pose)return;offset.set(0,0,0);size.copy(pose.scale);rotation.copy(pose.rotation);
                if(decal){
                    const face=enumValue(decal.face)??5;const q=new T.Quaternion(),half=pose.scale.clone().multiplyScalar(.5);
                    if(face===0){q.setFromAxisAngle(new T.Vector3(0,1,0),Math.PI/2);offset.x=half.x+.005;size.set(pose.scale.z,pose.scale.y,1);}
                    else if(face===3){q.setFromAxisAngle(new T.Vector3(0,1,0),-Math.PI/2);offset.x=-half.x-.005;size.set(pose.scale.z,pose.scale.y,1);}
                    else if(face===1||face===4){q.setFromAxisAngle(new T.Vector3(1,0,0),face===1?-Math.PI/2:Math.PI/2);offset.y=(face===1?1:-1)*(half.y+.005);size.set(pose.scale.x,pose.scale.z,1);}
                    else{if(face===5)q.setFromAxisAngle(new T.Vector3(0,1,0),Math.PI);offset.z=(face===5?-1:1)*(half.z+.005);size.set(pose.scale.x,pose.scale.y,1);}
                    rotation.multiply(q);
                    offset.multiplyScalar(1+(decal.layer||0)*.001);
                    if(decal.graphic)size.multiplyScalar(.8);
                }else if(part.mesh){
                    offset.fromArray(vector(part.mesh.offset));
                    if(file&&part.mesh.class==='MeshPart'){
                        const box=batch.mesh.geometry.boundingBox,extent=box.getSize(new T.Vector3()),center=box.getCenter(new T.Vector3());
                        size.set(pose.scale.x/Math.max(.00001,extent.x),pose.scale.y/Math.max(.00001,extent.y),pose.scale.z/Math.max(.00001,extent.z));offset.sub(center.multiply(size));
                    }else if(file){size.fromArray(vector(part.mesh.scale,[1,1,1]));}
                    else size.multiply(new T.Vector3(...vector(part.mesh.scale,[1,1,1])));
                }
                offset.applyQuaternion(pose.rotation).add(pose.position);matrix.compose(offset,rotation,size);batch.mesh.setMatrixAt(i,matrix);
            });batch.mesh.instanceMatrix.needsUpdate=true;
        }
        if(mode==='follow'&&focusTrack){
            const target=focusTrack.sample(now).position.add(new T.Vector3(0,1.7,0));
            if(!ready){controls.target.copy(target);camera.position.copy(target).add(new T.Vector3(9,6,14));ready=true;}
            else if(lastFocus){const delta=target.clone().sub(lastFocus);camera.position.add(delta);controls.target.add(delta);}lastFocus=target;
        }
        const center=controls.target;sun.target.position.copy(center);sun.position.copy(center).addScaledVector(scene.userData.sunDirection,140);
    }
    function release(value){try{value.setAnimationLoop(null);value.dispose();if(!value.getContext?.().isContextLost?.())value.forceContextLoss?.();}catch{}}
    function useSoftware(){
        if(destroyed||renderer.roveBackend==='software')return;const previous=renderer;renderer=createWorldRenderer({forceSoftware:true});
        environment?.dispose();environment=null;pmrem?.dispose();pmrem=null;scene.environment=null;lightingKey='';lighting(lastFrame?.lighting);frames=0;fps=0;last=performance.now();setupRenderer();resize();release(previous);renderer.setAnimationLoop(draw);
    }
    function draw(){
        if(destroyed||document.hidden)return;const now=performance.now();
        if(dirty&&lastFrame)frame(lastFrame);updatePoses(now);if(mode!=='script')controls.update();
        try{renderer.render(scene,camera);}catch(error){if(renderer.roveBackend==='webgl')useSoftware();else throw error;}
        frames++;if(now-last>1000){fps=Math.round(frames*1000/(now-last));frames=0;last=now;}
    }
    const gpuCanvas=renderer.roveBackend==='webgl'?renderer.domElement:null,lost=event=>{event.preventDefault();useSoftware();};gpuCanvas?.addEventListener('webglcontextlost',lost);renderer.setAnimationLoop(draw);
    return {frame,setMode(value){mode=value;controls.enabled=mode!=='script';lastFocus=null;ready=false;},direction(x,z){const forward=new T.Vector3();camera.getWorldDirection(forward);forward.y=0;forward.normalize();const right=new T.Vector3().crossVectors(forward,camera.up).normalize(),v=right.multiplyScalar(x).addScaledVector(forward,-z);if(v.length()>1)v.normalize();return [v.x,0,v.z];},
        get fps(){return fps;},get backend(){return renderer.roveBackend;},get reducedDetail(){return renderer.truncated===true;},get stats(){return stats();},
        dispose(){destroyed=true;gpuCanvas?.removeEventListener('webglcontextlost',lost);observer.disconnect();controls.dispose();assets.dispose();for(const batch of batches.values()){batch.mesh.dispose();batch.mesh.material.dispose();}Object.values(geometry).forEach(g=>g.dispose());for(const texture of textures.values())texture?.dispose();environment?.dispose();pmrem?.dispose();sky?.dispose();sun.shadow.dispose();release(renderer);stage.remove();}};
}
