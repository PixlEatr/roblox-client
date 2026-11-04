'use strict';
const $=id=>document.getElementById(id), key=document.querySelector('meta[name="rove-key"]').content;
const state={account:null,friends:[],games:[],favorites:[],recent:[],view:'home',filter:'all',query:'',selected:null,session:null};
const compact=n=>typeof n==='number'?Intl.NumberFormat('en',{notation:'compact',maximumFractionDigits:1}).format(n):'—';
function element(tag,cls,text){const el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=text;return el;}
function avatar(user,small=false){const el=element('span','avatar'+(small?' small':''));if(user.avatar){const img=element('img');img.src=user.avatar;img.alt='';img.loading='lazy';el.append(img);}else el.textContent=(user.displayName||user.name||'?').slice(0,1).toUpperCase();return el;}
async function api(path,body){const options={headers:{'X-Rove-Key':key}};if(body!==undefined){options.method='POST';options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}const response=await fetch(path,options);const data=await response.json();if(!response.ok){const error=new Error(data.error||'Could not complete this request.');error.status=response.status;throw error;}return data;}
let toastTimer;function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,5500);}
function banner(id,message){$(id).hidden=!message;$(id).textContent=message||'';}
function setView(view){
 $('workspace-page').hidden=view!=='workspace';document.body.classList.toggle('workspace-open',view==='workspace');
 if(view==='workspace')window.dispatchEvent(new Event('rove:workspace'));
 state.view=view;state.query='';state.filter='all';state.serverSearch=null;
 if(view!=='avatar'&&typeof avatarRender!=='undefined')avatarRender++;
 if(view!=='avatar'&&typeof avatarDestroy!=='undefined'&&avatarDestroy){avatarDestroy();avatarDestroy=null;}
 $('avatar-page').hidden=view!=='avatar';$('account-pages').hidden=!['messages','inventory','trade','communities','shop'].includes(view);$('search').value='';
 document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
 const title={workspace:'Workspace',home:'Home',discover:'Charts',friends:'Connections',favorites:'Favorites',profile:'Profile',avatar:'Avatar Editor',messages:'Messages',inventory:'Inventory',trade:'Trade',communities:'Communities',shop:'Marketplace'}[view];
 $('breadcrumb').textContent=title;document.title=title+' · Rove';$('greeting').textContent=title;
 $('home-hero').hidden=true;$('connections-section').hidden=view!=='home';$('friends-page').hidden=view!=='friends';$('profile-page').hidden=view!=='profile';document.querySelector('.page-heading').hidden=['profile','workspace'].includes(view);
 $('games-section').hidden=['workspace','friends','profile','avatar','messages','inventory','trade','communities','shop'].includes(view);$('join-strip').hidden=true;
 $('all-friends').hidden=view==='friends';$('friends-row').classList.toggle('friends-grid',view==='friends');
 renderFriends();renderGames();window.scrollTo({top:0,behavior:'smooth'});
}
function renderFriends(){const row=$('friends-row');row.replaceChildren();$('connections-count').textContent=state.friends.length;$('friend-badge').textContent=state.friends.length;const list=state.view==='friends'?state.friends:state.friends.slice(0,9);if(!list.length){const empty=element('div','empty-friends');empty.append(element('span','','♧'),element('strong','','Your next adventure is better together.'),element('span','','Your Roblox connections will appear here.'));row.append(empty);return;}for(const friend of list){const button=element('button','friend');const holder=element('div','friend-avatar');holder.append(avatar(friend));let presence=friend.presence;holder.append(element('span','presence-dot'+(presence===2?' ingame':presence>0?' online':'')));button.append(holder,element('strong','',friend.displayName),element('small','',presence===2?(friend.location||'In an experience'):presence===1?'Online':presence===3?'In Studio':presence===0?'Offline':'Status unavailable'));button.title=friend.displayName+(presence===2?' · '+friend.location:'');button.addEventListener('click',()=>navigateProfile(friend.id));row.append(button);}}
function gameCard(game){
 const card=element('button','game-card'),cover=element('div','game-cover');
 if(game.image){const img=element('img');img.src=game.image;img.alt=game.name;img.loading='lazy';cover.append(img);}
 else cover.append(element('span','placeholder',game.name.slice(0,1)));
 cover.append(element('span','cover-play','▶'));
 const meta=element('div','game-meta');
 if(game.votePercent!==null&&game.votePercent!==undefined){const vote=element('span','game-stat');vote.append(element('span','rbx-stat rbx-votes'),document.createTextNode(game.votePercent+'%'));meta.append(vote);}
 if(game.playing!==null&&game.playing!==undefined){const playing=element('span','game-stat');playing.append(element('span','rbx-stat rbx-playing'),document.createTextNode(compact(game.playing)));meta.append(playing);}
 card.append(cover,element('h3','',game.name),meta);card.addEventListener('click',()=>openGame(game));return card;
}
function renderGames(){
 const grid=$('games-grid');grid.replaceChildren();let games=state.serverSearch||state.games;
 if(state.view==='home'&&!state.query&&state.recommendationIds){const byId=new Map(games.map(g=>[g.placeId,g]));games=state.recommendationIds.map(id=>byId.get(id)).filter(Boolean);}
 if(state.view==='favorites')games=games.filter(g=>state.favorites.includes(g.placeId));
 if(state.filter==='recent')games=games.filter(g=>state.recent.includes(g.placeId));
 if(state.query&&!state.serverSearch)games=games.filter(g=>(g.name+' '+g.creator).toLowerCase().includes(state.query.toLowerCase()));
 $('games-title').textContent=state.query?'Search results':state.view==='favorites'?'Favorites':state.filter==='recent'?'Continue':'Recommended For You';
 $('games-subtitle').textContent='';$('games-empty').hidden=games.length>0;
 for(const game of games)grid.append(gameCard(game));
 const recent=state.games.filter(g=>state.recent.includes(g.placeId));
 const continuing=recent.length?recent:state.games.filter(g=>state.favorites.includes(g.placeId));
 $('continue-title').textContent=recent.length?'Continue':'Favorites';$('continue-grid').replaceChildren(...continuing.map(gameCard));
 $('continue-section').hidden=state.view!=='home'||state.query!==''||continuing.length===0;
 document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('active',b.dataset.filter===state.filter));
}
function openGame(game){state.selected=game;$('modal-title').textContent=game.name;$('modal-creator').textContent=game.creator?'By '+game.creator:'Roblox experience';$('modal-playing').textContent=compact(game.playing);$('modal-visits').textContent=compact(game.visits);$('modal-capacity').textContent=game.maxPlayers||'—';$('modal-description').textContent=game.description||'An experience is waiting. Join with your saved account in the local client.';$('modal-image').replaceChildren();if(game.image){const img=element('img');img.src=game.image;img.alt='';$('modal-image').append(img);}banner('modal-error',game.playable?null:'This experience is not currently playable by your account.');$('play-game').disabled=!game.playable;$('play-game').replaceChildren(element('span','rbx-icon rbx-play'),document.createTextNode(game.playable?'Play':'Unavailable'));if(!$('game-dialog').open)$('game-dialog').showModal();}
async function lookup(value){try{const game=await api('/api/game?place='+encodeURIComponent(value));if(!state.games.some(x=>x.placeId===game.placeId))state.games.unshift(game);$('join-dialog').close();openGame(game);}catch(error){if($('join-dialog').open)banner('join-error',error.message);else toast(error.message);}}
function openJoin(){banner('join-error',null);$('join-target').value='';$('join-dialog').showModal();setTimeout(()=>$('join-target').focus(),40);}
async function play(target,game){try{const result=await api('/api/play',{target,game:game||{}});$('game-dialog').close();$('join-dialog').close();renderSession(result);navigate('workspace');toast('Connecting to your experience…');}catch(error){if($('game-dialog').open)banner('modal-error',error.message);else if($('join-dialog').open)banner('join-error',error.message);else toast(error.message);}}
function renderSession(session){state.session=session;$('session-card').dataset.state=session.state;const visible=session.state!=='idle';$('session-card').hidden=!visible;if(!visible)return;$('session-name').textContent=session.game?.name||'Your experience';const labels={preparing:'Checking access and preparing your session…',joining:'Joining the game server…',playing:'Connected · Character spawned',leaving:'Leaving your session…',error:'Could not join'};$('session-progress').textContent=session.error||session.events?.at(-1)||labels[session.state];$('leave').textContent=session.state==='error'?'Dismiss':'Leave';$('leave').disabled=session.state==='leaving';const surface=state.liveSurface||(session.state==='playing'?'#/workspace':null);$('open-client').hidden=!surface;if(surface)$('open-client').href=surface;}
async function load(){const refresh=$('refresh');refresh.disabled=true;banner('account-error',null);try{state.account=await api('/api/account');$('header-name').textContent=state.account.name;$('header-avatar').replaceWith(Object.assign(avatar(state.account,true),{id:'header-avatar'}));$('sidebar-avatar').replaceWith(Object.assign(avatar(state.account,true),{id:'sidebar-avatar'}));$('sidebar-name').textContent=state.account.name;setView(state.view);}catch(error){banner('account-error',error.message);$('header-name').textContent='Account unavailable';refresh.disabled=false;return;}await Promise.allSettled([api('/api/friends').then(data=>{state.friends=data.friends;renderFriends();if(data.warning)toast(data.warning);}).catch(error=>{const empty=element('div','empty-friends',error.message);$('friends-row').replaceChildren(empty);}),api('/api/games').then(data=>{state.games=data.games;state.favorites=data.favoriteIds;state.recent=data.recentIds;state.recommendationIds=data.recommendationIds;renderGames();if(data.warning)toast(data.warning);}).catch(error=>{banner('account-error',error.message);$('games-grid').replaceChildren();$('games-empty').hidden=false;})]);refresh.disabled=false;if(state.view==='home')route();}
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>navigate(b.dataset.view)));document.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>{state.filter=b.dataset.filter;renderGames();}));['empty-open'].forEach(id=>$(id).addEventListener('click',openJoin));$('continue-all').addEventListener('click',()=>{setView(state.recent.length?'discover':'favorites');if(state.recent.length){state.filter='recent';renderGames();}});$('menu-toggle').addEventListener('click',()=>document.body.classList.toggle('sidebar-collapsed'));['profile-nav','sidebar-profile'].forEach(id=>$(id).addEventListener('click',()=>$('profile-button').click()));$('all-friends').addEventListener('click',()=>navigate('friends'));$('refresh').addEventListener('click',load);$('profile-button').addEventListener('click',()=>{if(state.account)navigateProfile(state.account.id);});$('close-game').addEventListener('click',()=>$('game-dialog').close());$('close-join').addEventListener('click',()=>$('join-dialog').close());document.querySelectorAll('dialog').forEach(d=>d.addEventListener('click',e=>{if(e.target===d){const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close();}}));$('play-game').addEventListener('click',()=>{if(state.selected)play(String(state.selected.placeId),state.selected);});$('join-form').addEventListener('submit',e=>{e.preventDefault();const value=$('join-target').value.trim();if(/^https:\/\/www\.roblox\.com\/share(?:-links)?\?/.test(value))play(value,{name:'Private server'});else lookup(value);});$('search-form').addEventListener('submit',e=>{e.preventDefault();const value=$('search').value.trim();if(/^https:\/\/www\.roblox\.com\/share(?:-links)?\?/.test(value))play(value,{name:'Private server'});else if(/^\d+$/.test(value)||value.startsWith('https://www.roblox.com/games/'))lookup(value);else{location.hash='#/search?q='+encodeURIComponent(value);}});$('leave').addEventListener('click',async()=>{try{renderSession(await api('/api/leave',{}));}catch(error){toast(error.message);}});
async function poll(){try{renderSession(await api('/api/status'));}catch{}setTimeout(poll,1200);}window.addEventListener("DOMContentLoaded",()=>{route();load();poll();});

