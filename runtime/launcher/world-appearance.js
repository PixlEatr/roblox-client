import * as T from 'three';
import {ConvexGeometry} from 'three/addons/geometries/ConvexGeometry.js';
import {RoundedBoxGeometry} from 'three/addons/geometries/RoundedBoxGeometry.js';

export const finite=(v,fallback=0)=>typeof v==='number'&&Number.isFinite(v)?v:fallback;
export const clamp=(v,min,max)=>Math.max(min,Math.min(max,v));
export function vector(value,fallback=[0,0,0]) {
    const v=value?.$vector3??value;
    return Array.isArray(v)&&v.length===3&&v.every(n=>typeof n==='number'&&Number.isFinite(n)&&Math.abs(n)<1e8)?v:fallback;
}
export const enumValue=value=>value?.$enum?.value??value;
const names={256:'Plastic',272:'SmoothPlastic',288:'Neon',512:'Wood',528:'WoodPlanks',784:'Marble',788:'Basalt',800:'Slate',804:'CrackedLava',816:'Concrete',820:'Limestone',832:'Granite',836:'Pavement',848:'Brick',864:'Pebble',880:'Cobblestone',896:'Rock',912:'Sandstone',1040:'CorrodedMetal',1056:'DiamondPlate',1072:'Foil',1088:'Metal',1280:'Grass',1284:'LeafyGrass',1296:'Sand',1312:'Fabric',1328:'Snow',1344:'Mud',1360:'Ground',1376:'Asphalt',1536:'Ice',1552:'Glacier',1568:'Glass',1584:'ForceField',2048:'Water'};
export const materialName=value=>names[enumValue(value)]??(Object.values(names).includes(value?.$enum?.name??value)?value?.$enum?.name??value:'Plastic');
export function partOpacity(part){return (1-clamp(finite(part.transparency),0,1))*(1-clamp(finite(part.localTransparency),0,1));}
export function partShape(part) {
    const mesh=part.mesh;
    if(mesh?.class==='BlockMesh')return 'box';
    if(mesh?.class==='CylinderMesh')return 'verticalCylinder';
    if(mesh?.class==='SpecialMesh')return ({0:'head',1:'box',2:'wedge',3:'sphere',4:'verticalCylinder',5:'file',6:'box',7:'wedge',8:'corner',9:'wedge',10:'wedge',11:'corner'})[enumValue(mesh.type)]??'box';
    if(part.class==='WedgePart')return 'wedge';
    if(part.class==='CornerWedgePart')return 'corner';
    return ['sphere','box','cylinder','wedge','corner'][enumValue(part.shape)??1]??'box';
}
export function primitiveGeometries(){
    const hull=points=>new ConvexGeometry(points.map(p=>new T.Vector3(...p)));
    const cylinder=new T.CylinderGeometry(.5,.5,1,24);cylinder.rotateZ(Math.PI/2);
    return {box:new T.BoxGeometry(1,1,1),sphere:new T.SphereGeometry(.5,24,16),cylinder,
        verticalCylinder:new T.CylinderGeometry(.5,.5,1,24),head:new RoundedBoxGeometry(1,1,1,3,.18),
        wedge:hull([[-.5,-.5,-.5],[.5,-.5,-.5],[-.5,-.5,.5],[.5,-.5,.5],[-.5,.5,.5],[.5,.5,.5]]),
        corner:hull([[-.5,-.5,-.5],[.5,-.5,-.5],[-.5,-.5,.5],[.5,-.5,.5],[.5,.5,-.5]])};
}

