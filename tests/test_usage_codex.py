import json
import tempfile
import unittest
from pathlib import Path

try:
    from scripts.usage import codex_ingest as ci
    from scripts.usage import store as store_module
except ImportError:
    ci = None
    store_module = None


def token_event(ts, inp, cached, out):
    return {
        "timestamp": ts,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "last_token_usage": {
                    "input_tokens": inp,
                    "cached_input_tokens": cached,
                    "output_tokens": out,
                    "reasoning_output_tokens": 0,
                    "total_tokens": inp + out,
                },
                "total_token_usage": {"input_tokens": inp, "cached_input_tokens": cached},
            },
        },
    }


class CodexIngestTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(ci, "scripts.usage.codex_ingest is missing")
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sessions = self.root / "sessions" / "2026" / "07" / "14"
        self.sessions.mkdir(parents=True)
        self.rollout = self.sessions / "rollout-2026-07-14T00-00-00-aaaa-bbbb-cccc-dddd-eeee.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, lines):
        with self.rollout.open("w", encoding="utf-8") as fh:
            for obj in lines:
                fh.write(json.dumps(obj) + "\n")

    def test_normalize_subtracts_cached_from_input(self):
        n = ci.normalize_token_usage(
            {"input_tokens": 6300, "cached_input_tokens": 5888, "output_tokens": 335}
        )
        self.assertEqual(n["input_tokens"], 412)      # 6300 - 5888
        self.assertEqual(n["cache_read_tokens"], 5888)
        self.assertEqual(n["cache_creation_tokens"], 0)
        self.assertEqual(n["output_tokens"], 335)

    def test_iter_records_extracts_model_and_usage(self):
        self.write([
            {"type": "session_meta", "payload": {"id": "s1"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                  "model": "gpt-5-codex"}},
            token_event("2026-07-14T00:01:00Z", 6300, 5888, 335),
        ])
        recs = [r for _, r in ci.iter_records_from_rollout(self.rollout)]
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r.source, "codex")
        self.assertEqual(r.ingest, "log")
        self.assertEqual(r.model, "gpt-5-codex")
        self.assertEqual(r.input_tokens, 412)
        self.assertEqual(r.cache_read_tokens, 5888)
        self.assertEqual(r.output_tokens, 335)
        self.assertTrue(r.dedupe_key.startswith("codex:"))

    def test_tailer_ingests_and_is_idempotent(self):
        self.write([
            {"type": "session_meta", "payload": {"id": "s1"}},
            token_event("2026-07-14T00:01:00Z", 100, 50, 10),
            token_event("2026-07-14T00:02:00Z", 200, 120, 20),
        ])
        db = self.root / "usage.sqlite"
        store = store_module.UsageStore(db)
        try:
            tailer = ci.CodexTailer(store, sessions_root=self.root / "sessions")
            self.assertEqual(tailer.scan_once(), 2)
            # Re-scan: no new rows (offset + dedupe both hold).
            self.assertEqual(tailer.scan_once(), 0)
            self.assertEqual(store.count(), 2)

            # Append a new turn; only the new one is ingested.
            with self.rollout.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(token_event("2026-07-14T00:03:00Z", 300, 200, 30)) + "\n")
            self.assertEqual(tailer.scan_once(), 1)
            self.assertEqual(store.count(), 3)
        finally:
            store.close()

    def test_missing_sessions_root_is_safe(self):
        db = self.root / "usage.sqlite"
        store = store_module.UsageStore(db)
        try:
            tailer = ci.CodexTailer(store, sessions_root=self.root / "does-not-exist")
            self.assertEqual(tailer.scan_once(), 0)
        finally:
            store.close()

    def test_malformed_line_does_not_abort_scan(self):
        with self.rollout.open("w", encoding="utf-8") as fh:
            fh.write("{ this is not json\n")
            fh.write(json.dumps(token_event("2026-07-14T00:01:00Z", 100, 40, 10)) + "\n")
        recs = [r for _, r in ci.iter_records_from_rollout(self.rollout)]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].input_tokens, 60)


if __name__ == "__main__":
    unittest.main()
