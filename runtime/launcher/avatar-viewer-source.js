import * as THREE from 'three';
import {OBJLoader} from 'three/addons/loaders/OBJLoader.js';
import {MTLLoader} from 'three/addons/loaders/MTLLoader.js';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
export async function mount(container,model){
 const valid=url=>{const u=new URL(url);if(u.protocol!=='https:'||!u.hostname.endsWith('.rbxcdn.com'))throw new Error('Invalid avatar resource');const hash=u.pathname.slice(1);if(/^(?:[0-9A-Z]+-)?[a-f0-9]{32,64}$/.test(hash)){let bucket=31;for(const character of hash)bucket^=character.charCodeAt(0);return 'https://t'+(bucket%8)+'.rbxcdn.com/'+hash;}return url;};
 const renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.outputColorSpace=THREE.SRGBColorSpace;
 try{
 const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(35,1,.01,1000);
 scene.add(new THREE.HemisphereLight(0xffffff,0xbac3cf,2.4));const light=new THREE.DirectionalLight(0xffffff,2.2);light.position.set(4,8,6);scene.add(light);
 const manager=new THREE.LoadingManager();manager.setURLModifier(valid);
 const materials=await new MTLLoader(manager).setResourcePath('https://t0.rbxcdn.com/').loadAsync(valid(model.mtl));let finishTextures;const texturesReady=new Promise(resolve=>{finishTextures=resolve;});manager.onLoad=()=>finishTextures();materials.preload();
 const object=await new OBJLoader(manager).setMaterials(materials).loadAsync(valid(model.obj));await texturesReady;
 const bounds=new THREE.Box3().setFromObject(object),center=bounds.getCenter(new THREE.Vector3()),size=bounds.getSize(new THREE.Vector3());object.position.sub(center);scene.add(object);
 const extent=Math.max(size.x,size.y,size.z),distance=extent*1.65;
 if(model.camera?.position){const p=model.camera.position;camera.position.set(p.x-center.x,p.y-center.y,p.z-center.z);}else camera.position.set(0,size.y*.12,distance);
 if(camera.position.length()<extent*.8||camera.position.length()>extent*5)camera.position.set(0,size.y*.12,distance);
 const controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.enablePan=false;controls.minDistance=extent*.7;controls.maxDistance=extent*4;controls.target.set(0,0,0);controls.update();
 container.replaceChildren(renderer.domElement);renderer.domElement.setAttribute('aria-label','Interactive 3D avatar. Drag to rotate and scroll to zoom.');renderer.domElement.setAttribute('role','img');
 const resize=()=>{const width=container.clientWidth,height=container.clientHeight;renderer.setSize(width,height,false);camera.aspect=width/Math.max(height,1);camera.updateProjectionMatrix();};const observer=new ResizeObserver(resize);observer.observe(container);resize();
 let destroyed=false;renderer.setAnimationLoop(()=>{if(!destroyed){controls.update();renderer.render(scene,camera);}});
 return ()=>{destroyed=true;observer.disconnect();controls.dispose();renderer.setAnimationLoop(null);object.traverse(mesh=>{mesh.geometry?.dispose();if(mesh.material){const mats=Array.isArray(mesh.material)?mesh.material:[mesh.material];mats.forEach(m=>{m.map?.dispose();m.dispose();});}});renderer.dispose();renderer.forceContextLoss();renderer.domElement.remove();};
 }catch(error){renderer.dispose();renderer.forceContextLoss();renderer.domElement.remove();throw error;}
}
