const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const Module=require('node:module');
const esbuild=require('../../ui/node_modules/esbuild');
const root=path.resolve(__dirname,'../../ui');
function load(source){const result=esbuild.buildSync({entryPoints:[path.join(root,source)],bundle:true,platform:'node',format:'cjs',write:false,logLevel:'silent'});const module=new Module(path.join(root,'renderer-test.cjs'));module.filename=path.join(root,'renderer-test.cjs');module.paths=Module._nodeModulePaths(root);module._compile(result.outputFiles[0].text,module.filename);return module.exports;}
const {createWorldRenderer}=load('world-renderer-factory.js');
const {clipTriangle,rasterTriangle,sampleTexture,packedColor,SoftwareWorldRenderer}=load('software-world-renderer.js');
const THREE=load('node_modules/three/build/three.cjs');
const {partShape,partOpacity,makeMaterial,decalMaterial,materialName,primitiveGeometries,PoseTrack,transformOf}=load('world-appearance.js');
const {clothingFaces}=load('world-clothing.js');
const {WorldAssets}=load('world-assets.js');
class Canvas {
    constructor(gl=null){this.gl=gl;this.calls=[];this.context={createImageData:(w,h)=>({data:new Uint8ClampedArray(w*h*4)}),putImageData:image=>{this.image=image;}};}
    getContext(kind,attributes){this.calls.push({kind,attributes});return kind==='2d'?this.context:this.gl;}
}
const context=()=>({isContextLost:()=>false,getExtension:()=>({loseContext(){this.lost=true;}})});

