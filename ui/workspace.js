let workspaceStarted=false;
window.addEventListener("rove:workspace", () => {
    if(workspaceStarted)return;workspaceStarted=true;
    'use strict';
    const $=id=>document.getElementById("game-"+id),key=document.querySelector('meta[name="console-key"]').content;
    const client=Array.from(crypto.getRandomValues(new Uint8Array(16)),x=>x.toString(16).padStart(2,'0')).join('');
    let renderer,connected=false,stopped=false,moving=false,moveBusy=false,moveSequence=0,resizeBusy=false;
    let lastSize='',lastPlayers='',lastTick=0,frameTime=0,typedChat=false;
    let runtimeIdentity=null;
    const held=new Set(),nodes=new Map();
    async function post(path,data,keepalive=false) {
        const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Console-Key':key},body:JSON.stringify(data),signal:keepalive?undefined:AbortSignal.timeout(path==='/api/runtime/render-asset'?20000:7000),keepalive});
        const result=await response.json();if(!response.ok||!result.ok)throw new Error(result.error||'Session request failed');return result;
    }
    try{renderer=RoveWorld.mount($('surface'),{
        forceSoftware:new URLSearchParams(location.search).get('renderer')==='software',
        loadAsset:(kind,reference)=>post('/api/runtime/render-asset',{kind,reference}),
        onBackendChange:backend=>{$('statistics').textContent=backend==='software'?'Software renderer · waiting for session':'GPU renderer · waiting for session';}
    });}catch(error){$('waiting-text').textContent='The game display could not start: '+error.message;return;}
    const controls=createKeyboardControls({surface:$('surface'),label:$('controls-state'),key,onResult:result=>{
        if(!result.ok)$('action-state').textContent=result.error;
    }});
    function resetMovement(){held.clear();if(moving)void sendMovement(true);}
    async function sendMovement(force=false) {
        if(moveBusy||!connected)return;
        const active=document.activeElement===$('surface')&&document.visibilityState==='visible';
        const x=active?Number(held.has('KeyD'))-Number(held.has('KeyA')):0;
        const z=active?Number(held.has('KeyS'))-Number(held.has('KeyW')):0;
        if(!x&&!z&&!moving&&!force)return;
        moveBusy=true;
        try{await post('/api/runtime/move',{client,sequence:++moveSequence,direction:renderer.direction(x,z)},force);moving=Boolean(x||z);}
        catch(error){moving=false;$('action-state').textContent=error.message;}
        finally{moveBusy=false;}
    }
    document.addEventListener('keydown',event=>{
        if(!event.isTrusted||!connected||document.activeElement!==$('surface'))return;
        if(event.code==='Escape'){resetMovement();return;}
        if(['KeyW','KeyA','KeyS','KeyD'].includes(event.code)){held.add(event.code);void sendMovement();}
        if(event.code==='Space'&&!event.repeat)void post('/api/runtime/jump',{action:'jump'}).catch(error=>{$('action-state').textContent=error.message;});
    });
    document.addEventListener('keyup',event=>{if(held.delete(event.code))void sendMovement();});
    $('surface').addEventListener('blur',resetMovement);window.addEventListener('blur',resetMovement);
    document.addEventListener('visibilitychange',()=>{if(document.hidden)resetMovement();});
    setInterval(()=>void sendMovement(),150);
    async function syncSize() {
        const size=[Math.round($('surface').clientWidth),Math.round($('surface').clientHeight)],serialized=JSON.stringify(size);
        if(resizeBusy||serialized===lastSize)return;resizeBusy=true;
        try{await post('/api/runtime/display',{size});lastSize=serialized;}finally{resizeBusy=false;}
    }
    const rgb=value=>{const c=value?.$color3;return Array.isArray(c)&&c.length===3?'rgb('+c.map(x=>Math.round(Math.max(0,Math.min(1,Number(x)||0))*255)).join(',')+')':'transparent';};
    const finite=(x,fallback=0)=>typeof x==='number'&&Number.isFinite(x)?x:fallback;
    async function guiAction(data){try{await post('/api/runtime/gui',data);}catch(error){$('action-state').textContent=error.message;}}
    function updateGui(frame) {
        const seen=new Set();
        for(const g of frame.gui||[]) {
            if(!Array.isArray(g.box)||g.box.length!==5||g.box.some(x=>!Number.isFinite(x)))continue;
            seen.add(g.id);let entry=nodes.get(g.id);
            if(!entry) {
                const element=document.createElement(g.class==='TextBox'?'input':g.interactive?'button':'div');element.className='game-node'+(g.interactive?' interactive':'');element.dataset.instance=g.id;element.setAttribute('aria-label',String(g.name));
                if(g.interactive&&g.class!=='TextBox')element.addEventListener('click',()=>void guiAction({id:g.id,kind:'click'}));
                if(g.class==='TextBox')element.addEventListener('change',()=>void guiAction({id:g.id,kind:'text',text:element.value.slice(0,4096)}));
                // GUI controls own their input; world movement never steals focus.
                for(const event of ['pointerdown','keydown','keyup'])element.addEventListener(event,e=>{if(g.interactive)e.stopPropagation();});
                $('game-gui').append(element);entry={element,image:null};nodes.set(g.id,entry);
            }
            const e=entry.element,[x,y,w,h,angle]=g.box;
            e.setAttribute('aria-label',String(g.text||g.name));
            e.style.left=x+'px';e.style.top=y+'px';e.style.width=w+'px';e.style.height=h+'px';e.style.transform='rotate('+angle+'deg)';
            e.style.backgroundColor=rgb(g.background);e.style.opacity=String(Math.max(0,Math.min(1,1-finite(g.backgroundTransparency))));
            // Background alpha must not fade text or children.
            e.style.opacity='1';const background=rgb(g.background);e.style.backgroundColor=background.replace('rgb(','rgba(').replace(')',','+Math.max(0,Math.min(1,1-finite(g.backgroundTransparency)))+')');
            e.style.borderRadius=Math.max(0,Math.min(w/2,h/2,finite(g.cornerRadius)))+'px';
            const stroke=g.stroke,strokeWidth=Math.max(0,Math.min(1000,finite(stroke?.thickness)))/2;
            const strokeColor=rgb(stroke?.color).replace('rgb(','rgba(').replace(')',','+Math.max(0,Math.min(1,1-finite(stroke?.transparency)))+')');
            e.style.boxShadow=strokeWidth?`0 0 0 ${strokeWidth}px ${strokeColor}, inset 0 0 0 ${strokeWidth}px ${strokeColor}`:'none';
            e.style.zIndex=String(Math.max(-100000,Math.min(100000,finite(g.displayOrder)*1000+finite(g.z,1))));
            e.style.color=rgb(g.textColor).replace('rgb(','rgba(').replace(')',','+Math.max(0,Math.min(1,1-finite(g.textTransparency)))+')');e.style.fontSize=(g.textScaled?Math.max(8,Math.min(h*.65,w/Math.max(1,String(g.text||'').length)*1.5)):Math.max(1,Math.min(200,finite(g.textSize,14))))+'px';
            if(e.tagName==='INPUT'){if(document.activeElement!==e)e.value=String(g.text||'');}
            else if(g.text){if(e.textContent!==String(g.text))e.textContent=String(g.text);}
            else if(g.image&&(/^(?:rbxassetid:\/\/)[0-9]+$/.test(g.image)||/^https:\/\/[a-z0-9.-]+\.rbxcdn\.com\//i.test(g.image))) {
                const src=g.image.startsWith('rbxassetid://')?'/api/runtime/image?id='+g.image.slice(13):g.image;
                if(!entry.image||entry.image.parentElement!==e){entry.image=document.createElement('img');entry.image.alt='';e.replaceChildren(entry.image);}if(entry.image.getAttribute('src')!==src)entry.image.src=src;
                entry.image.style.opacity=String(Math.max(0,Math.min(1,1-finite(g.imageTransparency))));
            }else if(e.textContent)e.textContent='';
        }
        for(const [id,entry] of nodes)if(!seen.has(id)){entry.element.remove();nodes.delete(id);}
    }
    function updatePlayers(players) {
        const serialized=JSON.stringify(players);if(lastPlayers===serialized)return;lastPlayers=serialized;$('player-list').replaceChildren();
        for(const player of players){const row=document.createElement('div');row.textContent=player.displayName||player.name;if(player.local){const own=document.createElement('small');own.textContent='You';row.append(own);}$('player-list').append(row);}
    }
    async function tick() {
        if(stopped)return;
        const started=performance.now();
        try{
            if(!document.hidden&&!document.getElementById('workspace-page').hidden){
                if(!connected||Date.now()-lastTick>1500){const response=await fetch('/api/runtime/status',{signal:AbortSignal.timeout(3000)});const state=await response.json();
                    if(state.session!==runtimeIdentity){runtimeIdentity=state.session;lastSize='';lastPlayers='';held.clear();moving=false;controls.setConnected(false);renderer.setMode($('camera').value);}
                    connected=state.connected===true;if(!connected)lastSize='';$('leave').disabled=!connected;controls.setConnected(connected);lastTick=Date.now();}
                if(!connected)throw new Error('Start a game from the launcher or terminal. This window attaches without rejoining.');
                await syncSize();const result=await post('/api/runtime/render',{});renderer.frame(result.frame);updateGui(result.frame);updatePlayers(result.frame.players||[]);frameTime=Date.now();
                $('waiting').hidden=true;$('connection').textContent='● Live · same session';$('connection').classList.add('live');
                $('statistics').textContent=(renderer.backend==='software'?'Software · ':'GPU · ')+renderer.fps+' fps · '+result.frame.parts.length+' parts · '+result.frame.gui.length+' UI';
                const f=result.frame,a=renderer.stats;$('render-details').textContent=(renderer.backend==='software'?'Software rendering at reduced resolution; GPU shadows are unavailable. ':'')+(renderer.reducedDetail?'Geometry budget reached; some geometry is omitted. ':'')+a.ready+' loaded assets · '+a.pending+' loading · '+a.failed+' unavailable · '+a.meshBounds+' mesh bounds · '+f.skippedParts+' omitted parts. '+(a.skinnedMeshes?a.skinnedMeshes+' meshes use their bind pose; bone deformation is not supported. ':'')+(f.terrainUnsupported?'Terrain voxels are not rendered. ':'')+'R15 clothing composition, layered clothing deformation, particles, and some GUI layouts remain unsupported.';
            }
        }catch(error){
            $('connection').textContent=connected?'Renderer unavailable':'Waiting for session';$('connection').classList.remove('live');$('waiting').hidden=false;$('waiting-text').textContent=error.message;
            if(!connected||Date.now()-frameTime>2000){for(const entry of nodes.values())entry.element.remove();nodes.clear();renderer.frame({parts:[]});}
        }finally{if(!stopped)setTimeout(tick,Math.max(100,150-(performance.now()-started)));}
    }
    $('camera').addEventListener('change',()=>renderer.setMode($('camera').value));
    $('players-toggle').onclick=()=>{$('players').hidden=!$('players').hidden;};
    $('details-toggle').onclick=()=>{$('details').hidden=!$('details').hidden;};
    $('fullscreen').onclick=()=>{if(document.fullscreenElement)void document.exitFullscreen();else void $('surface').requestFullscreen?.();};
    $('leave').onclick=()=>{$('leave-dialog').showModal();resetMovement();};$('cancel-leave').onclick=()=>$('leave-dialog').close();
    $('confirm-leave').onclick=async()=>{const button=$('confirm-leave');button.disabled=true;try{await post('/api/runtime/leave',{});connected=false;controls.setConnected(false);$('leave-dialog').close();$('waiting').hidden=false;$('waiting-text').textContent='Session ended. You can join another game from the launcher.';}catch(error){$('action-state').textContent=error.message;$('leave-dialog').close();$('details').hidden=false;}finally{button.disabled=false;}};
    createChatControls({input:$('chat-text'),button:$('chat-form').querySelector('button'),form:$('chat-form'),channel:$('chat-channel'),
        available:()=>connected,submit:data=>post('/api/runtime/user-chat',data),
        onResult:()=>$('chat-state').textContent='Submitted · waiting for server delivery',
        onError:message=>$('chat-state').textContent=message});
    window.addEventListener('pagehide',()=>{resetMovement();stopped=true;controls.destroy();renderer.dispose();});
    $('scripts-toggle').onclick=()=>{const panel=document.getElementById('script-workspace');panel.hidden=!panel.hidden;$('scripts-toggle').setAttribute('aria-expanded',String(!panel.hidden));window.dispatchEvent(new Event('resize'));};
    window.addEventListener('hashchange',()=>{resetMovement();controls.setConnected(false);lastSize='';});
    void tick();
});
