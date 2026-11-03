import {WebGLRenderer} from 'three';
import {SoftwareWorldRenderer} from './software-world-renderer.js';

export function createWorldRenderer({forceSoftware=false,canvasFactory=()=>document.createElement('canvas'),webGLFactory=options=>new WebGLRenderer(options)}={}) {
    if(!forceSoftware)for(const attributes of [
        {antialias:true,powerPreference:'default',alpha:false,depth:true,stencil:false,failIfMajorPerformanceCaveat:false},
        {antialias:false,powerPreference:'low-power',alpha:false,depth:true,stencil:false,failIfMajorPerformanceCaveat:false}
    ]) {
        const canvas=canvasFactory();let context;
        try {
            context=canvas.getContext('webgl2',attributes);
            if(!context||context.isContextLost())continue;
            const renderer=webGLFactory({canvas,context,...attributes});renderer.roveBackend='webgl';return renderer;
        }catch{/* The next attempt uses a new canvas and lighter settings. */}
        finally{if(context&&context.isContextLost?.())context=null;}
        // A failed constructor must not retain an allocated GPU context.
        try{context?.getExtension('WEBGL_lose_context')?.loseContext();}catch{}
    }
    return new SoftwareWorldRenderer({canvas:canvasFactory()});
}