test('GPU success uses one context and keeps it alive',()=>{
    const canvas=new Canvas(context());let options;
    const gpu={};const renderer=createWorldRenderer({canvasFactory:()=>canvas,webGLFactory:value=>{options=value;return gpu;}});
    assert.equal(renderer,gpu);assert.equal(renderer.roveBackend,'webgl');assert.equal(options.context,canvas.gl);assert.equal(options.stencil,false);assert.equal(canvas.calls.length,1);
});
test('context refusal retries without antialiasing before choosing software',()=>{
    const canvases=[new Canvas(),new Canvas(context())];let options;
    const renderer=createWorldRenderer({canvasFactory:()=>canvases.shift(),webGLFactory:value=>{options=value;return {};}});
    assert.equal(renderer.roveBackend,'webgl');assert.equal(options.antialias,false);assert.equal(options.powerPreference,'low-power');
});
test('both GPU attempts denied automatically yield a fresh Canvas 2D renderer',()=>{
    const made=[];const renderer=createWorldRenderer({canvasFactory:()=>{const canvas=new Canvas();made.push(canvas);return canvas;},webGLFactory:()=>{throw Error('Must not construct without a context');}});
    assert.equal(renderer.roveBackend,'software');assert.equal(made.length,3);assert.equal(made[2].calls[0].kind,'2d');
    renderer.setSize(3840,2160);assert.equal(renderer.width,640);assert.equal(renderer.height,360);renderer.dispose();
});
test('failed GPU construction releases allocated contexts',()=>{
    let losses=0;
    const renderer=createWorldRenderer({canvasFactory:()=>new Canvas({isContextLost:()=>false,getExtension:()=>({loseContext:()=>{losses++;}})}),webGLFactory:()=>{throw Error('Driver failed');}});
    assert.equal(renderer.roveBackend,'software');assert.equal(losses,2);renderer.dispose();
});
test('software override never allocates a GPU context',()=>{
    const canvas=new Canvas();const renderer=createWorldRenderer({forceSoftware:true,canvasFactory:()=>canvas,webGLFactory:()=>{throw Error('GPU must not be requested');}});
    assert.equal(renderer.roveBackend,'software');assert.deepEqual(canvas.calls.map(c=>c.kind),['2d']);renderer.dispose();
});
test('homogeneous clipping handles crossing and fully hidden triangles',()=>{
    assert.deepEqual(clipTriangle([[2,0,0,1],[3,1,0,1],[3,-1,0,1]]),[]);
    const polygon=clipTriangle([[-.5,-.5,-2,1],[.5,-.5,0,1],[0,.5,0,1]]);
    assert.ok(polygon.length>=3);for(const v of polygon)for(let i=0;i<3;i++)assert.ok(v[i]>=-v[3]-1e-8&&v[i]<=v[3]+1e-8);
});
test('depth buffer renders closer faces correctly in either submission order',()=>{
    const triangle=z=>[[2,2,z],[28,2,z],[2,28,z]];
    for(const order of [true,false]){
        const pixels=new Uint32Array(32*32),depth=new Float32Array(32*32);depth.fill(Infinity);
        const draw=(z,color)=>rasterTriangle(triangle(z),32,32,pixels,depth,color);
        if(order){draw(.5,[255,0,0]);draw(-.5,[0,255,0]);}else{draw(-.5,[0,255,0]);draw(.5,[255,0,0]);}
        assert.equal(pixels[10*32+10],packedColor(0,255,0));
    }
});
test('real Three instanced geometry renders pixels without WebGL',()=>{
    const canvas=new Canvas(),renderer=new SoftwareWorldRenderer({canvas});renderer.setSize(160,100);
    const scene=new THREE.Scene();scene.background=new THREE.Color('#a8c6dc');
    const camera=new THREE.PerspectiveCamera(60,1.6,.1,100);camera.position.set(0,0,5);camera.lookAt(0,0,0);
    const mesh=new THREE.InstancedMesh(new THREE.BoxGeometry(2,2,2),new THREE.MeshStandardMaterial(),1);
    mesh.setMatrixAt(0,new THREE.Matrix4());mesh.setColorAt(0,new THREE.Color(1,0,0));scene.add(mesh);
    renderer.render(scene,camera);const pixel=(50*160+80)*4;
    assert.ok(canvas.image.data[pixel]>100);assert.equal(canvas.image.data[pixel+1],0);assert.equal(canvas.image.data[pixel+2],0);
    renderer.dispose();mesh.geometry.dispose();mesh.material.dispose();
});
test('software rendering bounds triangle work on large scenes',()=>{
    const renderer=new SoftwareWorldRenderer({canvas:new Canvas()});renderer.setSize(32,32);
    const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(60,1,.1,100);
    camera.position.set(0,0,5);camera.lookAt(0,0,0);
    const mesh=new THREE.InstancedMesh(new THREE.SphereGeometry(.5,16,12),new THREE.MeshStandardMaterial(),200);
    for(let i=0;i<200;i++)mesh.setMatrixAt(i,new THREE.Matrix4().makeTranslation(1000,0,0));scene.add(mesh);
    renderer.render(scene,camera);assert.equal(renderer.truncated,true);
    renderer.dispose();mesh.geometry.dispose();mesh.material.dispose();
});

