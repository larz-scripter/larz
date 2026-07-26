"""
larz.seo — SEO as a framework primitive.

Auto-generates /sitemap.xml and /robots.txt from your registered GET routes,
and can ping IndexNow (which Bing honours — and Bing is where a lot of
long-tail money traffic actually lands) whenever you publish a URL.
"""

import html
import re
import urllib.parse
import urllib.request
from .core import Response

__all__ = ["enable", "meta_tags", "ContentGraph", "interlink", "json_ld",
           "breadcrumbs_html", "related_html"]


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


# =====================================================================
# Internal-linking engine — programmatic SEO with a real link graph.
#
# The value of a content site is its internal-link structure: hub pages
# pointing down to clusters, spokes pointing back up, and contextual
# in-body links between related pages. These helpers build that graph
# from page metadata and emit the links, breadcrumbs, related rails and
# JSON-LD that search engines reward — with a validator that catches
# orphaned pages before you publish.
# =====================================================================

_e = lambda s: html.escape(str(s), quote=True)

# spans that must never be rewritten by the auto-linker: existing
# anchors, headings, code, and any bare tag.
_PROTECT = re.compile(
    r"(<a\b[^>]*>.*?</a>"
    r"|<(h[1-6]|pre|code|script|style)\b[^>]*>.*?</\2>"
    r"|<[^>]+>)",
    re.I | re.S)


