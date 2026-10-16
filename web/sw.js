// Network-only worker: never cache account data or queue trading commands offline.
self.addEventListener('install',()=>self.skipWaiting());
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
self.addEventListener('fetch',event=>{
 if(event.request.mode==='navigate')event.respondWith(fetch(event.request).catch(()=>new Response(
  '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><title>THOTH offline</title><body style="background:#101918;color:#edf3e8;font:18px system-ui;padding:32px"><h1>Connection lost</h1><p>The dashboard is offline. Trading controls are unavailable. Reconnect to check the server and your positions.</p><button onclick="location.reload()">Retry</button>',
  {status:503,headers:{'Content-Type':'text/html'}})));
});
