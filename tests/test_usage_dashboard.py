import json
import tempfile
import unittest
from pathlib import Path

try:
    from scripts.usage import dashboard
    from scripts.usage import store as store_module
except ImportError:
    dashboard = None
    store_module = None


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(dashboard, "scripts.usage.dashboard is missing")
        self.tmp = tempfile.TemporaryDirectory()
        self.store = store_module.UsageStore(Path(self.tmp.name) / "usage.sqlite")
        self.store.record(store_module.UsageRecord(
            ts_utc="2026-07-14T00:00:00Z", source="claude", ingest="proxy",
            model="claude-opus-4-8", input_tokens=400, cache_creation_tokens=100,
            cache_read_tokens=500, output_tokens=50))
        self.store.record(store_module.UsageRecord(
            ts_utc="2026-07-14T00:01:00Z", source="codex", ingest="log",
            model="gpt-5-codex", input_tokens=200, cache_read_tokens=800, output_tokens=30))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def h(self, path, method="GET"):
        return dashboard.handle(path, method, self.store)

    def test_non_prefix_returns_none(self):
        self.assertIsNone(self.h("/v1/messages"))
        self.assertIsNone(self.h("/"))

    def test_index_html(self):
        status, ctype, body = self.h("/__acm/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"Agent Context Memory", body)

    def test_index_html_no_trailing_slash(self):
        status, ctype, body = self.h("/__acm")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)

    def test_api_summary(self):
        status, ctype, body = self.h("/__acm/api/summary")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["overall"]["requests"], 2)
        sources = {r["source"]: r for r in data["by_source"]}
        self.assertIn("claude", sources)
        self.assertIn("codex", sources)
        # claude cache hit ratio = 500 / (400+100+500) = 0.5
        self.assertAlmostEqual(sources["claude"]["cache_hit_ratio"], 0.5)
        self.assertIn("illustrative_cache_savings_usd", sources["claude"])

    def test_api_models(self):
        status, ctype, body = self.h("/__acm/api/models")
        data = json.loads(body)
        models = {r["model"] for r in data}
        self.assertEqual(models, {"claude-opus-4-8", "gpt-5-codex"})

    def test_api_events_paging(self):
        status, ctype, body = self.h("/__acm/api/events?limit=1")
        data = json.loads(body)
        self.assertEqual(len(data), 1)

    def test_post_not_allowed(self):
        status, ctype, body = self.h("/__acm/api/summary", method="POST")
        self.assertEqual(status, 405)

    def test_unknown_route_404(self):
        status, ctype, body = self.h("/__acm/api/nope")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
