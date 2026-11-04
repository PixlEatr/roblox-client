/* ImageLabel and ImageButton sampling in the workspace surface. */
window.RoveImage=(()=>{
    const namespace='http://www.w3.org/2000/svg';
    let serial=0;
    const clamp=(value,min,max)=>Math.min(max,Math.max(min,value));
    const finite=(value,fallback)=>Number.isFinite(value)?value:fallback;
    function svg(tag,attributes={}){
        const e=document.createElementNS(namespace,tag);
        for(const [name,value] of Object.entries(attributes))e.setAttribute(name,String(value));
        return e;
    }
    function sourceUrl(value){
        if(typeof value!=='string')return '';
        if(/^rbxassetid:\/\/[0-9]+$/.test(value))return '/api/runtime/image?id='+value.slice(13);
        return /^https:\/\/[a-z0-9.-]+\.rbxcdn\.com\//i.test(value)?value:'';
    }
    function sliceAxis(source,start,end,target,scale){
        const left=start*scale,right=(source-end)*scale;
        const split=left+right>target?target*left/(left+right):null;
        return {source:[0,start,end,source],target:[0,split??left,split??(target-right),target]};
    }
    function layout(g,naturalWidth,naturalHeight){
        const [, ,width,height]=g.box;
        const [rw,rh]=g.imageRect?.$vector2||[0,0];
        const crop=rw>0&&rh>0;
        const [rawX,rawY]=g.imageOffset?.$vector2||[0,0];
        const source=crop?[finite(rawX,0),finite(rawY,0),rw,rh]:[0,0,naturalWidth,naturalHeight];
        const [sx,sy,sw,sh]=source;
        if(g.imageMode===2){
            const [tw,th]=g.imageTile||[width,height];
            const tile=[Math.max(1,finite(tw,width)),Math.max(1,finite(th,height))];
            return {tile,regions:[{source,target:[0,0,...tile]}]};
        }
        const slice=g.imageSlice?.$rect;
        if(g.imageMode===1&&slice){
            const [left,top]=slice[0]?.$vector2||[0,0], [right,bottom]=slice[1]?.$vector2||[0,0];
            const x0=clamp(finite(left,0),0,sw),x1=clamp(finite(right,0),0,sw);
            const y0=clamp(finite(top,0),0,sh),y1=clamp(finite(bottom,0),0,sh);
            if(x1>x0&&y1>y0){
                const scale=clamp(finite(g.imageSliceScale,1),0,1000);
                const x=sliceAxis(sw,x0,x1,width,scale),y=sliceAxis(sh,y0,y1,height,scale);
                const regions=[];
                for(let row=0;row<3;row++)for(let col=0;col<3;col++){
                    const src=[sx+x.source[col],sy+y.source[row],x.source[col+1]-x.source[col],y.source[row+1]-y.source[row]];
                    const target=[x.target[col],y.target[row],x.target[col+1]-x.target[col],y.target[row+1]-y.target[row]];
                    if(src[2]>0&&src[3]>0&&target[2]>0&&target[3]>0)regions.push({source:src,target});
                }
                return {regions};
            }
        }
        if(g.imageMode===3||g.imageMode===4){
            const ratio=(g.imageMode===3?Math.min:Math.max)(width/sw,height/sh);
            const w=sw*ratio,h=sh*ratio;
            return {regions:[{source,target:[(width-w)/2,(height-h)/2,w,h]}]};
        }
        return {regions:[{source,target:[0,0,width,height]}]};
    }
    function render(entry,request){
        const state=entry.image;
        if(state?.variants[request.role]!==request||!request.loader.naturalWidth||!request.loader.naturalHeight)return;
        const g=state.frame;
        const [, ,width,height]=g.box;
        const signature=JSON.stringify([width,height,g.imageMode,g.imageRect,g.imageOffset,g.imageTile,
            g.imageSlice,g.imageSliceScale,g.imageColor,g.imageTransparency,g.imagePixelated,g.backgroundGradient]);
        if(request.signature===signature)return;
        request.signature=signature;
        const id='rove-image-'+(++serial);
        const root=svg('svg',{viewBox:`0 0 ${width} ${height}`,preserveAspectRatio:'none','aria-hidden':true,class:'gui-image'});
        root.style.opacity=String(1-clamp(finite(g.imageTransparency,0),0,1));
        const defs=svg('defs');
        const picture=svg('image',{id,href:request.src,width:request.loader.naturalWidth,
            height:request.loader.naturalHeight,preserveAspectRatio:'none'});
        if(g.imagePixelated)picture.style.imageRendering='pixelated';
        defs.append(picture);root.append(defs);
        const group=svg('g');
        const tint=(g.imageColor?.$color3||[1,1,1]).map(value=>clamp(finite(value,1),0,1));
        if(g.backgroundGradient){
            group.setAttribute('filter',state.gradientFilter(root,g.backgroundGradient,width,height,{$color3:tint}));
        }else if(tint.some(value=>value!==1)){
            const filter=svg('filter',{id:id+'-tint','color-interpolation-filters':'sRGB'});
            filter.append(svg('feColorMatrix',{type:'matrix',values:`${tint[0]} 0 0 0 0  0 ${tint[1]} 0 0 0  0 0 ${tint[2]} 0 0  0 0 0 1 0`}));
            defs.append(filter);group.setAttribute('filter','url(#'+id+'-tint)');
        }
        const {regions,tile}=layout(g,request.loader.naturalWidth,request.loader.naturalHeight);
        let target=group;
        if(tile){
            target=svg('pattern',{id:id+'-tile',patternUnits:'userSpaceOnUse',width:tile[0],height:tile[1]});
            defs.append(target);
            group.append(svg('rect',{width,height,fill:'url(#'+id+'-tile)'}));
        }
        for(const region of regions){
            const [x,y,w,h]=region.target;
            const patch=svg('svg',{x,y,width:w,height:h,viewBox:region.source.join(' '),preserveAspectRatio:'none',overflow:'hidden'});
            patch.append(svg('use',{href:'#'+id}));
            target.append(patch);
        }
        root.append(group);
        if(!request.layer){
            request.layer=document.createElement('span');
            request.layer.className='gui-image-layer gui-image-'+request.role;
            entry.element.append(request.layer);
        }
        request.layer.replaceChildren(root);
    }
    function reportLoad(entry,request){
        const state=entry.image;
        if(request.role!=='base'||state?.variants.base!==request||!request.signature||!request.token||
            state.frame.imageLoaded||!state.sendLoaded||request.sent||request.attempts>=3||Date.now()<request.retryAt)return;
        request.sent=true;
        request.attempts++;
        void state.sendLoaded({kind:'image_loaded',id:state.frame.id,token:request.token}).then(ok=>{
            if(ok)return;
            request.sent=false;
            request.retryAt=Date.now()+250*request.attempts;
        });
    }
    function clear(entry){
        if(!entry.image)return;
        entry.image=null;
        entry.element.replaceChildren();
    }
    function apply(entry,g,gradientFilter,sendLoaded){
        const sources={base:sourceUrl(g.image),hover:sourceUrl(g.imageHover),pressed:sourceUrl(g.imagePressed)};
        if(!Object.values(sources).some(Boolean)){clear(entry);return;}
        if(!entry.image){
            entry.element.replaceChildren();
            entry.image={variants:{},gradientFilter,sendLoaded};
        }
        const state=entry.image;
        state.frame=g;
        for(const [role,src] of Object.entries(sources)){
            const token=role==='base'?g.imageLoadToken:undefined;
            let request=state.variants[role];
            if(request&&(request.src!==src||request.token!==token)){
                request.layer?.remove();
                delete state.variants[role];
                request=null;
            }
            if(!src)continue;
            if(!request){
                const loader=document.createElement('img');
                request={role,src,token,loader,attempts:0,retryAt:0,sent:false};
                state.variants[role]=request;
                loader.addEventListener('load',()=>{
                    render(entry,request);
                    reportLoad(entry,request);
                },{once:true});
                loader.src=src;
            }
            render(entry,request);
            reportLoad(entry,request);
        }
    }
    return {apply,clear,layout};
})();