def interlink(body_html, link_map, max_links=3, max_per_target=1,
              css_class="xlink"):
    """Auto-link the first mention of known keywords in ``body_html``.

    ``link_map`` is ``{keyword: url}``. Only plain text is touched — text
    inside existing links, headings, and code is left alone — and linking
    is capped (``max_links`` total, ``max_per_target`` per destination) so
    the result stays natural rather than spammy.
    """
    if not link_map:
        return body_html
    keywords = sorted(link_map, key=len, reverse=True)
    kw_re = re.compile(r"\b(" + "|".join(re.escape(k) for k in keywords) + r")\b",
                       re.I)
    lower = {k.lower(): (k, v) for k, v in link_map.items()}
    state = {"total": 0, "per": {}}

    def _link_segment(seg):
        def repl(m):
            if state["total"] >= max_links:
                return m.group(0)
            word = m.group(0)
            pair = lower.get(word.lower())
            if not pair:
                return m.group(0)
            url = pair[1]
            if state["per"].get(url, 0) >= max_per_target:
                return m.group(0)
            state["per"][url] = state["per"].get(url, 0) + 1
            state["total"] += 1
            return '<a class="%s" href="%s">%s</a>' % (_e(css_class), _e(url), word)
        return kw_re.sub(repl, seg)

    out, pos = [], 0
    for m in _PROTECT.finditer(body_html):
        out.append(_link_segment(body_html[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(_link_segment(body_html[pos:]))
    return "".join(out)


class ContentGraph(object):
    """A registry of pages that computes the internal-link graph.

        g = ContentGraph()
        g.add("/guide/", "Guide", cluster="crypto", tags=["safety"])
        g.add("/guide/scams/", "Spot a scam", cluster="crypto",
              tags=["safety"], keywords=["scam", "scams"], parent="/guide/")

        g.related("/guide/")          # pages sharing tags/cluster
        g.breadcrumbs("/guide/scams/")
        g.link_map()                  # keyword -> url for interlink()
        g.orphans()                   # pages nothing links to
    """

    def __init__(self):
        self.pages = {}

    def add(self, path, title, cluster=None, tags=(), keywords=(),
            lastmod=None, parent=None, description=""):
        self.pages[path] = {
            "path": path, "title": title, "cluster": cluster,
            "tags": set(tags), "keywords": list(keywords),
            "lastmod": lastmod, "parent": parent, "description": description}
        return self

    def related(self, path, n=4):
        """Pages most related to ``path``: shared tags weighted over a
        shared cluster, ties broken alphabetically for determinism."""
        p = self.pages[path]
        scored = []
        for o in self.pages.values():
            if o["path"] == path:
                continue
            score = len(p["tags"] & o["tags"]) * 2
            if p["cluster"] and o["cluster"] == p["cluster"]:
                score += 1
            if score:
                scored.append((score, o))
        scored.sort(key=lambda t: (-t[0], t[1]["title"]))
        return [o for _, o in scored[:n]]

    def link_map(self, exclude=None):
        """A ``{keyword: url}`` map for :func:`interlink`. First page to
        claim a keyword wins; ``exclude`` (a path) drops self-links."""
        out = {}
        for o in self.pages.values():
            if o["path"] == exclude:
                continue
            for kw in o["keywords"]:
                out.setdefault(kw, o["path"])
        return out

    def breadcrumbs(self, path):
        """The parent chain from the root down to ``path`` (inclusive)."""
        chain, seen, cur = [], set(), self.pages.get(path)
        while cur and cur["path"] not in seen:
            seen.add(cur["path"])
            chain.append(cur)
            cur = self.pages.get(cur["parent"])
        return list(reversed(chain))

    def inbound_counts(self):
        """Inbound internal links per page, from three sources: related
        rails, the up-link every child gives its parent, and the down-link
        every parent (hub index) gives each child."""
        counts = dict((p, 0) for p in self.pages)
        for o in self.pages.values():
            for r in self.related(o["path"]):
                counts[r["path"]] = counts.get(r["path"], 0) + 1
            if o["parent"] in counts:
                counts[o["parent"]] += 1      # child -> parent (up-link)
                counts[o["path"]] += 1        # parent hub -> child (down-link)
        return counts

    def orphans(self):
        """Pages with no inbound link of any kind — unreachable by a
        crawler, and an SEO smell. (A hub reached only by its children is
        not an orphan; a disconnected page with no relations is.)"""
        counts = self.inbound_counts()
        return sorted(p for p, c in counts.items() if c == 0)

    def validate(self):
        """Raise ``ValueError`` on broken parents or orphaned pages."""
        problems = []
        for o in self.pages.values():
            if o["parent"] and o["parent"] not in self.pages:
                problems.append("dead parent: %s -> %s" % (o["path"], o["parent"]))
        problems += ["orphan: %s" % p for p in self.orphans()]
        if problems:
            raise ValueError("link graph invalid:\n  " + "\n  ".join(problems))
        return True

    def sitemap_items(self, base_url=""):
        items = []
        for o in sorted(self.pages.values(), key=lambda x: x["path"]):
            loc = base_url.rstrip("/") + o["path"]
            row = "<url><loc>%s</loc>" % _e(loc)
            if o["lastmod"]:
                row += "<lastmod>%s</lastmod>" % _e(o["lastmod"])
            items.append(row + "</url>")
        return "".join(items)

    def sitemap_xml(self, base_url=""):
        return ('<?xml version="1.0" encoding="UTF-8"?>'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                + self.sitemap_items(base_url) + "</urlset>")


def breadcrumbs_html(chain, base_url="", sep="&rsaquo;", css="crumbs"):
    """Render a breadcrumb chain (list of page dicts) with JSON-LD."""
    parts = []
    for p in chain:
        parts.append('<a href="%s">%s</a>' % (_e(p["path"]), _e(p["title"])))
    trail = ('<span> %s </span>' % sep).join(parts)
    ld = json_ld("BreadcrumbList", items=[
        {"name": p["title"], "item": base_url.rstrip("/") + p["path"]}
        for p in chain])
    return '<nav class="%s">%s</nav>%s' % (_e(css), trail, ld)


def related_html(pages, heading="Related", css="xlinks"):
    """Render a 'related pages' rail from a list of page dicts."""
    if not pages:
        return ""
    cards = []
    for p in pages:
        desc = ('<span>%s</span>' % _e(p["description"])) if p.get("description") else ""
        cards.append('<a class="xl" href="%s"><b>%s</b>%s<span class="arw">'
                     '&rarr;</span></a>' % (_e(p["path"]), _e(p["title"]), desc))
    return ('<section class="%s-wrap"><h2>%s</h2><div class="%s">%s</div></section>'
            % (_e(css), _e(heading), _e(css), "".join(cards)))


def json_ld(schema_type, **fields):
    """Emit a JSON-LD ``<script>`` block. Handy shapes:

        json_ld("Article", headline=..., description=..., datePublished=...)
        json_ld("Course", name=..., description=..., provider=...)
        json_ld("FAQPage", faqs=[{"q": ..., "a": ...}])
        json_ld("BreadcrumbList", items=[{"name": ..., "item": url}])
    """
    import json

    data = {"@context": "https://schema.org", "@type": schema_type}
    if schema_type == "FAQPage":
        data["mainEntity"] = [
            {"@type": "Question", "name": f["q"],
             "acceptedAnswer": {"@type": "Answer", "text": f["a"]}}
            for f in fields.pop("faqs", [])]
    elif schema_type == "BreadcrumbList":
        data["itemListElement"] = [
            {"@type": "ListItem", "position": i + 1,
             "name": it["name"], "item": it.get("item", "")}
            for i, it in enumerate(fields.pop("items", []))]
    data.update((k, v) for k, v in fields.items() if v is not None)
    return ('<script type="application/ld+json">%s</script>'
            % json.dumps(data, separators=(",", ":")))
