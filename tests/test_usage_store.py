import tempfile
import unittest
from pathlib import Path

try:
    from scripts.usage import store as store_module
except ImportError:
    store_module = None


class UsageStoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(store_module, "scripts.usage.store is missing")
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "sub" / "usage.sqlite"
        self.store = store_module.UsageStore(self.db)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def rec(self, **kw):
        base = dict(ts_utc="2026-07-14T00:00:00Z", source="claude", ingest="proxy")
        base.update(kw)
        return store_module.UsageRecord(**base)

    def test_creates_db_and_parents(self):
        self.assertTrue(self.db.exists())
        self.assertEqual(self.store.get_meta("schema_version"), "1")

    def test_record_and_count(self):
        self.assertTrue(self.store.record(self.rec(input_tokens=100, output_tokens=20)))
        self.assertEqual(self.store.count(), 1)

    def test_dedupe_key_is_idempotent(self):
        self.assertTrue(self.store.record(self.rec(dedupe_key="k1", input_tokens=5)))
        self.assertFalse(self.store.record(self.rec(dedupe_key="k1", input_tokens=5)))
        self.assertEqual(self.store.count(), 1)

    def test_null_dedupe_keys_do_not_collide(self):
        self.assertTrue(self.store.record(self.rec(input_tokens=1)))
        self.assertTrue(self.store.record(self.rec(input_tokens=2)))
        self.assertEqual(self.store.count(), 2)

    def test_summary_cache_hit_ratio(self):
        self.store.record(self.rec(input_tokens=400, cache_creation_tokens=100,
                                   cache_read_tokens=500, output_tokens=50))
        s = self.store.summary()
        self.assertEqual(s["requests"], 1)
        self.assertEqual(s["total_input_tokens"], 1000)
        self.assertAlmostEqual(s["cache_hit_ratio"], 0.5)

    def test_summary_empty_is_zero_not_error(self):
        s = self.store.summary()
        self.assertEqual(s["requests"], 0)
        self.assertEqual(s["cache_hit_ratio"], 0.0)

    def test_by_source_and_by_model(self):
        self.store.record(self.rec(source="claude", model="claude-opus-4-8", input_tokens=10))
        self.store.record(self.rec(source="codex", ingest="log", model="gpt-5-codex",
                                   input_tokens=20, cache_read_tokens=5))
        by_src = {r["source"]: r for r in self.store.by_source()}
        self.assertEqual(by_src["claude"]["requests"], 1)
        self.assertEqual(by_src["codex"]["cache_read_tokens"], 5)
        models = {r["model"] for r in self.store.by_model()}
        self.assertEqual(models, {"claude-opus-4-8", "gpt-5-codex"})

    def test_recent_ordering_and_paging(self):
        for i in range(5):
            self.store.record(self.rec(input_tokens=i))
        recent = self.store.recent(limit=2)
        self.assertEqual(len(recent), 2)
        self.assertGreater(recent[0]["id"], recent[1]["id"])

    def test_meta_roundtrip(self):
        self.store.set_meta("offset:foo", "123")
        self.assertEqual(self.store.get_meta("offset:foo"), "123")
        self.store.set_meta("offset:foo", "456")
        self.assertEqual(self.store.get_meta("offset:foo"), "456")

    def test_record_dataclass_helpers(self):
        r = self.rec(input_tokens=400, cache_creation_tokens=100, cache_read_tokens=500)
        self.assertEqual(r.total_input(), 1000)
        self.assertAlmostEqual(r.cache_hit_ratio(), 0.5)


if __name__ == "__main__":
    unittest.main()
