import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts import context_memory_journal as journal
from scripts import fill_table_worker as worker
from scripts.context_memory_runtime import initialize_memory, load_config


TOOL_ROOT = Path(__file__).resolve().parents[1]


class FillTableWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "-C", str(self.repo), "init", "--quiet"], check=True)
        self.memory_root = initialize_memory(
            self.repo, TOOL_ROOT, update_gitignore=False, origin="manual"
        )
        self.db = self.memory_root / "events.sqlite"
        self.config = load_config(self.memory_root / "config.yaml")
        self.add_event("implement reliable memory")

    def tearDown(self):
        self.temp_dir.cleanup()

    def add_event(self, prompt):
        return journal.append_event(
            self.db,
            {
                "adapter": "codex-cli",
                "event": "user_prompt_submit",
                "framework_event": "UserPromptSubmit",
                "action": "inject",
                "cwd": str(self.repo),
                "prompt": prompt,
                "summary": "",
            },
            self.config,
        )

    def valid_state_yaml(self, status="updated"):
        state = yaml.safe_load(
            (self.memory_root / "state.yaml").read_text(encoding="utf-8-sig")
        )
        state["last_updated"] = "2026-07-11T00:00:00Z"
        state["current_focus"]["status"] = status
        return yaml.safe_dump(state, allow_unicode=True, sort_keys=False)

    def valid_model_json(self):
        return json.dumps(
            {"state_yaml": self.valid_state_yaml(), "notes": ["updated"]},
            ensure_ascii=False,
        )

    def test_nested_cwd_reads_journal_from_project_root(self):
        nested = self.repo / "src" / "feature"
        nested.mkdir(parents=True)

        report = worker.run_worker(
            nested, "codex-cli", live=False, apply=False
        )

        self.assertEqual(Path(report["journal_path"]), self.db.resolve())
        self.assertEqual(len(report["events"]), 1)

    def test_invalid_routine_output_retries_then_uses_repair_model(self):
        calls = []
        outputs = iter(["not-json", "{bad", self.valid_model_json()])

        def invoke(adapter, model, prompt, config, cwd):
            calls.append(model)
            return next(outputs), f"stub {model}"

        report = worker.run_worker(
            self.repo, "codex-cli", live=True, apply=True, invoke_model=invoke
        )

        self.assertEqual(calls, ["gpt-5-nano", "gpt-5-nano", "gpt-5-mini"])
        self.assertEqual(report["status"], "updated")
        self.assertEqual(journal.get_worker_state(self.db)["last_status"], "updated")

    def test_no_change_advances_cursor(self):
        event_id = journal.read_unprocessed_events(self.db, 10)[-1]["id"]

        report = worker.run_worker(
            self.repo,
            "codex-cli",
            live=True,
            apply=True,
            invoke_model=lambda *args: ('{"no_change":true,"notes":[]}', "stub"),
        )

        self.assertEqual(report["status"], "no_change")
        self.assertEqual(
            journal.get_worker_state(self.db)["last_processed_event_id"], event_id
        )

    def test_failed_attempt_does_not_advance_cursor(self):
        before = journal.get_worker_state(self.db)["last_processed_event_id"]

        with self.assertRaises(ValueError):
            worker.run_worker(
                self.repo,
                "codex-cli",
                live=True,
                apply=True,
                invoke_model=lambda *args: ("invalid", "stub"),
            )

        state = journal.get_worker_state(self.db)
        self.assertEqual(state["last_processed_event_id"], before)
        self.assertEqual(state["last_status"], "failed")
        self.assertTrue(state["last_error"])


if __name__ == "__main__":
    unittest.main()
