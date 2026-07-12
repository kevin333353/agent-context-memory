import json
import tempfile
import unittest
from pathlib import Path

from scripts import context_memory_session_guard as guard


class ContextMemorySessionGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.transcript = self.root / "session.jsonl"
        self.state_path = self.root / "single-session-guard.json"
        self.config = {
            "enabled": True,
            "threshold_tokens": 40000,
            "min_growth_after_compact_tokens": 10000,
            "block_on_threshold": True,
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_record(self, record):
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def usage_record(self, request_id, total):
        return {
            "requestId": request_id,
            "message": {
                "usage": {
                    "input_tokens": 2,
                    "cache_creation_input_tokens": 559,
                    "cache_read_input_tokens": total - 561,
                }
            },
        }

    def test_blocks_at_threshold_using_provider_input_side_tokens(self):
        self.write_record(self.usage_record("r1", 44692))

        result = guard.evaluate_guard(
            self.transcript, self.state_path, self.config, "continue"
        )

        self.assertTrue(result["should_block"])
        self.assertEqual(result["observed_tokens"], 44692)
        self.assertEqual(result["effective_threshold"], 40000)
        self.assertEqual(result["reason"], "threshold")

    def test_below_threshold_allows_prompt(self):
        self.write_record(self.usage_record("r1", 39999))

        result = guard.evaluate_guard(
            self.transcript, self.state_path, self.config, "continue"
        )

        self.assertFalse(result["should_block"])
        self.assertEqual(result["reason"], "below_threshold")

    def test_duplicate_request_id_is_counted_once(self):
        self.write_record(self.usage_record("same", 44692))
        self.write_record(self.usage_record("same", 1000))

        latest = guard.latest_provider_usage(self.transcript)

        self.assertEqual(latest["tokens"], 44692)

    def test_malformed_tail_keeps_last_valid_usage(self):
        self.write_record(self.usage_record("r1", 41000))
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.write("{not-json\n")

        latest = guard.latest_provider_usage(self.transcript)

        self.assertEqual(latest["tokens"], 41000)

    def test_missing_transcript_fails_open(self):
        result = guard.evaluate_guard(
            self.root / "missing.jsonl", self.state_path, self.config, "continue"
        )

        self.assertFalse(result["should_block"])
        self.assertEqual(result["reason"], "missing_transcript")

    def test_missing_usage_fails_open(self):
        self.write_record({"type": "user", "message": {"content": "hello"}})

        result = guard.evaluate_guard(
            self.transcript, self.state_path, self.config, "continue"
        )

        self.assertFalse(result["should_block"])
        self.assertEqual(result["reason"], "missing_usage")

    def test_disabled_guard_preserves_current_behavior(self):
        self.write_record(self.usage_record("r1", 90000))
        config = dict(self.config, enabled=False)

        result = guard.evaluate_guard(
            self.transcript, self.state_path, config, "continue"
        )

        self.assertFalse(result["should_block"])
        self.assertEqual(result["reason"], "disabled")

    def test_compact_slash_command_is_never_blocked(self):
        self.write_record(self.usage_record("r1", 90000))

        result = guard.evaluate_guard(
            self.transcript,
            self.state_path,
            self.config,
            "/compact preserve decisions and next steps",
        )

        self.assertFalse(result["should_block"])
        self.assertEqual(result["reason"], "compact_command")

    def test_compact_boundary_learns_baseline_and_requires_growth(self):
        self.write_record(self.usage_record("before", 90000))
        boundary = guard.mark_compact_boundary(
            self.transcript, self.state_path, "post_compact"
        )
        self.assertEqual(boundary["compact_offset"], self.transcript.stat().st_size)

        no_usage = guard.evaluate_guard(
            self.transcript, self.state_path, self.config, "resubmit"
        )
        self.assertFalse(no_usage["should_block"])
        self.assertEqual(no_usage["reason"], "missing_usage")

        self.write_record(self.usage_record("baseline", 35000))
        baseline = guard.evaluate_guard(
            self.transcript, self.state_path, self.config, "resubmit"
        )
        self.assertFalse(baseline["should_block"])
        self.assertEqual(baseline["baseline_tokens"], 35000)
        self.assertEqual(baseline["effective_threshold"], 45000)

        self.write_record(self.usage_record("grown", 45000))
        grown = guard.evaluate_guard(
            self.transcript, self.state_path, self.config, "continue"
        )
        self.assertTrue(grown["should_block"])
        self.assertEqual(grown["effective_threshold"], 45000)

    def test_clear_resets_compact_and_usage_state(self):
        self.write_record(self.usage_record("r1", 44000))
        guard.evaluate_guard(self.transcript, self.state_path, self.config, "continue")

        state = guard.mark_compact_boundary(self.transcript, self.state_path, "clear")

        self.assertEqual(state["compact_offset"], self.transcript.stat().st_size)
        self.assertIsNone(state["post_compact_baseline_tokens"])
        self.assertIsNone(state["last_observed_tokens"])

    def test_invalid_state_is_replaced_and_prompt_text_is_not_stored(self):
        self.state_path.write_text("not-json", encoding="utf-8")
        self.write_record(self.usage_record("r1", 39000))
        prompt = "secret prompt text that must not be persisted"

        result = guard.evaluate_guard(
            self.transcript, self.state_path, self.config, prompt
        )
        state_text = self.state_path.read_text(encoding="utf-8")
        state = json.loads(state_text)

        self.assertFalse(result["should_block"])
        self.assertEqual(state["schema_version"], 1)
        self.assertNotIn(prompt, state_text)


if __name__ == "__main__":
    unittest.main()
