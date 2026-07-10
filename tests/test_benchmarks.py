import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


report = load_script(
    "claude_code_usage_report", "benchmarks/claude-code-usage-report.py"
)


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_transcript(self, name, messages):
        path = self.root / name
        lines = []
        for message_type, text in messages:
            lines.append(
                json.dumps(
                    {"type": message_type, "message": {"content": text}},
                    ensure_ascii=False,
                )
            )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_replay_resets_running_context_for_each_transcript(self):
        first = self.write_transcript(
            "one.jsonl", [("user", "alpha"), ("assistant", "beta")]
        )
        second = self.write_transcript("two.jsonl", [("user", "gamma")])

        result = report.replay_transcript_tokens([first, second], len)

        self.assertEqual(len(result["per_transcript"]), 2)
        self.assertEqual(
            result["per_transcript"][1]["baseline_replay_total_tokens"], 0
        )
        self.assertEqual(
            result["baseline_replay_total_tokens"],
            sum(
                row["baseline_replay_total_tokens"]
                for row in result["per_transcript"]
            ),
        )


if __name__ == "__main__":
    unittest.main()
