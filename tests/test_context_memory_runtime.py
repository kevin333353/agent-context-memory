import json
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

        self.assertEqual(migrated["schema_version"], 2)
        self.assertTrue(migrated["fill_table"]["worker"]["auto_run"])
        self.assertEqual(migrated["fill_table"]["worker"]["status"], "managed")
        self.assertTrue(migrated["fill_table"]["journal"]["capture_prompts"])
        self.assertEqual(
            migrated["fill_table"]["adapters"]["codex-cli"]["routine_model"],
            "custom-routine",
        )


if __name__ == "__main__":
    unittest.main()
