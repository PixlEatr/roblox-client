/* Wheel/trackpad input for the native ScrollingFrame canvas. */
(function () {
    'use strict';
    const limit=value=>Math.max(-4096,Math.min(4096,value));
    function wheelDelta(event,viewport) {
        if(!event.isTrusted||event.ctrlKey||event.metaKey||
                !Number.isFinite(event.deltaX)||!Number.isFinite(event.deltaY))return null;
        const mode=event.deltaMode||0;if(![0,1,2].includes(mode))return null;
        let x=event.deltaX,y=event.deltaY;
        if(event.shiftKey&&!x){x=y;y=0;}
        const scale=mode===1?[16,16]:mode===2?viewport:[1,1];
        if(!Array.isArray(scale)||scale.length!==2||scale.some(v=>!Number.isFinite(v)||v<0))return null;
        x=limit(x*scale[0]);y=limit(y*scale[1]);
        return x||y?[x,y]:null;
    }
    function create({surface,send,enabled=()=>true,onError=()=>{}}) {
        let metadata=new WeakMap(),queue=[],busy=false,disposed=false,generation=0;
        async function flush() {
            if(busy||disposed||!enabled()||!queue.length)return;
            busy=true;const epoch=generation,action=queue.shift();
            try{await send(action);}
            catch(error){if(epoch===generation){queue=[];onError(error);}}
            finally{busy=false;if(!disposed)void flush();}
        }
        function wheel(event) {
            if(disposed||!enabled())return;
            let element=event.target,frame;
            while(element&&element!==surface){frame=metadata.get(element);if(frame)break;element=element.parentElement;}
            if(!frame?.id||(!frame.scrollTarget&&!frame.wheelTarget))return;
            const delta=wheelDelta(event,frame.scrollViewport||frame.box?.slice(2,4));if(!delta)return;
            const rect=surface.getBoundingClientRect();
            const position=[event.clientX-rect.left,event.clientY-rect.top];
            if(position.some(v=>!Number.isFinite(v)||Math.abs(v)>32768))return;
            const steps=-Math.sign(event.deltaY);
            event.preventDefault();event.stopPropagation();
            const tail=queue[queue.length-1];
            // Preserve reversals: combining +100 and -100 would lose movement
            // when the first event hits an edge. Same-direction bursts coalesce.
            if(tail&&tail.id===frame.id&&tail.steps*steps>=0&&tail.delta.every((v,i)=>v*delta[i]>=0)&&
                    tail.position.every((v,i)=>v===position[i])&&Math.abs(tail.steps+steps)<=128) {
                tail.delta=tail.delta.map((v,i)=>limit(v+delta[i]));tail.steps+=steps;
            }else if(queue.length<32)queue.push({id:frame.id,kind:'wheel',delta,position,steps});
            void flush();
        }
        surface.addEventListener('wheel',wheel,{passive:false});
        return {
            update(element,frame){metadata.set(element,frame);element.classList.toggle('scrollable',Boolean(frame.scrollTarget||frame.wheelTarget));},
            reset(){generation++;queue=[];metadata=new WeakMap();},
            destroy(){disposed=true;generation++;queue=[];surface.removeEventListener('wheel',wheel);}
        };
    }
    window.RoveGuiScroll={wheelDelta,create};
})();
