/* Ancestor clips use display coordinates; CSS clip paths use the child's
 * unrotated border box. Inverse rotation keeps drawing and browser hit testing
 * on the same polygon without inserting wrappers that change stacking order. */
(function () {
    'use strict';
    function clipping(box, clip) {
        if (!Array.isArray(clip)) return {path:'', visible:true};
        if (clip.length!==4 || clip.some(v=>!Number.isFinite(v)))
            return {path:'polygon(0px 0px, 0px 0px, 0px 0px)', visible:false};
        const [x,y,w,h,angle]=box, [left,top,width,height]=clip;
        const radians=-angle*Math.PI/180, c=Math.cos(radians), s=Math.sin(radians);
        const local=([px,py])=>{
            const dx=px-x-w/2,dy=py-y-h/2;
            return [dx*c-dy*s+w/2,dx*s+dy*c+h/2];
        };
        const points=[[left,top],[left+width,top],[left+width,top+height],[left,top+height]].map(local);
        // Intersect with the object's box only to decide visibility/focus. Keep
        // the original polygon for drawing so an in-bounds UIStroke survives.
        let visible=points;
        for(const [axis,bound,sign] of [[0,0,1],[0,w,-1],[1,0,1],[1,h,-1]]) {
            const result=[];
            for(let i=0;i<visible.length;i++) {
                const a=visible[i],b=visible[(i+1)%visible.length];
                const da=(a[axis]-bound)*sign,db=(b[axis]-bound)*sign;
                if(da>=0)result.push(a);
                if((da>=0)!==(db>=0)) {
                    const t=da/(da-db);
                    result.push([a[0]+t*(b[0]-a[0]),a[1]+t*(b[1]-a[1])]);
                }
            }
            visible=result;
        }
        let area=0;
        for(let i=0;i<visible.length;i++) {
            const a=visible[i],b=visible[(i+1)%visible.length];area+=a[0]*b[1]-b[0]*a[1];
        }
        return {path:'polygon('+points.map(p=>p.map(v=>`${v}px`).join(' ')).join(', ')+')',
            visible:width>0&&height>0&&Math.abs(area)>1e-8};
    }
    function apply(element,frame) {
        const result=clipping(frame.box,frame.clip);
        element.style.clipPath=result.path;
        element.style.visibility=result.visible?'':'hidden';
        element.inert=!result.visible;
        if(!result.visible && element===document.activeElement)element.blur();
    }
    window.RoveGuiClip={clipping,apply};
})();
