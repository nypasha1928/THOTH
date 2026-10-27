// Network-only worker: never cache account data or queue trading commands offline.
self.addEventListener('install',()=>self.skipWaiting());
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
self.addEventListener('push',event=>{
 let data={};try{data=event.data?.json()||{}}catch{}
 event.waitUntil(self.registration.showNotification(data.title||'THOTH alert',{
  body:(data.body||'Open THOTH to check trading status.')+(Number.isFinite(data.paused_at)?'\n'+new Date(data.paused_at*1000).toLocaleString('en-US',{timeZone:'America/New_York',timeZoneName:'short'}):''),icon:'/icon-192.png',badge:'/icon-192.png',
  requireInteraction:true,data:{url:'/'},tag:data.tag||undefined
 }));
});
self.addEventListener('notificationclick',event=>{
 event.notification.close();
 event.waitUntil(self.clients.matchAll({type:'window',includeUncontrolled:true}).then(async clients=>{
  const existing=clients.find(client=>new URL(client.url).origin===self.location.origin);
  if(existing){await existing.focus();return existing.navigate('/')}
  return self.clients.openWindow('/');
 }));
});
self.addEventListener('fetch',event=>{
 if(event.request.mode==='navigate')event.respondWith(fetch(event.request).catch(()=>new Response(
  '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><title>THOTH offline</title><body style="background:#101918;color:#edf3e8;font:18px system-ui;padding:32px"><h1>Connection lost</h1><p>The dashboard is offline. Trading controls are unavailable. Reconnect to check the server and your positions.</p><button onclick="location.reload()">Retry</button>',
  {status:503,headers:{'Content-Type':'text/html'}})));
});
