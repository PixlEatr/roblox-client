(() => {
    const dock=document.createElement('div');dock.id='session-attach';dock.hidden=true;
    const copy=document.createElement('div'),title=document.createElement('strong'),hint=document.createElement('small'),open=document.createElement('a');
    copy.append(title,hint);open.className='primary';open.textContent='Continue session';dock.append(copy,open);document.body.append(dock);
    async function refresh(){try{
        const response=await fetch('/api/sessions',{headers:{'X-Rove-Key':document.querySelector('meta[name="rove-key"]').content}});
        if(!response.ok)return;const data=await response.json();const session=data.sessions.find(s=>s.connected);
        dock.hidden=!session;if(!session){if(typeof state!=='undefined')state.liveSurface=null;return;}
        title.textContent='Your client is running';hint.textContent='Browser, app, and terminal share this session';
        open.href='#/workspace';
        if(typeof state!=='undefined')state.liveSurface='#/workspace';
        const existing=document.getElementById('open-client');if(existing){existing.href='#/workspace';existing.hidden=false;existing.textContent='Open workspace';}
    }catch{/* Other launcher instances can remain available during an update. */}}
    setInterval(refresh,2000);void refresh();
})();