// Procedural detail is a local approximation of Roblox's named materials;
// downloaded mesh/face textures use their own authored UVs instead.
export function materialTexture(name) {
    if(['Plastic','SmoothPlastic','Neon','Glass','ForceField','Metal','Ice','Water'].includes(name))return null;
    const size=128,data=new Uint8Array(size*size*4);
    const noise=(x,y)=>{let n=Math.imul(x+17,374761393)^Math.imul(y+73,668265263);n=Math.imul(n^(n>>>13),1274126177);return (n>>>0)/4294967295;};
    for(let y=0;y<size;y++)for(let x=0;x<size;x++) {
        const n=noise(x,y);let shade=.84+n*.16;
        if(name==='Brick'){const row=Math.floor(y/32),joint=(x+row%2*32)%64;shade=y%32<3||joint<3?.53:.78+n*.2;}
        else if(name==='Wood'||name==='WoodPlanks'){shade=.77+.13*Math.sin(x*.65+Math.sin(y*.1)*2)+n*.1;if(name==='WoodPlanks'&&x%32<2)shade=.45;}
        else if(name==='Marble')shade=.9-.17*Math.pow(Math.abs(Math.sin(x*.09+y*.08+Math.sin(y*.03)*3)),12);
        else if(name==='Grass'||name==='LeafyGrass')shade=.68+n*.32+(y%9===0?.05:0);
        else if(name==='Fabric')shade=.77+((x+y)%2)*.14+n*.07;
        else if(name==='DiamondPlate')shade=Math.abs((x+y)%32-16)<2&&x%32<18?.65:.9;
        const i=(y*size+x)*4,v=Math.round(clamp(shade,0,1)*255);data[i]=data[i+1]=data[i+2]=v;data[i+3]=255;
    }
    const texture=new T.DataTexture(data,size,size);texture.colorSpace=T.SRGBColorSpace;texture.wrapS=texture.wrapT=T.RepeatWrapping;
    texture.magFilter=T.LinearFilter;texture.minFilter=T.LinearMipmapLinearFilter;texture.generateMipmaps=true;texture.needsUpdate=true;
    return texture;
}
export function makeMaterial(part,map=null,procedural=null) {
    const name=materialName(part.material),opacity=partOpacity(part),metal=/Metal|DiamondPlate|Foil/.test(name);
    const material=new T.MeshStandardMaterial({color:0xffffff,roughness:metal?.28:name==='Glass'||name==='Ice'?.09:name==='SmoothPlastic'?.32:.82,
        metalness:metal?.75:clamp(finite(part.reflectance),0,1)*.7,transparent:opacity<.999,opacity,depthWrite:opacity>=.999,
        map:map||procedural,alphaTest:map?.02:0,side:map?T.DoubleSide:T.FrontSide});
    if(name==='Neon'){material.emissive.setRGB(1,1,1);material.emissiveIntensity=.65;material.roughness=.25;
        material.onBeforeCompile=shader=>{shader.fragmentShader=shader.fragmentShader.replace('#include <emissivemap_fragment>','#include <emissivemap_fragment>\n#ifdef USE_INSTANCING_COLOR\n totalEmissiveRadiance *= vColor;\n#endif');};material.customProgramCacheKey=()=> 'rove-neon-v1';}
    if(procedural&&!map){
        material.userData.tileSize=name==='Brick'?8:name.startsWith('Wood')?5:6;
        material.onBeforeCompile=shader=>{
            shader.uniforms.roveTileSize={value:material.userData.tileSize};
            shader.vertexShader=shader.vertexShader.replace('#include <common>','#include <common>\nvarying vec3 vRovePosition;\nvarying vec3 vRoveNormal;');
            shader.vertexShader=shader.vertexShader.replace('#include <begin_vertex>',`#include <begin_vertex>
                vRovePosition=position;vRoveNormal=normal;
                #ifdef USE_INSTANCING
                    vRovePosition*=vec3(length(instanceMatrix[0].xyz),length(instanceMatrix[1].xyz),length(instanceMatrix[2].xyz));
                #endif`);
            shader.fragmentShader=shader.fragmentShader.replace('#include <common>','#include <common>\nvarying vec3 vRovePosition;\nvarying vec3 vRoveNormal;\nuniform float roveTileSize;');
            shader.fragmentShader=shader.fragmentShader.replace('#include <map_fragment>',`vec3 rn=abs(vRoveNormal);
                vec2 ruv=rn.y>=rn.x&&rn.y>=rn.z?vRovePosition.xz:rn.x>=rn.z?vRovePosition.zy:vRovePosition.xy;
                diffuseColor*=texture2D(map,ruv/roveTileSize);`);
        };
        material.customProgramCacheKey=()=> 'rove-surface-v1';
    }
    return material;
}

export function decalMaterial(texture,opacity,uvRect=[1,1,0,0],repeat=false) {
    const material=new T.MeshStandardMaterial({map:texture,transparent:true,opacity,depthWrite:false,roughness:.85,side:T.DoubleSide,polygonOffset:true,polygonOffsetFactor:-2,polygonOffsetUnits:-2});
    material.userData.uvRect=uvRect;material.userData.repeatTexture=repeat;
    material.onBeforeCompile=shader=>{
        shader.uniforms.roveUvRect={value:new T.Vector4(...uvRect)};
        shader.vertexShader=shader.vertexShader.replace('#include <common>','#include <common>\nuniform vec4 roveUvRect;');
        shader.vertexShader=shader.vertexShader.replace('#include <uv_vertex>','#include <uv_vertex>\nvMapUv=vMapUv*roveUvRect.xy+roveUvRect.zw;');
        if(repeat)shader.fragmentShader=shader.fragmentShader.replace('#include <map_fragment>','diffuseColor *= texture2D(map, fract(vMapUv));');
    };
    material.customProgramCacheKey=()=>repeat?'rove-decal-repeat-v1':'rove-decal-crop-v1';
    return material;
}

export function transformOf(part) {
    const position=new T.Vector3(...vector(part.position)),scale=new T.Vector3(...vector(part.size,[1,1,1])).clampScalar(.001,1e6),rotation=new T.Quaternion();
    const r=part.frame?.$cframe?.rotation;
    if(Array.isArray(r)&&r.length===9&&r.every(n=>Number.isFinite(n))){const m=new T.Matrix4().set(r[0],r[1],r[2],0,r[3],r[4],r[5],0,r[6],r[7],r[8],0,0,0,0,1);rotation.setFromRotationMatrix(m).normalize();}
    return {position,scale,rotation};
}

// Interpolate rendering only. Never predict motion, modify the VM, or turn
// missing gravity/animation replication into invented character movement.
export class PoseTrack {
    constructor(pose,time){this.from=pose;this.to=pose;this.time=time;this.duration=0;this.received=time;}
    sample(time){const t=clamp((time-this.time)/Math.max(1,this.duration),0,1);return {
        position:this.from.position.clone().lerp(this.to.position,t),scale:this.from.scale.clone().lerp(this.to.scale,t),rotation:this.from.rotation.clone().slerp(this.to.rotation,t)};}
    update(pose,time){
        if(pose.position.equals(this.to.position)&&pose.rotation.angleTo(this.to.rotation)<1e-5&&pose.scale.equals(this.to.scale))return;
        const current=this.sample(time),teleport=current.position.distanceToSquared(pose.position)>64*64;
        this.from=teleport?pose:current;this.to=pose;this.duration=teleport?0:clamp(time-this.received,50,180);this.time=time;this.received=time;
    }
}
