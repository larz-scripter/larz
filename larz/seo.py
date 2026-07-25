"""
larz.seo — SEO as a framework primitive.

Auto-generates /sitemap.xml and /robots.txt from your registered GET routes,
and can ping IndexNow (which Bing honours — and Bing is where a lot of
long-tail money traffic actually lands) whenever you publish a URL.
"""

import html
import urllib.parse
import urllib.request
from .core import Response

__all__ = ["enable", "meta_tags"]


def meta_tags(title, description="", url="", image="", site_name="", type="website"):
    """Render a full <title> + SEO + OpenGraph + Twitter-card meta block."""
    e = lambda s: html.escape(str(s), quote=True)
    tags = ["<title>%s</title>" % e(title),
            "<meta name='description' content='%s'>" % e(description),
            "<meta property='og:title' content='%s'>" % e(title),
            "<meta property='og:description' content='%s'>" % e(description),
            "<meta property='og:type' content='%s'>" % e(type),
            "<meta name='twitter:card' content='summary_large_image'>",
            "<meta name='twitter:title' content='%s'>" % e(title),
            "<meta name='twitter:description' content='%s'>" % e(description)]
    if url:
        tags.append("<meta property='og:url' content='%s'>" % e(url))
    if image:
        tags.append("<meta property='og:image' content='%s'>" % e(image))
        tags.append("<meta name='twitter:image' content='%s'>" % e(image))
    if site_name:
        tags.append("<meta property='og:site_name' content='%s'>" % e(site_name))
    return "\n".join(tags)


class _Seo:
    def __init__(self, app, base_url, indexnow_key=None):
        self.app = app
        self.base_url = base_url.rstrip("/")
        self.indexnow_key = indexnow_key
        self._extra = []          # manually added URLs (e.g. dynamic content)

    def add_url(self, path):
        self._extra.append(path)

    def _static_paths(self):
        seen, out = set(), []
        for r in self.app.routes:
            if "GET" not in r.methods:
                continue
            if "<" in r.pattern:                 # skip parameterised routes
                continue
            if r.pattern.startswith("/larz/"):   # skip internal plumbing
                continue
            if r.opts.get("sitemap") is False:
                continue
            if r.pattern in seen:
                continue
            seen.add(r.pattern)
            out.append(r.pattern)
        return out + self._extra

    def sitemap_xml(self):
        urls = self._static_paths()
        items = "".join(
            "<url><loc>%s%s</loc></url>" % (self.base_url, p) for p in urls)
        return ('<?xml version="1.0" encoding="UTF-8"?>'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                + items + "</urlset>")

    def robots_txt(self):
        return "User-agent: *\nAllow: /\nSitemap: %s/sitemap.xml\n" % self.base_url

    def indexnow(self, path):
        """Submit a single URL to IndexNow (api.indexnow.org)."""
        if not self.indexnow_key:
            return False
        host = urllib.parse.urlparse(self.base_url).netloc
        q = urllib.parse.urlencode({
            "url": self.base_url + path, "key": self.indexnow_key})
        try:
            with urllib.request.urlopen(
                    "https://api.indexnow.org/indexnow?" + q, timeout=10) as r:
                return 200 <= r.status < 300
        except Exception:
            return False

    def install_routes(self):
        app = self.app

        @app.get("/sitemap.xml", sitemap=False)
        def _sitemap(req):
            return Response(self.sitemap_xml(), content_type="application/xml")

        @app.get("/robots.txt", sitemap=False)
        def _robots(req):
            return Response(self.robots_txt(), content_type="text/plain")


def enable(app, base_url="http://127.0.0.1:8000", indexnow_key=None):
    app.seo = _Seo(app, base_url, indexnow_key)
    return app.seo
