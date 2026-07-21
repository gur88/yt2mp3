const CACHE_NAME = 'audiograb-offline-v1';
const OFFLINE_URL = '/offline.html';

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.add(OFFLINE_URL))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)))
    )
  );
  self.clients.claim();
});

// Only navigations are intercepted (network-first, offline.html as the
// fallback). Everything else — static assets, /api/* — passes straight
// through untouched. No runtime caching beyond the one offline page:
// the ?v= query-string scheme already handles cache-busting for
// app.css/app.js, and a second cache layer here would just be a way
// for a stale app.js to silently outlive an API contract change.
self.addEventListener('fetch', (event) => {
  if (event.request.mode !== 'navigate') return;

  event.respondWith(
    fetch(event.request).catch(() => caches.match(OFFLINE_URL))
  );
});
