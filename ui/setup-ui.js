'use strict';
// Credential input stays in this form and one authenticated localhost request.
window.RoveSetup=(()=>{
 let dialog,status,busy=false,needsAccount=false;
 const byId=id=>document.getElementById(id);
 function setPageInert(inert){
  for(const area of document.querySelectorAll('body > header, body > aside, main'))area.inert=inert;
 }
 function loading(title,detail){
  byId('rove-loading-title').textContent=title;
  byId('rove-loading-detail').textContent=detail;
  byId('rove-loading').hidden=false;
  setPageInert(true);
 }
 function finishLoading(){
  byId('rove-loading').hidden=true;
  setPageInert(false);
 }
 async function enter(){
  loading('Opening your account','Checking your saved Roblox session…');
  try{await load({startup:true});}
  finally{finishLoading();}
 }
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
    <form id="setup-account-form" hidden><label for="setup-session">Roblox session cookie</label><input id="setup-session" type="password" autocomplete="off" spellcheck="false" maxlength="32768" aria-describedby="setup-session-hint" required placeholder="Paste your .ROBLOSECURITY cookie">
     <p class="setup-small" id="setup-session-hint">Use your own account cookie. Rove verifies it with Roblox and saves it privately on this computer.</p>
     <button id="setup-connect" class="primary" type="submit">Connect account</button>
    </form>
   </section><div class="setup-footer"><button id="setup-recheck" class="secondary" type="button">Check again</button><button id="setup-continue" class="primary" type="button" disabled>Open Rove →</button></div>`;
  document.body.append(dialog);
  byId('setup-close').onclick=()=>dialog.close();
  dialog.addEventListener('close',()=>{byId('setup-session').value='';});
  byId('setup-recheck').onclick=()=>check().catch(showError);
  byId('setup-change').onclick=()=>{byId('setup-account-form').hidden=false;byId('setup-session').focus();};
  byId('setup-continue').onclick=()=>{dialog.close();void enter();};
  byId('setup-account-form').addEventListener('submit',connect);
 }
 function showError(error){byId('setup-status').textContent=error.message||'Could not complete setup. Try again.';byId('setup-status').className='setup-error';}
 async function check(){
  status=await api('/api/setup');byId('setup-platform').textContent=status.platform;
  const list=byId('setup-checks');list.replaceChildren();
  for(const item of status.checks){const row=document.createElement('li'),mark=document.createElement('span'),label=document.createElement('span');mark.textContent=item.ready?'✓':'!';mark.className=item.ready?'setup-ok':'setup-required';label.textContent=item.label;row.append(mark,label);list.append(row);}
  byId('setup-build').hidden=status.runtimeReady;byId('setup-file').textContent=status.setupFile;
  const connected=status.accountConfigured&&!needsAccount;
  byId('setup-account-status').textContent=connected?'An account session is saved on this computer.':'Connect an account to browse and join experiences.';
  byId('setup-account-form').hidden=connected;byId('setup-change').hidden=!connected;
  byId('setup-continue').disabled=!(status.runtimeReady&&connected);
  return status.runtimeReady&&connected;
 }
 function showDialog(){
  finishLoading();
  if(!dialog.open)dialog.showModal();
  if(!byId('setup-account-form').hidden)byId('setup-session').focus();
 }
 async function open(message,{reconnect=false}={}){
  mount();if(reconnect)needsAccount=true;
  byId('setup-status').textContent=message||'';byId('setup-status').className=message?'setup-error':'';
  try{await check();}catch(error){showError(error);}
  showDialog();
 }
 async function connect(event){
  event.preventDefault();if(busy)return;busy=true;
  const button=byId('setup-connect'),input=byId('setup-session'),form=byId('setup-account-form');
  const session=input.value;input.value='';
  const continueButton=byId('setup-continue'),wasDisabled=continueButton.disabled;
  button.disabled=true;byId('setup-recheck').disabled=true;continueButton.disabled=true;
  form.setAttribute('aria-busy','true');button.textContent='Verifying account…';
  byId('setup-status').textContent='Verifying your account…';byId('setup-status').className='';
  try{
   const result=await api('/api/setup/account',{session});
   needsAccount=false;
   await check();
   byId('setup-status').textContent='Connected as '+(result.displayName||result.name)+'.';
   if(dialog.open&&!continueButton.disabled)continueButton.focus();
  }catch(error){
   showError(error);continueButton.disabled=wasDisabled;
   if(dialog.open&&!form.hidden)input.focus();
  }finally{
   input.value='';button.disabled=false;byId('setup-recheck').disabled=false;
   form.removeAttribute('aria-busy');button.textContent='Connect account';busy=false;
  }
 }
 async function start(){
  mount();loading('Starting Rove','Checking this computer…');
  try{if(await check()){await enter();return;}}
  catch(error){showError(error);}
  showDialog();
 }
 return {start,open,loading};
})();