// Local navigation mirrors Roblox's profile and connection routes.
state.socialKind='friends';state.socialUser=null;state.people=[];state.profile=null;state.socialRequest=0;
function navigate(view){const hash=view==='friends'&&state.account?'#/users/'+state.account.id+'/connections':'#/'+view;if(location.hash===hash)route();else location.hash=hash;}
function navigateProfile(id){const hash='#/users/'+id+'/profile';if(location.hash===hash)route();else location.hash=hash;}
function navigatePeople(id,kind='friends'){location.hash='#/users/'+id+'/connections/'+kind;}
async function socialAPI(path){return api(path);}
async function route(){
 if(location.hash==='#/workspace'){setView('workspace');return;}
 if(!state.account)return;
 const match=location.hash.match(/^#\/users\/(\d+)\/(profile|connections)(?:\/(friends|followers|followings|requests))?$/);
 if(match){if(match[2]==='profile')return showProfile(match[1]);return showPeople(match[1],match[3]||'friends');}
 if(location.hash.startsWith('#/search?')){const params=new URLSearchParams(location.hash.split('?')[1]);return searchExperiences(params.get('q')||'');}
 const view=location.hash.replace(/^#\//,'');setView(['discover','favorites','avatar','messages','inventory','trade','communities','shop'].includes(view)?view:'home');if(view==='avatar')showAvatar();if(['messages','inventory','trade','communities','shop'].includes(view))AccountPages.show(view);
}
function statusText(person){return person.presence===2?(person.location||'In Experience'):person.presence===1?'Online':person.presence===3?'In Studio':person.presence===0?'Offline':'Status unavailable';}
function statusNode(person){const span=element('span','person-status'),dot=element('span','status-dot'+(person.presence===2?' ingame':person.presence>0?' online':''));span.append(dot,document.createTextNode(statusText(person)));return span;}
async function showPeople(id,kind){
 const request=++state.socialRequest;setView('friends');state.socialUser=String(id);state.socialKind=kind;state.people=[];
 $('greeting').textContent=Number(id)===state.account.id?'Connections':(state.profile?.id===Number(id)?state.profile.displayName+"'s Connections":'Connections');
 document.querySelectorAll('[data-social]').forEach(button=>{button.classList.toggle('selected',button.dataset.social===kind);button.setAttribute('aria-selected',String(button.dataset.social===kind));button.hidden=button.dataset.social==='requests'&&Number(id)!==state.account.id;});
 $('people-search').value='';$('people-status').value='all';$('people-grid').replaceChildren(element('p','people-empty','Loading…'));banner('people-error',null);
 const heading={friends:'Connections',followers:'Followers',followings:'Following',requests:'Requests'}[kind];$('people-heading').textContent=heading;$('people-search').placeholder='Search '+heading;
 try{
  const data=kind==='friends'&&Number(id)===state.account.id?{people:(await api('/api/friends')).friends}:await socialAPI('/api/people?user='+id+'&kind='+kind);
  if(request!==state.socialRequest||state.view!=='friends')return;
  state.people=data.people;$('people-heading').textContent=heading+' ('+state.people.length+')';
  $('people-note').textContent=data.nextCursor?'Showing the first '+state.people.length+' connections.':'';banner('people-error',data.warning);renderPeople();
 }catch(error){if(request!==state.socialRequest)return;banner('people-error',error.message);$('people-grid').replaceChildren();}
}
function renderPeople(){
 const search=$('people-search').value.toLowerCase(),filter=$('people-status').value;let people=state.people.filter(p=>(p.name+' '+p.displayName).toLowerCase().includes(search));
 if(filter==='online')people=people.filter(p=>p.presence>0);if(filter==='ingame')people=people.filter(p=>p.presence===2);
 const grid=$('people-grid');grid.replaceChildren();
 for(const person of people){const card=element('button','person-card');const info=element('div','person-info');info.append(element('h3','',person.displayName),element('p','','@'+person.name),statusNode(person));card.append(avatar(person),info);card.addEventListener('click',()=>navigateProfile(person.id));grid.append(card);}
 if(!people.length)grid.append(element('p','people-empty',search?'No connections found.':state.socialKind==='requests'?'You have no pending connection requests.':'No users to display.'));
}
async function showProfile(id){
 const request=++state.socialRequest;setView('profile');$('profile-loading').hidden=false;$('profile-content').hidden=true;banner('profile-error',null);
 try{const profile=await socialAPI('/api/profile?user='+id);if(request!==state.socialRequest||state.view!=='profile')return;state.profile=profile;renderProfile(profile);}
 catch(error){if(request===state.socialRequest)banner('profile-error',error.message);}
 finally{if(request===state.socialRequest)$('profile-loading').hidden=true;}
}
function renderProfile(profile){
 $('profile-content').hidden=false;$('profile-avatar').replaceChildren(avatar(profile));$('profile-display').replaceChildren(document.createTextNode(profile.displayName));if(profile.verified)$('profile-display').append(element('span','profile-verified','✓'));
 $('profile-handle').textContent='@'+profile.name;$('profile-presence').replaceChildren(statusNode(profile));
 for(const kind of ['friends','followers','followings'])$('profile-'+kind).textContent=profile.counts[kind]===null?'—':compact(profile.counts[kind]);
 $('profile-connected').hidden=!profile.isFriend;$('profile-official').href='https://www.roblox.com/users/'+profile.id+'/profile';
 $('profile-play').hidden=profile.presence!==2||!profile.placeId;
 $('profile-description').textContent=profile.description||'This user has not added a description.';
 const preview=$('wearing-preview');preview.replaceChildren();if(profile.fullAvatar){const img=element('img');img.src=profile.fullAvatar;img.alt=profile.displayName+"'s current avatar";preview.append(img);}else preview.append(avatar(profile));preview.append(element('span','mode-label','2D'));
 const date=profile.created?new Date(profile.created):null;$('profile-joined').textContent=date&&!Number.isNaN(+date)?date.toLocaleDateString('en-US'):'—';
 const games=$('profile-games');games.replaceChildren(...profile.creations.map(gameCard));$('profile-games-empty').hidden=profile.creations.length>0;$('profile-games-empty').textContent=profile.creationsError||'No public experiences to show.';profileTab('about');document.title=profile.displayName+' · Rove';
}
function profileTab(tab){$('profile-about').hidden=tab!=='about';$('profile-creations').hidden=tab!=='creations';document.querySelectorAll('[data-profile-tab]').forEach(b=>{b.classList.toggle('selected',b.dataset.profileTab===tab);b.setAttribute('aria-selected',String(b.dataset.profileTab===tab));});}
document.querySelectorAll('[data-social]').forEach(b=>b.addEventListener('click',()=>navigatePeople(state.socialUser,b.dataset.social)));
document.querySelectorAll('[data-profile-social]').forEach(b=>b.addEventListener('click',()=>{if(state.profile)navigatePeople(state.profile.id,b.dataset.profileSocial);}));
document.querySelectorAll('[data-profile-tab]').forEach(b=>b.addEventListener('click',()=>profileTab(b.dataset.profileTab)));
$('people-search').addEventListener('input',renderPeople);$('people-status').addEventListener('change',renderPeople);
$('profile-play').addEventListener('click',()=>{if(state.profile?.placeId)lookup(String(state.profile.placeId));});
window.addEventListener('hashchange',route);

let searchGeneration=0;
async function searchExperiences(query,more=false){
 const generation=++searchGeneration;
 if(!more){setView('discover');state.query=query;$('search').value=query;state.serverSearch=[];$('greeting').textContent='Search';$('games-title').textContent='Search results for “'+query+'”';$('games-grid').replaceChildren(element('p','muted','Searching Roblox…'));$('games-empty').hidden=true;$('search-more').hidden=true;}
 banner('search-error',null);$('search-more').disabled=true;
 try{const data=await socialAPI('/api/search?q='+encodeURIComponent(query)+(more&&state.searchCursor?'&cursor='+encodeURIComponent(state.searchCursor):''));if(generation!==searchGeneration||state.view!=='discover')return;state.query=query;state.serverSearch=more?(state.serverSearch||[]).concat(data.games):data.games;state.searchCursor=data.nextCursor;renderGames();$('games-title').textContent='Search results for “'+data.query+'”';$('search-more').hidden=!data.nextCursor;$('continue-section').hidden=true;}
 catch(error){if(generation===searchGeneration){banner('search-error',error.message);if(!more)$('games-grid').replaceChildren();}}
 finally{$('search-more').disabled=false;}
}
$('search-more').addEventListener('click',()=>searchExperiences(state.query,true));
