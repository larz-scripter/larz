"""Tests for the larz.seo internal-linking engine."""
import unittest
from larz import seo


class TestInterlink(unittest.TestCase):
    def test_links_first_mention_only(self):
        out = seo.interlink("A scam is a scam.", {"scam": "/scams/"})
        self.assertEqual(out.count('href="/scams/"'), 1)  # max_per_target=1
        self.assertIn('>scam</a> is a scam', out)

    def test_never_links_inside_existing_anchor(self):
        html = 'See <a href="/x/">a scam guide</a> now, scam.'
        out = seo.interlink(html, {"scam": "/scams/"}, max_links=5)
        # the anchor text is protected; only the trailing bare "scam" links
        self.assertEqual(out.count("<a "), 2)
        self.assertIn('<a href="/x/">a scam guide</a>', out)

    def test_never_links_inside_heading_or_code(self):
        html = "<h2>scam</h2><p>a scam</p><code>scam()</code>"
        out = seo.interlink(html, {"scam": "/scams/"}, max_links=5)
        self.assertEqual(out.count('href="/scams/"'), 1)  # only the <p> text
        self.assertIn("<h2>scam</h2>", out)
        self.assertIn("<code>scam()</code>", out)

    def test_total_cap(self):
        html = "a b c d e"
        out = seo.interlink(html, {"a": "/a", "b": "/b", "c": "/c",
                                   "d": "/d", "e": "/e"}, max_links=2)
        self.assertEqual(out.count("<a "), 2)

    def test_case_insensitive_whole_word(self):
        out = seo.interlink("Scams and scampi", {"scam": "/s/", "scams": "/ss/"})
        self.assertIn('href="/ss/"', out)      # 'Scams' matched (longest first)
        self.assertIn("scampi", out)           # 'scampi' not linked (word boundary)

    def test_empty_map_noop(self):
        self.assertEqual(seo.interlink("hi", {}), "hi")


class TestContentGraph(unittest.TestCase):
    def graph(self):
        g = seo.ContentGraph()
        g.add("/crypto/", "Crypto", cluster="crypto", tags=["money"])
        g.add("/crypto/scams/", "Scams", cluster="crypto", tags=["safety"],
              keywords=["scam"], parent="/crypto/")
        g.add("/crypto/wallets/", "Wallets", cluster="crypto", tags=["safety"],
              keywords=["wallet"], parent="/crypto/")
        g.add("/money/", "Money", cluster="money", tags=["income"])
        return g

    def test_related_by_shared_tags(self):
        g = self.graph()
        rel = [p["path"] for p in g.related("/crypto/scams/")]
        self.assertEqual(rel[0], "/crypto/wallets/")  # shares 'safety' tag

    def test_related_cluster_bonus(self):
        g = self.graph()
        rel = [p["path"] for p in g.related("/crypto/")]
        self.assertIn("/crypto/scams/", rel)          # same cluster
        self.assertNotIn("/money/", rel[:1])          # different cluster ranks lower

    def test_link_map(self):
        m = self.graph().link_map()
        self.assertEqual(m["scam"], "/crypto/scams/")
        self.assertEqual(m["wallet"], "/crypto/wallets/")

    def test_link_map_excludes_self(self):
        m = self.graph().link_map(exclude="/crypto/scams/")
        self.assertNotIn("scam", m)

    def test_breadcrumbs(self):
        chain = [p["path"] for p in self.graph().breadcrumbs("/crypto/scams/")]
        self.assertEqual(chain, ["/crypto/", "/crypto/scams/"])

    def test_orphans_and_validate(self):
        g = self.graph()
        # scams/wallets share a tag so link to each other; /money/ is an orphan
        self.assertIn("/money/", g.orphans())
        self.assertRaises(ValueError, g.validate)

    def test_validate_passes_when_linked(self):
        g = seo.ContentGraph()
        g.add("/hub/", "Hub")
        g.add("/hub/a/", "A", tags=["t"], parent="/hub/")
        g.add("/hub/b/", "B", tags=["t"], parent="/hub/")
        self.assertTrue(g.validate())

    def test_dead_parent_detected(self):
        g = seo.ContentGraph()
        g.add("/x/", "X", parent="/nope/")
        self.assertRaises(ValueError, g.validate)

    def test_sitemap_has_lastmod(self):
        g = seo.ContentGraph()
        g.add("/p/", "P", lastmod="2026-07-26")
        xml = g.sitemap_xml("https://larzos.com")
        self.assertIn("<loc>https://larzos.com/p/</loc>", xml)
        self.assertIn("<lastmod>2026-07-26</lastmod>", xml)


class TestRenderers(unittest.TestCase):
    def test_breadcrumbs_html_with_jsonld(self):
        g = seo.ContentGraph()
        g.add("/a/", "A")
        g.add("/a/b/", "B", parent="/a/")
        out = seo.breadcrumbs_html(g.breadcrumbs("/a/b/"), "https://x.com")
        self.assertIn('<a href="/a/">A</a>', out)
        self.assertIn("BreadcrumbList", out)
        self.assertIn("application/ld+json", out)

    def test_related_html(self):
        pages = [{"path": "/x/", "title": "X", "description": "d"}]
        out = seo.related_html(pages)
        self.assertIn('href="/x/"', out)
        self.assertIn("<h2>Related</h2>", out)

    def test_related_html_empty(self):
        self.assertEqual(seo.related_html([]), "")

    def test_json_ld_faq(self):
        out = seo.json_ld("FAQPage", faqs=[{"q": "Why?", "a": "Because."}])
        self.assertIn('"@type":"FAQPage"', out)
        self.assertIn('"Question"', out)
        self.assertIn("Because.", out)

    def test_json_ld_course(self):
        out = seo.json_ld("Course", name="Crypto 101", description="d")
        self.assertIn('"@type":"Course"', out)
        self.assertIn("Crypto 101", out)


if __name__ == "__main__":
    unittest.main()
