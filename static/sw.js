// EzBay resilience worker. CRM pages remain network-only: no inventory,
// pricing, customer or authentication data is stored in a browser cache.
const RECONNECT_PAGE = `<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0891b2"><title>EzBay — reconnecting</title>
<style>
html,body{height:100%;margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f6f8fb;color:#152033}
body{display:grid;place-items:center;padding:24px;box-sizing:border-box}.card{width:min(390px,100%);text-align:center;background:#fff;border:1px solid #dce3ec;border-radius:18px;padding:28px;box-shadow:0 14px 40px rgba(20,42,70,.12)}
.spinner{width:34px;height:34px;margin:0 auto 18px;border:4px solid #d9edf2;border-top-color:#0891b2;border-radius:50%;animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
h1{font-size:22px;margin:0 0 8px}p{color:#5d6b7e;line-height:1.45;margin:0 0 18px}button{border:0;border-radius:10px;background:#087f9c;color:#fff;font:inherit;font-weight:700;padding:11px 18px}
</style></head><body><main class="card"><div class="spinner"></div><h1>Reconnecting to EzBay</h1><p>The app was interrupted while it was in the background. This page will recover automatically.</p><button onclick="location.reload()">Try now</button></main>
<script>(function(){async function retry(){try{var r=await fetch('/health?reconnect='+Date.now(),{cache:'no-store'});if(r.ok){location.reload();return;}}catch(e){}setTimeout(retry,2000)}retry()})()<\/script></body></html>`;

self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => {
  if (event.request.mode === 'navigate') {
    event.respondWith((async () => {
      try {
        const response = await fetch(event.request);
        if ([502, 503, 504].includes(response.status)) throw new Error('server unavailable');
        return response;
      } catch (error) {
        return new Response(RECONNECT_PAGE, {
          status: 503,
          headers: {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store'}
        });
      }
    })());
  }
});
