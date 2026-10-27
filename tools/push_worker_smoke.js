// Real service worker handlers with mocked OS/browser APIs: no notification delivery.
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const handlers={},shown=[],opened=[];
const worker={addEventListener:(type,handler)=>handlers[type]=handler,skipWaiting:()=>{},
 registration:{showNotification:async(title,options)=>shown.push({title,options})},
 location:{origin:'https://app.thothtrade.com'},
 clients:{claim:async()=>{},matchAll:async()=>[],openWindow:async url=>opened.push(url)}};
vm.runInNewContext(fs.readFileSync('web/sw.js','utf8'),{self:worker,URL,Date,Number,Response,fetch:()=>{throw Error('Unexpected network')}});
(async()=>{
 let waiting;const paused_at=Date.UTC(2026,9,27,7,37,8)/1000;
 handlers.push({data:{json:()=>({title:'THOTH: new entries paused',body:'Account monitoring unavailable · KeyError',paused_at,url:'https://evil.example'})},waitUntil:promise=>waiting=promise});
 await waiting;
 assert.equal(shown[0].title,'THOTH: new entries paused');
 assert.match(shown[0].options.body,/Account monitoring unavailable/);
 assert.match(shown[0].options.body,/3:37:08 AM EDT/);
 assert.equal(shown[0].options.requireInteraction,true);
 let closed=false;
 handlers.notificationclick({notification:{close:()=>closed=true},waitUntil:promise=>waiting=promise});
 await waiting;assert(closed);assert.deepEqual(opened,['/']);
 console.log('PASS: pause push displays saved reason + New York timestamp; notification opens only THOTH');
})().catch(error=>{console.error(error);process.exitCode=1});
