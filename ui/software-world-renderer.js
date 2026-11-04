import {Matrix4, Matrix3, Color, Vector3} from 'three';

// Clip in homogeneous space before perspective division. This keeps large
// ground planes and triangles crossing the camera from exploding on screen.
export function clipTriangle(vertices) {
    let polygon=vertices;
    for(let plane=0;plane<6&&polygon.length;plane++) {
        const axis=plane>>1,sign=plane%2? -1:1;
        const distance=v=>v[3]+sign*v[axis];
        const next=[];let previous=polygon.at(-1),a=distance(previous);
        for(const current of polygon) {
            const b=distance(current);
            if((a>=0)!==(b>=0)) {
                const t=a/(a-b);next.push(previous.map((value,i)=>value+(current[i]-value)*t));
            }
            if(b>=0)next.push(current);
            previous=current;a=b;
        }
        polygon=next;
    }
    return polygon;
}

const littleEndian=new Uint8Array(new Uint32Array([1]).buffer)[0]===1;
export const packedColor=(r,g,b)=>littleEndian?(255<<24|b<<16|g<<8|r)>>>0:(r<<24|g<<16|b<<8|255)>>>0;
const channel=value=>Math.round(Math.max(0,Math.min(1,value<=.0031308?12.92*value:1.055*value**(1/2.4)-.055))*255);
const linear=Float32Array.from({length:256},(_,i)=>{const v=i/255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;});

export function sampleTexture(texture,u,v,out=[0,0,0,0]) {
    const {data,width,height}=texture.image;
    u=u*(texture.repeat?.x??1)+(texture.offset?.x??0);v=v*(texture.repeat?.y??1)+(texture.offset?.y??0);
    if(texture.flipY)v=1-v;
    const x=u*width-.5,y=v*height-.5,x0=Math.floor(x),y0=Math.floor(y),fx=x-x0,fy=y-y0;
    const coord=(n,size,wrap)=>wrap===1000?((n%size)+size)%size:Math.max(0,Math.min(size-1,n));
    out.fill(0);
    for(let dy=0;dy<2;dy++)for(let dx=0;dx<2;dx++){
        const i=(coord(y0+dy,height,texture.wrapT)*width+coord(x0+dx,width,texture.wrapS))*4,weight=(dx?fx:1-fx)*(dy?fy:1-fy);
        for(let c=0;c<3;c++)out[c]+=linear[data[i+c]]*weight;
        out[3]+=data[i+3]/255*weight;
    }
    return out;
}

