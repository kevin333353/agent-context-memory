import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "provider-ab-benchmark.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("provider_ab_benchmark", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProviderAbBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def test_generate_history_contains_all_approved_facts(self):
        history = self.runner.generate_history(100)

        self.assertEqual(history.count("Archive item "), 100)
        for fact in self.runner.APPROVED_FACTS:
            self.assertIn(fact, history)

    def test_quality_passes_fenced_exact_answer(self):
        answer = "```json\n" + json.dumps(self.runner.EXPECTED) + "\n```"

        self.assertTrue(self.runner.answer_passes(answer))
        wrong = dict(self.runner.EXPECTED)
        wrong["port"] = 9999
        self.assertFalse(self.runner.answer_passes(json.dumps(wrong)))

    def test_parse_claude_result_sums_input_side_tokens(self):
        raw = json.dumps(
            {
                "result": json.dumps(self.runner.EXPECTED),
                "duration_ms": 123,
                "usage": {
                    "input_tokens": 2,
                    "cache_creation_input_tokens": 300,
                    "cache_read_input_tokens": 700,
                    "output_tokens": 50,
                },
            }
        )

        parsed = self.runner.parse_claude_result(raw)

        self.assertEqual(parsed["usage"]["input_tokens"], 1002)
        self.assertEqual(parsed["usage"]["cached_input_tokens"], 700)
        self.assertTrue(parsed["quality_pass"])

    def test_parse_codex_result_uses_completed_turn(self):
        raw = "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "abc"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "agent_message",
                            "text": json.dumps(self.runner.EXPECTED),
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 1200,
                            "cached_input_tokens": 800,
                            "output_tokens": 40,
                            "reasoning_output_tokens": 5,
                        },
                    }
                ),
            ]
        )

        parsed = self.runner.parse_codex_result(raw)

        self.assertEqual(parsed["thread_id"], "abc")
        self.assertEqual(parsed["usage"]["input_tokens"], 1200)
        self.assertEqual(parsed["usage"]["cached_input_tokens"], 800)
        self.assertTrue(parsed["quality_pass"])

    def test_build_case_prompts_and_summary(self):
        history = self.runner.generate_history(12)

        baseline_prompt, memory_prompt = self.runner.build_case_prompts(history)
        summary = self.runner.summarize_case(
            {
                "quality_pass": True,
                "usage": {"input_tokens": 1000, "cached_input_tokens": 100},
            },
            {
                "quality_pass": True,
                "usage": {"input_tokens": 250, "cached_input_tokens": 50},
            },
        )

        self.assertIn("FULL HISTORICAL TRANSCRIPT:\n", baseline_prompt)
        self.assertIn(history, baseline_prompt)
        self.assertNotIn("FULL HISTORICAL TRANSCRIPT:\n", memory_prompt)
        self.assertEqual(summary["saved_tokens"], 750)
        self.assertEqual(summary["saved_percent"], 75.0)
        self.assertTrue(summary["quality_pass"])

    def test_windows_resolver_prefers_cmd_wrapper(self):
        with patch.object(
            self.runner.shutil,
            "which",
            side_effect=[r"C:\\npm\\codex.cmd", r"C:\\npm\\codex"],
        ):
            executable = self.runner.resolve_executable("codex", windows=True)

        self.assertEqual(executable, r"C:\\npm\\codex.cmd")

    def test_provider_subprocess_uses_utf8_output(self):
        options = self.runner.provider_subprocess_options()

        self.assertTrue(options["text"])
        self.assertEqual(options["encoding"], "utf-8")
        self.assertEqual(options["errors"], "replace")

    def test_coding_mode_enables_workspace_tools(self):
        history = self.runner.generate_history(8)

        baseline_prompt, memory_prompt = self.runner.build_case_prompts(
            history, task="coding"
        )
        claude_command = self.runner.build_provider_command("claude", 1.0, "coding")
        codex_command = self.runner.build_provider_command("codex", 1.0, "coding")

        self.assertIn("atlas_config.py", baseline_prompt)
        self.assertIn(history, baseline_prompt)
        self.assertIn("atlas_config.py", memory_prompt)
        self.assertNotIn("--tools", claude_command)
        self.assertIn("--dangerously-skip-permissions", claude_command)
        self.assertIn("workspace-write", codex_command)


if __name__ == "__main__":
    unittest.main()
