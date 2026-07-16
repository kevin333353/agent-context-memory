import json
import tempfile
import unittest
from pathlib import Path

try:
    from scripts.usage import savings as savings_module
    from scripts.usage import store as store_module
except ImportError:
    savings_module = None
    store_module = None


SIMULATOR_JSON = {
    "estimate_kind": "offline_upper_bound_input_replay_estimate",
    "assumptions": {"turns": 30, "chars_per_turn_added_to_raw_transcript": 3000},
    "summary": {
        "baseline_total_tokens": 404250,
        "memory_total_tokens": 56245,
        "saved_total_tokens": 348005,
        "saved_percent": 86.09,
    },
    "all_turns": [
        {"turn": 1, "saved_percent": -314.66},
        {"turn": 2, "saved_percent": -40.0},
        {"turn": 30, "saved_percent": 92.38},
    ],
}

AB_JSON = {
    "provider": "claude",
    "task": "recall",
    "summary": {
        "baseline_input_tokens": 50000,
        "memory_input_tokens": 13000,
        "saved_tokens": 37000,
        "saved_percent": 74.0,
        "quality_pass": True,
    },
}


class SavingsParseTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(savings_module, "scripts.usage.savings is missing")

    def test_savings_from_simulator_maps_summary(self):
        r = savings_module.savings_from_simulator(
            SIMULATOR_JSON, memory_root="/repo", ts_utc="2026-07-14T00:00:00Z")
        self.assertEqual(r.kind, "simulate")
        self.assertAlmostEqual(r.saved_percent, 86.09)
        self.assertEqual(r.baseline_tokens, 404250)
        self.assertEqual(r.memory_tokens, 56245)
        self.assertEqual(r.memory_root, "/repo")

    def test_simulator_detail_includes_turn_series(self):
        r = savings_module.savings_from_simulator(SIMULATOR_JSON)
        detail = json.loads(r.detail)
        self.assertIn("turns", detail)
        self.assertEqual(detail["turns"][0]["t"], 1)
        self.assertAlmostEqual(detail["turns"][0]["p"], -314.66)
        self.assertEqual(detail["turns"][-1]["t"], 30)
        self.assertAlmostEqual(detail["turns"][-1]["p"], 92.38)

    def test_savings_from_ab_maps_summary_and_provider(self):
        r = savings_module.savings_from_ab(
            AB_JSON, memory_root="/repo", ts_utc="2026-07-14T00:00:00Z")
        self.assertEqual(r.kind, "ab")
        self.assertAlmostEqual(r.saved_percent, 74.0)
        self.assertEqual(r.baseline_tokens, 50000)
        self.assertEqual(r.memory_tokens, 13000)
        self.assertEqual(r.provider, "claude")
        self.assertEqual(r.task, "recall")
        self.assertEqual(r.quality_pass, 1)

    def test_savings_from_ab_quality_pass_absent_is_none(self):
        payload = {"provider": "codex", "task": "recall",
                   "summary": {"baseline_input_tokens": 100, "memory_input_tokens": 40,
                               "saved_percent": 60.0}}
        r = savings_module.savings_from_ab(payload)
        self.assertIsNone(r.quality_pass)

    def test_parsed_rows_persist_into_store(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = store_module.UsageStore(Path(tmp.name) / "usage.sqlite")
        self.addCleanup(store.close)
        store.record_savings(savings_module.savings_from_simulator(SIMULATOR_JSON))
        store.record_savings(savings_module.savings_from_ab(AB_JSON))
        latest = store.latest_savings()
        self.assertAlmostEqual(latest["simulate"]["saved_percent"], 86.09)
        self.assertAlmostEqual(latest["ab"]["saved_percent"], 74.0)
        self.assertEqual(latest["ab"]["provider"], "claude")


if __name__ == "__main__":
    unittest.main()
