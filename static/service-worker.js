self.CACHE_NAME = "broncaserp-assets-v5";
self.PRECACHE_URLS = [
  "/static/css/app.css",
  "/static/js/pos.js",
  "/static/icons/broncas-logo.png",
  "/static/icons/icon-32.png",
  "/static/icons/icon-180.png",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(self.CACHE_NAME).then((cache) => cache.addAll(self.PRECACHE_URLS))
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key.startsWith("broncaserp-assets-") && key !== self.CACHE_NAME)
          .map((key) => caches.delete(key))
      )
    )
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  event.respondWith(
    caches.open(self.CACHE_NAME)
      .then((cache) => cache.match(event.request))
      .then((cached) => cached || fetch(event.request))
  );
});
