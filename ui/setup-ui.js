'use strict';
// Credential input stays in this form and one authenticated localhost request.
window.RoveSetup=(()=>{
 let dialog,status,busy=false;
 const byId=id=>document.getElementById(id);
 function mount(){
  if(dialog)return;
  const button=document.createElement('button');button.className='setup-open';button.textContent='Setup';button.id='open-setup';button.addEventListener('click',()=>open());
  document.querySelector('.header-actions').append(button);
  dialog=document.createElement('dialog');dialog.id='setup-dialog';dialog.setAttribute('aria-labelledby','setup-title');
  dialog.innerHTML=`<button class="dialog-close" id="setup-close" aria-label="Close setup">×</button>
   <div class="setup-brand">R<span>ROVE</span></div><h2 id="setup-title">Make yourself at home.</h2>
   <p class="setup-intro">Your account, your games. Let’s get this computer ready.</p>
   <p id="setup-status" role="status" aria-live="polite"></p>
   <section class="setup-section"><div class="setup-section-title"><h3>1. This computer</h3><span id="setup-platform"></span></div><ul id="setup-checks"></ul>
    <div id="setup-build" hidden><p>Finish the one-time setup, then check again.</p><strong id="setup-file"></strong><p class="setup-small">Open this file in the Rove project folder. The setup guide lists any tools you need first.</p></div>
   </section>
   <section class="setup-section"><h3>2. Your account</h3><p id="setup-account-status"></p>
    <button id="setup-change" type="button" class="secondary" hidden>Reconnect account</button>
    <form id="setup-account-form"><label for="setup-session">Account session</label><input id="setup-session" type="password" autocomplete="off" spellcheck="false" maxlength="32768" required placeholder="Paste your .ROBLOSECURITY session value">
     <p class="setup-small">This is an account session value, not your Roblox password. It is verified with Roblox and saved privately on this computer. Never share it with anyone.</p>
     <button id="setup-connect" class="primary" type="submit">Connect account</button>
    </form>
   </section><div class="setup-footer"><button id="setup-recheck" class="secondary" type="button">Check again</button><button id="setup-continue" class="primary" type="button" disabled>Open Rove →</button></div>`;
  document.body.append(dialog);
  byId('setup-close').onclick=()=>dialog.close();
  dialog.addEventListener('close',()=>{byId('setup-session').value='';});
  byId('setup-recheck').onclick=()=>check().catch(showError);
  byId('setup-change').onclick=()=>{byId('setup-account-form').hidden=false;byId('setup-session').focus();};
  byId('setup-continue').onclick=()=>{dialog.close();load();};
  byId('setup-account-form').addEventListener('submit',connect);
 }
 function showError(error){byId('setup-status').textContent=error.message||'Could not complete setup. Try again.';byId('setup-status').className='setup-error';}
 async function check(){
  status=await api('/api/setup');byId('setup-platform').textContent=status.platform;
  const list=byId('setup-checks');list.replaceChildren();
  for(const item of status.checks){const row=document.createElement('li'),mark=document.createElement('span'),label=document.createElement('span');mark.textContent=item.ready?'✓':'!';mark.className=item.ready?'setup-ok':'setup-required';label.textContent=item.label;row.append(mark,label);list.append(row);}
  byId('setup-build').hidden=status.runtimeReady;byId('setup-file').textContent=status.setupFile;
  byId('setup-account-status').textContent=status.accountConfigured?'An account session is saved on this computer.':'Connect an account to browse and join experiences.';
  byId('setup-account-form').hidden=status.accountConfigured;byId('setup-change').hidden=!status.accountConfigured;
  byId('setup-continue').disabled=!(status.runtimeReady&&status.accountConfigured);
  return status.runtimeReady&&status.accountConfigured;
 }
 async function open(message){mount();if(!dialog.open)dialog.showModal();byId('setup-status').textContent=message||'';byId('setup-status').className=message?'setup-error':'';try{await check();}catch(error){showError(error);}}
 async function connect(event){
  event.preventDefault();if(busy)return;busy=true;byId('setup-connect').disabled=true;
  byId('setup-status').textContent='Verifying your account…';byId('setup-status').className='';
  try{const result=await api('/api/setup/account',{session:byId('setup-session').value});byId('setup-session').value='';await check();byId('setup-status').textContent='Connected as '+(result.displayName||result.name)+'.';}
  catch(error){showError(error);}finally{byId('setup-session').value='';byId('setup-connect').disabled=false;busy=false;}
 }
 async function start(){mount();try{if(await check())return true;await open();}catch(error){await open(error.message);}return false;}
 return {start,open};
})();
