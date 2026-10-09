/* MakeSetu service worker (rendered by homepage.pwa.service_worker).
 *
 * Caches only static assets and the offline page. Pages are never cached:
 * they carry one user's company data and must always be fresh, so a page
 * that can't be fetched shows the offline page instead. Non-GET requests
 * and htmx partial requests go straight to the network. */
const CONFIG = {{ config }};

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CONFIG.cacheName)
      .then((cache) => cache.addAll(CONFIG.precache))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(
        names.filter((name) => name.startsWith("makesetu-") && name !== CONFIG.cacheName)
          .map((name) => caches.delete(name))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin || request.headers.has("HX-Request")) {
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() => caches.match(CONFIG.offlineUrl))
    );
    return;
  }

  if (url.pathname.startsWith(CONFIG.staticUrl)) {
    event.respondWith(CONFIG.staticCacheFirst ? cacheFirst(request) : networkFirst(request));
  }
});

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) {
    const cache = await caches.open(CONFIG.cacheName);
    cache.put(request, response.clone());
  }
  return response;
}

async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(CONFIG.cacheName);
      cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    const cached = await caches.match(request);
    if (cached) return cached;
    throw error;
  }
}
