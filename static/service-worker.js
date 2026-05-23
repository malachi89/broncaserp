self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open("broncaserp-assets-v4").then((cache) =>
      cache.addAll([
        "/static/css/app.css",
        "/static/js/pos.js",
        "/static/icons/broncas-logo.png",
        "/static/icons/icon-32.png",
        "/static/icons/icon-180.png",
        "/static/icons/icon-192.png",
        "/static/icons/icon-512.png",
      ])
    )
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request)));
});
