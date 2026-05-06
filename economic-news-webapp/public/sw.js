const CACHE_NAME = 'flowcapital-v1';
const API_CACHE_NAME = 'flowcapital-api-v1';

// 静态资源：安装时预缓存
const STATIC_ASSETS = [
  '/',
  '/index.html',
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS).catch(() => {}))
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME && k !== API_CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// 策略：静态资源 Cache First, API Network First with cache fallback
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // API 请求：Network First
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          const clone = response.clone();
          caches.open(API_CACHE_NAME).then(cache => {
            cache.put(event.request, clone);
          });
          return response;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // 静态资源：Cache First
  if (event.request.destination === 'script' ||
      event.request.destination === 'style' ||
      event.request.destination === 'font' ||
      event.request.destination === 'image') {
    event.respondWith(
      caches.match(event.request).then(cached => {
        const fetchPromise = fetch(event.request).then(response => {
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, response.clone()));
          return response;
        });
        return cached || fetchPromise;
      })
    );
    return;
  }

  // HTML & 其他：Network First
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});
