// Opt-in, server-sent pause notifications. No trading commands in this module.
(()=>{
 const panel=document.createElement('section');panel.className='card';panel.id='phonePauseAlerts';
 panel.innerHTML='<h2>Phone pause alerts</h2><p>Receive a notification when account-wide new entries pause, including a server restart. Individual coin cooldowns are excluded. Notifications can arrive while THOTH is closed.</p><p class="note">Android: use Chrome and allow notifications. iPhone/iPad: add THOTH to the Home Screen, open it there, then enable alerts (iOS 16.4+). Delivery depends on your browser, connection and phone settings. A server that is completely offline cannot send an alert.</p><button id="enablePhoneAlerts" type="button">Enable on this device</button> <button id="testPhoneAlerts" type="button" disabled>Send test alert</button> <button id="disablePhoneAlerts" type="button" disabled>Disable on this device</button><p id="phoneAlertStatus" role="status">Checking notification support…</p><details><summary>Recent account pauses (New York)</summary><ul id="recentEntryPauses" class="note compact-scroll"></ul><button id="copyPauseReport" type="button">Copy pause report</button><textarea id="pauseReportFallback" readonly hidden aria-label="Pause report to copy"></textarea></details>';
 document.getElementById('page-security').append(panel);
 const el=id=>document.getElementById(id),status=el('phoneAlertStatus');let current=null,busy=false,pauseReport='';
 const supported=window.isSecureContext&&'serviceWorker' in navigator&&'PushManager' in window&&'Notification' in window;
 async function request(path,data){const response=await fetch('/api/phone-alerts'+path,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined});const value=await response.json();if(!response.ok)throw Error(typeof value.detail==='string'?value.detail:'Notification setup failed');return value}
 function decode(text){return Uint8Array.from(atob(text.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-text.length%4)%4)),c=>c.charCodeAt(0))}
 async function registration(){await navigator.serviceWorker.register('/sw.js');return navigator.serviceWorker.ready}
 async function refresh(){
  const data=await request('');pauseReport='THOTH · Saved account entry pauses (New York)\n'+(data.recent_pauses||[]).map(pause=>new Date(pause.time*1000).toLocaleString('en-US',{timeZone:'America/New_York',timeZoneName:'short'})+' · '+pause.body).join('\n');el('recentEntryPauses').replaceChildren();for(const pause of data.recent_pauses||[]){const row=document.createElement('li');row.textContent=new Date(pause.time*1000).toLocaleString('en-US',{timeZone:'America/New_York',timeZoneName:'short'})+' · '+pause.body;el('recentEntryPauses').append(row)}
  if(!supported){status.textContent='Push notifications are unavailable here. On iPhone, open the installed Home Screen app.';el('enablePhoneAlerts').disabled=true;return}
  const reg=await registration();current=await reg.pushManager.getSubscription();
  const endpoint=current?.endpoint;let device=null;
  if(endpoint){const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(endpoint));const id=Array.from(new Uint8Array(hash),b=>b.toString(16).padStart(2,'0')).join('');device=data.devices.find(row=>row.id===id)}
  const enabled=!!device;el('enablePhoneAlerts').disabled=enabled;el('disablePhoneAlerts').disabled=!current;el('testPhoneAlerts').disabled=!enabled;
  status.textContent=enabled?'Enabled on this device. Last delivery status: '+(device.status==='accepted'?'accepted by push provider; receipt is not confirmed':device.status)+'. Send a test and confirm it arrives.':Notification.permission==='denied'?'Notifications are blocked. Allow them in browser/site settings, then reload.':'Not enabled on this device.';
 }
 async function action(work){if(busy)return;busy=true;try{await work()}catch(error){status.textContent=error.message||'Notification operation failed'}finally{busy=false}}
 el('enablePhoneAlerts').onclick=()=>action(async()=>{
  // Permission request must occur directly after the user's click, before network work.
  const permission=await Notification.requestPermission();if(permission!=='granted')throw Error('Allow notifications in browser/site settings to enable phone alerts.');
  const data=await request(''),reg=await registration();current=await reg.pushManager.getSubscription();
  if(current&&encodeKey(current.options.applicationServerKey)!==data.public_key){await current.unsubscribe();current=null}
  current=current||await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:decode(data.public_key)});
  await request('/enable',current.toJSON());await refresh();
 });
 function encodeKey(key){return key?btoa(String.fromCharCode(...new Uint8Array(key))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,''):''}
 el('testPhoneAlerts').onclick=()=>action(async()=>{if(!current)throw Error('Enable alerts first.');await request('/test',current.toJSON());status.textContent='Test queued. Check your phone notification tray; a queued test does not confirm receipt.'});
 el('disablePhoneAlerts').onclick=()=>action(async()=>{if(current){await request('/disable',current.toJSON());await current.unsubscribe();current=null}await refresh()});
 el('copyPauseReport').onclick=async()=>{try{await navigator.clipboard.writeText(pauseReport);status.textContent='Pause report copied. Paste it into the chat.'}catch{const box=el('pauseReportFallback');box.hidden=false;box.value=pauseReport;box.focus();box.select();status.textContent='Select and copy the report below.'}};
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh().catch(error=>status.textContent=error.message)});
 refresh().catch(error=>status.textContent=error.message);
})();
