self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open("broncaserp-assets-v1").then((cache) =>
      cache.addAll(["/static/css/app.css", "/static/js/pos.js", "/static/icons/icon.svg"])
    )
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request)));
});