test('primitive and SpecialMesh enums select the right geometry',()=>{
    assert.equal(partShape({shape:{$enum:{type:'PartType',value:0}}}),'sphere');
    for(const [value,shape] of [[0,'head'],[1,'box'],[2,'wedge'],[3,'sphere'],[4,'verticalCylinder'],[5,'file'],[6,'box']])assert.equal(partShape({mesh:{class:'SpecialMesh',type:{$enum:{value}}}}),shape);
    assert.equal(partShape({class:'CornerWedgePart'}),'corner');
});
test('part alpha and local alpha are preserved instead of one shared translucent value',()=>{
    assert.equal(partOpacity({transparency:.2,localTransparency:.5}),.4);
    assert.equal(partOpacity({transparency:1}),0);
    const a=makeMaterial({transparency:.2,material:1568}),b=makeMaterial({transparency:.8,material:1568});
    assert.equal(a.opacity,.8);assert.ok(Math.abs(b.opacity-.2)<1e-8);assert.equal(a.depthWrite,false);a.dispose();b.dispose();
    assert.equal(materialName({$enum:{name:'Metal',value:1088}}),'Metal');assert.equal(materialName('unbounded custom name'),'Plastic');
});
test('wedge hulls have finite normals and stay in their unit bounds',()=>{
    const geometries=primitiveGeometries();
    for(const name of ['wedge','corner']){const g=geometries[name];g.computeBoundingBox();assert.deepEqual(g.boundingBox.min.toArray(),[-.5,-.5,-.5]);assert.deepEqual(g.boundingBox.max.toArray(),[.5,.5,.5]);assert.ok(Array.from(g.attributes.normal.array).every(Number.isFinite));}
    Object.values(geometries).forEach(g=>g.dispose());
});
test('render poses interpolate translation and quaternion without modifying snapshots',()=>{
    const a={position:{$vector3:[0,0,0]},size:{$vector3:[1,1,1]}},b={position:{$vector3:[10,0,0]},size:{$vector3:[1,1,1]}};
    const track=new PoseTrack(transformOf(a),0);track.update(transformOf(b),100);
    assert.equal(track.sample(150).position.x,5);assert.equal(track.sample(250).position.x,10);
    assert.deepEqual(a.position.$vector3,[0,0,0]);assert.deepEqual(b.position.$vector3,[10,0,0]);
    track.update(transformOf({position:[200,0,0]}),300);assert.equal(track.sample(300).position.x,200);
});
test('the local pose can catch up sooner after a gap between samples',()=>{
    const start=transformOf({position:[0,0,0]}),end=transformOf({position:[10,0,0]});
    const local=new PoseTrack(start,0),remote=new PoseTrack(start,0);
    local.update(end,200,100);remote.update(end,200);
    assert.equal(local.sample(250).position.x,5);
    assert.equal(local.sample(300).position.x,10);
    assert.ok(remote.sample(300).position.x<10);
    assert.equal(remote.sample(380).position.x,10);
});
test('classic clothing maps front torso and both limbs to different template regions',()=>{
    const torso=clothingFaces({characterPart:'Torso',clothing:{shirt:'shirt',pants:'pants',graphic:'graphic'}});
    assert.equal(torso.length,13);assert.deepEqual(torso.find(d=>d.face===5&&d.texture==='shirt').crop,[231,74,128,128]);
    const right=clothingFaces({characterPart:'Right Arm',clothing:{shirt:'shirt'}}),left=clothingFaces({characterPart:'Left Arm',clothing:{shirt:'shirt'}});
    assert.notDeepEqual(right.find(d=>d.face===5).crop,left.find(d=>d.face===5).crop);
    assert.deepEqual(clothingFaces({characterPart:'UpperTorso',clothing:{shirt:'shirt'}}),[]);
});
test('software rasterizer applies texture UVs, alpha, and clothing crop transforms',()=>{
    const pixels=new Uint32Array(16*16),depth=new Float32Array(16*16);depth.fill(Infinity);
    const map={image:{width:2,height:1,data:new Uint8Array([255,0,0,255,0,255,0,255])},repeat:{x:.5,y:1},offset:{x:.75,y:0},flipY:true};
    rasterTriangle([[0,0,0,1,0,0],[16,0,0,1,0,0],[0,16,0,1,0,0]],16,16,pixels,depth,[255,255,255],1,map);
    assert.equal(pixels[2*16+2],packedColor(0,255,0));
    depth.fill(Infinity);map.image.data[7]=0;pixels.fill(packedColor(0,0,255));
    rasterTriangle([[0,0,0,1,0,0],[16,0,0,1,0,0],[0,16,0,1,0,0]],16,16,pixels,depth,[255,255,255],1,map);
    assert.equal(pixels[2*16+2],packedColor(0,0,255));
});
test('software textures filter between texels in linear color and clamp their edges',()=>{
    const map={image:{width:2,height:1,data:new Uint8Array([255,0,0,255,0,255,0,255])}};
    assert.deepEqual(sampleTexture(map,.5,.5),[.5,.5,0,1]);
    assert.deepEqual(sampleTexture(map,0,.5),[1,0,0,1]);
    map.wrapS=1000;assert.deepEqual(sampleTexture(map,0,.5),[.5,.5,0,1]);
});
test('SurfaceAppearance uses its PBR maps and preserves the part under overlay alpha',()=>{
    const texture=new THREE.DataTexture(new Uint8Array([255,0,0,0]),1,1);
    const maps={color:texture,normal:texture,roughness:texture,metalness:texture};
    const overlay=makeMaterial({appearance:{alphaMode:0,color:{$color3:[1,1,1]}}},null,null,maps);
    assert.equal(overlay.normalMap,texture);assert.equal(overlay.roughness,1);assert.equal(overlay.metalness,1);
    assert.equal(overlay.transparent,false);assert.equal(overlay.alphaTest,0);
    const pixels=new Uint32Array(16*16),depth=new Float32Array(16*16);depth.fill(Infinity);
    const triangle=[[0,0,0,1,0,0],[16,0,0,1,0,0],[0,16,0,1,0,0]];
    rasterTriangle(triangle,16,16,pixels,depth,[0,0,255],1,texture,{tint:[1,1,1],light:[1,1,1],overlay:true});
    assert.equal(pixels[2*16+2],packedColor(0,0,255));assert.equal(depth[2*16+2],0);
    const cutout=makeMaterial({appearance:{alphaMode:1}},null,null,maps);assert.equal(cutout.transparent,true);
    pixels.fill(0);depth.fill(Infinity);
    rasterTriangle(triangle,16,16,pixels,depth,[0,0,255],1,texture,{tint:[1,1,1],light:[1,1,1],overlay:false});
    assert.equal(pixels[2*16+2],0);assert.equal(depth[2*16+2],Infinity);
    overlay.dispose();cutout.dispose();texture.dispose();
});
test('software scene lighting respects a dark environment and colored ambient light',()=>{
    const canvas=new Canvas(),renderer=new SoftwareWorldRenderer({canvas});renderer.setSize(80,80);
    const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(60,1,.1,100);camera.position.z=5;
    const mesh=new THREE.Mesh(new THREE.BoxGeometry(2,2,2),new THREE.MeshStandardMaterial());scene.add(mesh);
    scene.userData.lighting={sky:new THREE.Color(0,0,0),ground:new THREE.Color(0,0,0),sun:new THREE.Color(0,0,0),exposure:1};
    renderer.render(scene,camera);const i=(40*80+40)*4;assert.deepEqual([...canvas.image.data.slice(i,i+3)],[0,0,0]);
    scene.userData.lighting.sky.setRGB(1,0,0);scene.userData.lighting.ground.setRGB(1,0,0);
    renderer.render(scene,camera);assert.deepEqual([...canvas.image.data.slice(i,i+3)],[255,0,0]);
    renderer.dispose();mesh.geometry.dispose();mesh.material.dispose();
});
test('visual assets deduplicate loads and contain failures without retry storms',async()=>{
    let calls=0,finish;const loaded=new Promise(resolve=>finish=resolve);
    const assets=new WorldAssets(async()=>{calls++;await loaded;throw Error('Unavailable');});
    for(let i=0;i<20;i++)assets.get('mesh','rbxassetid://1');assert.equal(calls,1);finish();
    await new Promise(resolve=>setImmediate(resolve));assert.equal(assets.stats.failed,1);assets.get('mesh','rbxassetid://1');assert.equal(calls,1);assets.dispose();
});
test('clothing faces share image memory while retaining independent UV rectangles',()=>{
    const texture=new THREE.DataTexture(new Uint8Array([255,255,255,255]),1,1);
    const front=decalMaterial(texture,1,[.2,.4,.1,.3]),back=decalMaterial(texture,1,[.2,.4,.6,.3]);
    assert.equal(front.map,texture);assert.equal(back.map,texture);
    assert.notDeepEqual(front.userData.uvRect,back.userData.uvRect);
    const shader={uniforms:{},vertexShader:'#include <common>\n#include <uv_vertex>',fragmentShader:'#include <map_fragment>'};
    const tiled=decalMaterial(texture,1,[4,3,0,0],true);tiled.onBeforeCompile(shader);
    assert.deepEqual(shader.uniforms.roveUvRect.value.toArray(),[4,3,0,0]);
    assert.match(shader.fragmentShader,/fract\(vMapUv\)/);
    front.dispose();back.dispose();tiled.dispose();texture.dispose();
});