// A small depth-buffer rasterizer, independent of WebGL. The Canvas 2D surface
// only presents the finished pixels; nearer triangles win regardless of order.
export function rasterTriangle(vertices,width,height,pixels,depth,color,opacity=1,texture=null,surface=null) {
    const [a,b,c]=vertices;
    const area=(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
    if(!Number.isFinite(area)||Math.abs(area)<1e-8)return;
    const minX=Math.max(0,Math.floor(Math.min(a[0],b[0],c[0]))),maxX=Math.min(width-1,Math.ceil(Math.max(a[0],b[0],c[0])));
    const minY=Math.max(0,Math.floor(Math.min(a[1],b[1],c[1]))),maxY=Math.min(height-1,Math.ceil(Math.max(a[1],b[1],c[1])));
    if(minX>maxX||minY>maxY)return;
    const inverse=1/area;
    const stepA=(b[1]-c[1])*inverse,stepB=(c[1]-a[1])*inverse;
    const packed=packedColor(...color);
    const texel=[0,0,0,0],base=color.map(v=>linear[v]);
    for(let y=minY;y<=maxY;y++) {
        const py=y+.5,px=minX+.5;
        let wa=((b[0]-px)*(c[1]-py)-(b[1]-py)*(c[0]-px))*inverse;
        let wb=((c[0]-px)*(a[1]-py)-(c[1]-py)*(a[0]-px))*inverse;
        for(let x=minX;x<=maxX;x++,wa+=stepA,wb+=stepB) {
            const wc=1-wa-wb;if(wa< -1e-7||wb< -1e-7||wc< -1e-7)continue;
            const z=wa*a[2]+wb*b[2]+wc*c[2],index=y*width+x;
            if(z< -1||z>1||z>=depth[index])continue;
            let shade=color,alpha=opacity,output=packed;
            if(texture?.image?.data&&a.length>=6){
                const reciprocal=wa*a[3]+wb*b[3]+wc*c[3];if(Math.abs(reciprocal)<1e-12)continue;
                let u=(wa*a[4]+wb*b[4]+wc*c[4])/reciprocal,v=(wa*a[5]+wb*b[5]+wc*c[5])/reciprocal;
                sampleTexture(texture,u,v,texel);
                if(!surface?.overlay)alpha*=texel[3];if(alpha<.02)continue;
                shade=base.map((value,c)=>{
                    if(!surface)return channel(value*texel[c]);
                    const mapped=texel[c]*surface.tint[c]*surface.light[c];
                    return channel(surface.overlay?value*(1-texel[3])+mapped*texel[3]:mapped);
                });output=packedColor(...shade);
            }
            if(alpha>=.999){pixels[index]=output;depth[index]=z;}
            else {
                const old=pixels[index],r=littleEndian?old&255:old>>>24,g=littleEndian?old>>>8&255:old>>>16&255,b=littleEndian?old>>>16&255:old>>>8&255;
                pixels[index]=packedColor(Math.round(r+(shade[0]-r)*alpha),Math.round(g+(shade[1]-g)*alpha),Math.round(b+(shade[2]-b)*alpha));
            }
        }
    }
}

export class SoftwareWorldRenderer {
    constructor({canvas=document.createElement('canvas')}={}) {
        this.domElement=canvas;this.context=canvas.getContext('2d',{alpha:false});
        if(!this.context)throw new Error('Neither WebGL 2 nor Canvas 2D is available');
        this.roveBackend='software';this.width=0;this.height=0;this.disposed=false;this.loop=null;this.animation=null;
        this.instance=new Matrix4();this.model=new Matrix4();this.mvp=new Matrix4();this.viewProjection=new Matrix4();
        this.normal=new Matrix3();this.color=new Color();this.direction=new Vector3();
        this.light=new Vector3(.3,.8,.45).normalize();
    }
    setPixelRatio() {} // Deliberately bounded resolution on the CPU path.
    setSize(width,height) {
        const scale=Math.min(1,640/Math.max(width,1),480/Math.max(height,1));
        this.width=Math.max(1,Math.round(width*scale));this.height=Math.max(1,Math.round(height*scale));
        this.domElement.width=this.width;this.domElement.height=this.height;
        this.image=this.context.createImageData(this.width,this.height);
        this.pixels=new Uint32Array(this.image.data.buffer);this.depth=new Float32Array(this.width*this.height);
    }
    setAnimationLoop(callback) {
        if(this.animation!==null)cancelAnimationFrame(this.animation);
        this.animation=null;this.loop=callback;
        if(!callback||this.disposed)return;
        let previous=-Infinity;
        const tick=time=>{
            if(this.disposed||this.loop!==callback)return;
            // Limit CPU rendering to 24 fps; input and the GUI remain separate.
            if(time-previous>=1000/24){previous=time;callback(time);}
            if(!this.disposed&&this.loop===callback)this.animation=requestAnimationFrame(tick);
        };
        this.animation=requestAnimationFrame(tick);
    }
    render(scene,camera) {
        if(this.disposed||!this.pixels)return;
        const bg=scene.background;
        this.pixels.fill(bg?.isColor?packedColor(channel(bg.r),channel(bg.g),channel(bg.b)):packedColor(168,198,220));this.depth.fill(Infinity);
        if(scene.userData.sky){const {top,horizon}=scene.userData.sky;
            for(let y=0;y<this.height;y++){const t=Math.min(1,y/this.height*1.7),c=top.clone().lerp(horizon,t);this.pixels.fill(packedColor(channel(c.r),channel(c.g),channel(c.b)),y*this.width,(y+1)*this.width);}}
        if(scene.userData.sunDirection)this.light.copy(scene.userData.sunDirection);
        scene.updateMatrixWorld();camera.updateMatrixWorld();
        this.viewProjection.multiplyMatrices(camera.projectionMatrix,camera.matrixWorldInverse);
        const opaque=[],transparent=[];
        scene.traverse(object=>{if(object.isMesh&&object.visible&&object.geometry){const material=Array.isArray(object.material)?object.material[0]:object.material;(material?.transparent?transparent:opaque).push(object);}});
        let triangles=0;this.truncated=false;
        objects:for(const object of [...opaque,...transparent]) {
            const geometry=object.geometry,positions=geometry.getAttribute('position'),normals=geometry.getAttribute('normal'),uv=geometry.getAttribute('uv'),indices=geometry.index;
            if(!positions)continue;
            const material=Array.isArray(object.material)?object.material[0]:object.material;
            let samplingMap=material.map;
            if(samplingMap&&material.userData?.uvRect){const [x,y,u,v]=material.userData.uvRect;
                samplingMap={image:samplingMap.image,flipY:samplingMap.flipY,repeat:{x,y},offset:{x:u,y:v},wrapS:material.userData.repeatTexture?1000:samplingMap.wrapS,wrapT:material.userData.repeatTexture?1000:samplingMap.wrapT};}
            const count=object.isInstancedMesh?object.count:1;
            for(let instance=0;instance<count;instance++) {
                if(object.isInstancedMesh){object.getMatrixAt(instance,this.instance);this.model.multiplyMatrices(object.matrixWorld,this.instance);}else this.model.copy(object.matrixWorld);
                this.mvp.multiplyMatrices(this.viewProjection,this.model);this.normal.getNormalMatrix(this.model);
                if(object.isInstancedMesh&&object.instanceColor)object.getColorAt(instance,this.color);else this.color.copy(material?.color||new Color(.64,.64,.64));
                const tile=material.userData?.tileSize,me=this.model.elements;
                const sx=Math.hypot(me[0],me[1],me[2]),sy=Math.hypot(me[4],me[5],me[6]),sz=Math.hypot(me[8],me[9],me[10]);
                const m=this.mvp.elements,vertices=new Array(positions.count);
                for(let i=0;i<positions.count;i++) {
                    const x=positions.getX(i),y=positions.getY(i),z=positions.getZ(i);
                    vertices[i]=[m[0]*x+m[4]*y+m[8]*z+m[12],m[1]*x+m[5]*y+m[9]*z+m[13],m[2]*x+m[6]*y+m[10]*z+m[14],m[3]*x+m[7]*y+m[11]*z+m[15],uv?.getX(i)||0,uv?.getY(i)||0];
                    if(tile&&normals){const nx=Math.abs(normals.getX(i)),ny=Math.abs(normals.getY(i)),nz=Math.abs(normals.getZ(i));vertices[i][4]=(ny>=nx&&ny>=nz?x*sx:nx>=nz?z*sz:x*sx)/tile;vertices[i][5]=(ny>=nx&&ny>=nz?z*sz:y*sy)/tile;}
                }
                for(let i=0,length=indices?indices.count:positions.count;i+2<length;i+=3) {
                    if(++triangles>50000){this.truncated=true;break objects;}
                    const a=indices?indices.getX(i):i,b=indices?indices.getX(i+1):i+1,c=indices?indices.getX(i+2):i+2;
                    const clipped=clipTriangle([vertices[a],vertices[b],vertices[c]]);if(clipped.length<3)continue;
                    this.direction.set(0,1,0);
                    if(normals)this.direction.set(normals.getX(a)+normals.getX(b)+normals.getX(c),normals.getY(a)+normals.getY(b)+normals.getY(c),normals.getZ(a)+normals.getZ(b)+normals.getZ(c)).applyMatrix3(this.normal).normalize();
                    const lambert=Math.max(0,this.direction.dot(this.light)),lighting=scene.userData.lighting,hemi=(this.direction.y+1)/2;
                    const light=lighting?['r','g','b'].map(c=>(lighting.ground[c]*(1-hemi)+lighting.sky[c]*hemi+lighting.sun[c]*lambert/Math.PI)*lighting.exposure):[1,1,1].map(()=>.5+.5*lambert);
                    if(material.emissiveIntensity&&material.emissive?.r+material.emissive?.g+material.emissive?.b>0)for(let c=0;c<3;c++)light[c]+=material.emissiveIntensity;
                    const color=[channel(this.color.r*light[0]),channel(this.color.g*light[1]),channel(this.color.b*light[2])];
                    const appearance=material.userData?.surfaceAppearance,surface=appearance?{tint:appearance.tint.toArray(),overlay:appearance.overlay,light}:null;
                    const projected=clipped.map(v=>[(v[0]/v[3]*.5+.5)*this.width,(-v[1]/v[3]*.5+.5)*this.height,v[2]/v[3],1/v[3],v[4]/v[3],v[5]/v[3]]);
                    for(let triangle=1;triangle+1<projected.length;triangle++)rasterTriangle([projected[0],projected[triangle],projected[triangle+1]],this.width,this.height,this.pixels,this.depth,color,material?.opacity??1,samplingMap,surface);
                }
            }
        }
        this.context.putImageData(this.image,0,0);
    }
    dispose(){this.disposed=true;this.setAnimationLoop(null);this.image=null;this.pixels=null;this.depth=null;}
}
