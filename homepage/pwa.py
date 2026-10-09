"""Installable-app (PWA) endpoints: web app manifest, service worker, offline
page and Android Digital Asset Links.

These are what a store wrapper builds on later: a Trusted Web Activity
(Play Store) needs the manifest plus /.well-known/assetlinks.json, and
Capacitor (App Store) loads the same site. Keep the app's look and
behaviour in the templates, not in a wrapper, so every platform stays in
step.
"""
import hashlib
import json

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

THEME_COLOR = "#11355c"  # navy-900, the logo's navy


@login_not_required
@require_GET
@cache_control(max_age=3600, public=True)
def manifest(request):
    return JsonResponse({
        "id": "/",
        "name": "MakeSetu",
        "short_name": "MakeSetu",
        "description": "Post RFQs, compare quotes from verified manufacturers and track orders to delivery.",
        "start_url": reverse("home") + "?source=pwa",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#ffffff",
        "theme_color": THEME_COLOR,
        "categories": ["business", "productivity"],
        "icons": [
            {"src": static("pwa/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": static("pwa/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": static("pwa/icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }, content_type="application/manifest+json")


@login_not_required
@require_GET
@cache_control(no_cache=True)
def service_worker(request):
    # Served from the site root so its scope covers every page. The cache
    # name is derived from what it caches, so a deploy that changes any of
    # those URLs (hashed static names in production) replaces the old cache.
    precache = [
        reverse("pwa-offline"),
        static("css/tailwind-built.css"),
        static("js/vendor/htmx-1.9.12.min.js"),
        static("js/vendor/alpine-3.14.1.min.js"),
        static("images/logo.svg"),
        static("pwa/icon-192.png"),
    ]
    config = {
        "cacheName": "makesetu-" + hashlib.sha256("|".join(precache).encode()).hexdigest()[:12],
        "precache": precache,
        "offlineUrl": reverse("pwa-offline"),
        "staticUrl": settings.STATIC_URL,
        # Production static names carry a content hash, so a cached copy
        # never goes stale; in development they don't, so check the network.
        "staticCacheFirst": not settings.DEBUG,
    }
    body = render_to_string("pwa/sw.js", {"config": mark_safe(json.dumps(config))})
    response = HttpResponse(body, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    return response


@login_not_required
@require_GET
def offline(request):
    # Precached by the service worker and shown when a page can't be
    # fetched. Rendered without user data: it is cached for everyone.
    return render(request, "pwa/offline.html")


@login_not_required
@require_GET
@cache_control(max_age=3600, public=True)
def assetlinks(request):
    # Lets the Play Store app (a Trusted Web Activity) open this site full
    # screen, without a browser bar. Empty until the app exists.
    statements = []
    if settings.ANDROID_APP_PACKAGE and settings.ANDROID_APP_CERT_FINGERPRINTS:
        statements.append({
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": settings.ANDROID_APP_PACKAGE,
                "sha256_cert_fingerprints": settings.ANDROID_APP_CERT_FINGERPRINTS,
            },
        })
    return JsonResponse(statements, safe=False)
