import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


try:
    from scripts import context_memory_runtime as runtime
except ImportError:
    runtime = None


class ContextMemoryRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.tool_root = self.root / "tool"
        self.tool_root.mkdir()
        templates = self.tool_root / "templates" / ".context-memory"
        (templates / "handoff").mkdir(parents=True)
        (templates / "schema.yaml").write_text("schema_version: 1\n", encoding="utf-8")
        (templates / "config.yaml").write_text("schema_version: 1\n", encoding="utf-8")
        (templates / "project.yaml").write_text(
            'schema_version: 1\nproject:\n  name: ""\n  root: "."\n',
            encoding="utf-8",
        )
        (templates / "handoff" / "README.md").write_text(
            "# Context Memory Handoff\n", encoding="utf-8"
        )
        self.config = runtime.default_config() if runtime else None
        if self.config:
            self.config["auto_init"]["exclude_temp_roots"] = False

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_git_repo(self, name="repo"):
        repo = self.root / name
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "--quiet"], check=True)
        return repo.resolve()

    def require_runtime(self):
        self.assertIsNotNone(runtime, "scripts.context_memory_runtime is missing")

    def test_eligible_nested_git_directory_resolves_repo_root(self):
        self.require_runtime()
        repo = self.make_git_repo()
        nested = repo / "src" / "feature"
        nested.mkdir(parents=True)

        eligible, root, reason = runtime.is_auto_init_eligible(
            nested, self.tool_root, self.config
        )

        self.assertTrue(eligible)
        self.assertEqual(root, repo)
        self.assertEqual(reason, "eligible")

    def test_disabled_repo_is_not_auto_initialized(self):
        self.require_runtime()
        repo = self.make_git_repo()
        (repo / ".context-memory-disabled").write_text("", encoding="utf-8")

        eligible, root, reason = runtime.is_auto_init_eligible(
            repo, self.tool_root, self.config
        )

        self.assertFalse(eligible)
        self.assertEqual(root, repo)
        self.assertEqual(reason, "disabled_marker")

    def test_non_git_directory_is_not_eligible(self):
        self.require_runtime()
        directory = self.root / "plain"
        directory.mkdir()

        eligible, root, reason = runtime.is_auto_init_eligible(
            directory, self.tool_root, self.config
        )

        self.assertFalse(eligible)
        self.assertIsNone(root)
        self.assertEqual(reason, "not_git")

    def test_tool_repo_is_not_eligible(self):
        self.require_runtime()
        subprocess.run(
            ["git", "-C", str(self.tool_root), "init", "--quiet"], check=True
        )

        eligible, root, reason = runtime.is_auto_init_eligible(
            self.tool_root, self.tool_root, self.config
        )

        self.assertFalse(eligible)
        self.assertEqual(root, self.tool_root.resolve())
        self.assertEqual(reason, "tool_repo")

    def test_initialize_memory_is_idempotent_and_records_origin(self):
        self.require_runtime()
        repo = self.make_git_repo()

        memory_root = runtime.initialize_memory(
            repo, self.tool_root, update_gitignore=True, origin="hook_auto"
        )
        state_before = (memory_root / "state.yaml").read_text(encoding="utf-8")
        runtime.initialize_memory(
            repo, self.tool_root, update_gitignore=True, origin="hook_auto"
        )

        self.assertEqual(
            (memory_root / "state.yaml").read_text(encoding="utf-8"), state_before
        )
        metadata = json.loads((memory_root / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["initialization_origin"], "hook_auto")
        gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".context-memory/events.sqlite", gitignore)
        self.assertIn("!.context-memory/schema.yaml", gitignore)

    def test_migrates_v1_config_and_enables_managed_worker(self):
        self.require_runtime()
        config_path = self.root / "config.yaml"
        config_path.write_text(
            """schema_version: 1
fill_table:
  worker:
    auto_run: false
    status: not_installed
  adapters:
    codex-cli:
      routine_model: custom-routine
""",
            encoding="utf-8",
        )

        migrated = runtime.migrate_config_file(config_path)

        self.assertEqual(migrated["schema_version"], 3)
        self.assertTrue(migrated["fill_table"]["worker"]["auto_run"])
        self.assertEqual(migrated["fill_table"]["worker"]["status"], "managed")
        self.assertTrue(migrated["fill_table"]["journal"]["capture_prompts"])
        self.assertEqual(
            migrated["fill_table"]["adapters"]["codex-cli"]["routine_model"],
            "custom-routine",
        )

    def test_schema_three_adds_disabled_single_session_guard(self):
        self.require_runtime()
        config_path = self.root / "config.yaml"
        config_path.write_text("schema_version: 2\n", encoding="utf-8")

        migrated = runtime.migrate_config_file(config_path)

        self.assertEqual(migrated["schema_version"], 3)
        self.assertEqual(
            migrated["single_session_guard"],
            {
                "enabled": False,
                "threshold_tokens": 40000,
                "min_growth_after_compact_tokens": 10000,
                "block_on_threshold": True,
                "auto_compact_window_tokens": 100000,
            },
        )

    def test_single_session_enable_is_idempotent_and_disable_restores_settings(self):
        self.require_runtime()
        repo = self.make_git_repo()
        memory_root = repo / ".context-memory"
        settings_path = repo / ".claude" / "settings.local.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(
            json.dumps(
                {
                    "permissions": {"allow": ["Read"]},
                    "autoCompactWindow": 200000,
                }
            ),
            encoding="utf-8",
        )

        enabled = runtime.configure_single_session(
            repo, self.tool_root, "enable", 45000
        )
        enabled_again = runtime.configure_single_session(
            repo, self.tool_root, "enable", 45000
        )

        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        state = json.loads(
            (memory_root / "single-session-guard.json").read_text(
                encoding="utf-8"
            )
        )
        config = runtime.load_config(memory_root / "config.yaml")
        self.assertEqual(enabled["action"], "enabled")
        self.assertEqual(enabled_again["action"], "enabled")
        self.assertEqual(settings["permissions"], {"allow": ["Read"]})
        self.assertEqual(settings["autoCompactWindow"], 100000)
        self.assertEqual(state["settings_ownership"]["previous_value"], 200000)
        self.assertTrue(state["settings_ownership"]["previous_existed"])
        self.assertTrue(config["single_session_guard"]["enabled"])
        self.assertEqual(config["single_session_guard"]["threshold_tokens"], 45000)

        disabled = runtime.configure_single_session(
            repo, self.tool_root, "disable", 40000
        )

        restored = json.loads(settings_path.read_text(encoding="utf-8"))
        self.assertEqual(disabled["action"], "disabled")
        self.assertTrue(disabled["settings_restored"])
        self.assertEqual(restored["autoCompactWindow"], 200000)
        self.assertEqual(restored["permissions"], {"allow": ["Read"]})

    def test_single_session_disable_preserves_user_changed_setting(self):
        self.require_runtime()
        repo = self.make_git_repo()
        runtime.configure_single_session(repo, self.tool_root, "enable", 40000)
        settings_path = repo / ".claude" / "settings.local.json"
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        settings["autoCompactWindow"] = 300000
        settings_path.write_text(json.dumps(settings), encoding="utf-8")

        disabled = runtime.configure_single_session(
            repo, self.tool_root, "disable", 40000
        )

        preserved = json.loads(settings_path.read_text(encoding="utf-8"))
        self.assertFalse(disabled["settings_restored"])
        self.assertTrue(disabled["settings_preserved"])
        self.assertEqual(preserved["autoCompactWindow"], 300000)

    def test_single_session_status_reports_runtime_state(self):
        self.require_runtime()
        repo = self.make_git_repo()
        memory_root = repo / ".context-memory"
        runtime.configure_single_session(repo, self.tool_root, "enable", 40000)
        state_path = memory_root / "single-session-guard.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["last_observed_tokens"] = 44692
        state["post_compact_baseline_tokens"] = 35000
        state_path.write_text(json.dumps(state), encoding="utf-8")

        status = runtime.configure_single_session(
            repo, self.tool_root, "status", 40000
        )

        self.assertTrue(status["enabled"])
        self.assertEqual(status["threshold_tokens"], 40000)
        self.assertEqual(status["last_observed_tokens"], 44692)
        self.assertEqual(status["post_compact_baseline_tokens"], 35000)
        self.assertEqual(status["effective_threshold"], 45000)
        self.assertTrue(status["auto_compact_managed"])

    def test_exclusive_lock_recovers_stale_file(self):
        self.require_runtime()
        lock_path = self.root / "stale.lock"
        lock_path.write_text("123", encoding="ascii")
        stale_time = lock_path.stat().st_mtime - 700
        os.utime(lock_path, (stale_time, stale_time))

        with runtime.exclusive_lock(lock_path, timeout_seconds=0.2):
            self.assertTrue(lock_path.exists())

        self.assertFalse(lock_path.exists())


if __name__ == "__main__":
    unittest.main()
