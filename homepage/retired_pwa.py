"""Retires the service worker the installable-app release put at /sw.js
(commit 110892b, reverted in 149a4a7).

Browsers that visited while it was live still have it installed for the
whole site, and a 404 on its update check does not remove it. It shows its
own "You're offline" page whenever a page can't be fetched, which hides
the real error (the server being down looks like the visitor's
connection). So /sw.js now serves a worker that deletes the old caches,
unregisters itself and reloads the open pages without it. The browser
picks it up on its next visit, since it checks /sw.js for updates on
navigation.

Keep this route for a while after every browser has had a chance to visit
(a few months); removing it early leaves the old worker in place.
"""
from django.contrib.auth.decorators import login_not_required
from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

SCRIPT = """\
/* MakeSetu no longer uses a service worker. This one removes the old one. */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((name) => name.startsWith("makesetu-")).map((name) => caches.delete(name)));
    await self.registration.unregister();
    const pages = await self.clients.matchAll({ type: "window" });
    pages.forEach((page) => page.navigate(page.url));
  })());
});
"""


@login_not_required
@require_GET
@never_cache
def service_worker(request):
    return HttpResponse(SCRIPT, content_type="application/javascript")
