import os
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


try:
    from scripts import context_memory_dispatch as dispatch
except ImportError:
    dispatch = None

from scripts.context_memory_runtime import default_config, initialize_memory


TOOL_ROOT = Path(__file__).resolve().parents[1]


class ContextMemoryDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "-C", str(self.repo), "init", "--quiet"], check=True)
        self.memory_root = initialize_memory(
            self.repo, TOOL_ROOT, update_gitignore=False, origin="manual"
        )
        self.config = default_config()
        self.config["fill_table"]["worker"] = {"auto_run": True}
        self.config["fill_table"]["summary_interval_turns"] = 2

    def tearDown(self):
        self.temp_dir.cleanup()

    def require_dispatch(self):
        self.assertIsNotNone(dispatch, "scripts.context_memory_dispatch is missing")

    def event(self, event="user_prompt_submit"):
        return {
            "adapter": "codex-cli",
            "event": event,
            "framework_event": "UserPromptSubmit",
            "action": "inject",
            "cwd": str(self.repo),
            "prompt": "hello",
            "summary": "",
        }

    def test_threshold_dispatches_only_after_two_unprocessed_events(self):
        self.require_dispatch()
        launches = []
        launch = lambda *args: launches.append(args) or True

        first = dispatch.record_and_maybe_dispatch(
            self.memory_root, "codex-cli", self.event(), self.config, TOOL_ROOT, launch
        )
        second = dispatch.record_and_maybe_dispatch(
            self.memory_root, "codex-cli", self.event(), self.config, TOOL_ROOT, launch
        )
        third = dispatch.record_and_maybe_dispatch(
            self.memory_root, "codex-cli", self.event(), self.config, TOOL_ROOT, launch
        )

        self.assertFalse(first["dispatch_due"])
        self.assertTrue(second["dispatch_due"])
        self.assertTrue(second["worker_started"])
        self.assertFalse(third["worker_started"])
        self.assertEqual(third["dispatch_reason"], "cooldown")
        self.assertEqual(len(launches), 1)

    def test_post_compact_forces_dispatch(self):
        self.require_dispatch()
        self.config["fill_table"]["summary_interval_turns"] = 99

        result = dispatch.record_and_maybe_dispatch(
            self.memory_root,
            "codex-cli",
            self.event("post_compact"),
            self.config,
            TOOL_ROOT,
            lambda *args: True,
        )

        self.assertTrue(result["dispatch_due"])
        self.assertEqual(result["dispatch_reason"], "post_compact")

    def test_pre_compact_forces_dispatch(self):
        self.require_dispatch()
        self.config["fill_table"]["summary_interval_turns"] = 99

        result = dispatch.record_and_maybe_dispatch(
            self.memory_root,
            "claude-code",
            self.event("pre_compact"),
            self.config,
            TOOL_ROOT,
            lambda *args: True,
        )

        self.assertTrue(result["dispatch_due"])
        self.assertEqual(result["dispatch_reason"], "pre_compact")

    def test_synchronous_worker_uses_existing_locked_runner(self):
        self.require_dispatch()
        expected = {"status": "no_change", "cursor": 3}
        with patch.object(dispatch, "run_worker_locked", return_value=expected) as run:
            result = dispatch.run_worker_synchronously(
                self.memory_root, "claude-code", TOOL_ROOT
            )

        self.assertEqual(result, expected)
        run.assert_called_once_with(self.memory_root, "claude-code", TOOL_ROOT)

    def test_synchronous_worker_honors_disable_environment(self):
        self.require_dispatch()
        with patch.dict(os.environ, {"CONTEXT_MEMORY_DISABLE_WORKER_DISPATCH": "1"}):
            with patch.object(dispatch, "run_worker_locked") as run:
                result = dispatch.run_worker_synchronously(
                    self.memory_root, "claude-code", TOOL_ROOT
                )

        self.assertEqual(result["status"], "disabled_env")
        run.assert_not_called()

    def test_disable_environment_prevents_launch_but_still_journals(self):
        self.require_dispatch()
        self.config["fill_table"]["summary_interval_turns"] = 1
        with patch.dict(os.environ, {"CONTEXT_MEMORY_DISABLE_WORKER_DISPATCH": "1"}):
            result = dispatch.record_and_maybe_dispatch(
                self.memory_root,
                "codex-cli",
                self.event(),
                self.config,
                TOOL_ROOT,
                lambda *args: self.fail("worker should not launch"),
            )

        self.assertTrue(result["journaled"])
        self.assertTrue(result["dispatch_due"])
        self.assertFalse(result["worker_started"])
        self.assertEqual(result["dispatch_reason"], "disabled_env")

    def test_disabled_journal_records_nothing(self):
        self.require_dispatch()
        self.config["fill_table"]["journal"]["enabled"] = False

        result = dispatch.record_and_maybe_dispatch(
            self.memory_root,
            "codex-cli",
            self.event(),
            self.config,
            TOOL_ROOT,
            lambda *args: self.fail("worker should not launch"),
        )

        self.assertFalse(result["journaled"])
        self.assertEqual(result["dispatch_reason"], "journal_disabled")
        self.assertFalse((self.memory_root / "events.sqlite").exists())

    def test_disabled_fill_table_journals_without_dispatch(self):
        self.require_dispatch()
        self.config["fill_table"]["enabled"] = False

        result = dispatch.record_and_maybe_dispatch(
            self.memory_root,
            "codex-cli",
            self.event(),
            self.config,
            TOOL_ROOT,
            lambda *args: self.fail("worker should not launch"),
        )

        self.assertTrue(result["journaled"])
        self.assertEqual(result["dispatch_reason"], "fill_table_disabled")

    def test_custom_journal_path_is_honored(self):
        self.require_dispatch()
        self.config["fill_table"]["journal"]["path"] = ".cache/context-memory.sqlite"

        result = dispatch.record_and_maybe_dispatch(
            self.memory_root,
            "codex-cli",
            self.event(),
            self.config,
            TOOL_ROOT,
            lambda *args: True,
        )

        expected = self.repo / ".cache" / "context-memory.sqlite"
        self.assertTrue(result["journaled"])
        self.assertTrue(expected.exists())
        self.assertFalse((self.memory_root / "events.sqlite").exists())

    def test_event_payload_accepts_large_json_from_stdin(self):
        self.require_dispatch()
        event = self.event()
        event["prompt"] = "x" * 100_000

        parsed = dispatch.parse_event_payload(None, json.dumps(event))

        self.assertEqual(parsed["prompt"], event["prompt"])


if __name__ == "__main__":
    unittest.main()
