import tempfile
import unittest
from pathlib import Path

import yaml


try:
    from scripts import context_memory_state as state_module
except ImportError:
    state_module = None


class ContextMemoryStateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def require_module(self):
        self.assertIsNotNone(state_module, "scripts.context_memory_state is missing")

    def valid_state(self):
        return {
            "schema_version": 1,
            "last_updated": "2026-07-11T00:00:00Z",
            "project": {"name": "demo", "root": ".", "goal": "ship"},
            "current_focus": {"task": "test", "status": "active", "next_step": "run"},
            "stable_context": [],
            "dynamic_context": [],
            "open_questions": [],
            "decisions": [],
            "files": [],
            "next_actions": [],
        }

    def yaml_text(self, state=None):
        return yaml.safe_dump(
            state or self.valid_state(), allow_unicode=True, sort_keys=False
        )

    def test_accepts_valid_state(self):
        self.require_module()
        parsed = state_module.validate_state_yaml(self.yaml_text(), 2000)
        self.assertEqual(parsed["schema_version"], 1)

    def test_rejects_wrong_top_level_types(self):
        self.require_module()
        state = self.valid_state()
        state["next_actions"] = "not-a-list"
        with self.assertRaisesRegex(ValueError, "next_actions must be a list"):
            state_module.validate_state_yaml(self.yaml_text(state), 2000)

    def test_rejects_missing_required_key(self):
        self.require_module()
        state = self.valid_state()
        del state["current_focus"]
        with self.assertRaisesRegex(ValueError, "missing keys: current_focus"):
            state_module.validate_state_yaml(self.yaml_text(state), 2000)

    def test_rejects_state_above_token_limit(self):
        self.require_module()
        state = self.valid_state()
        state["dynamic_context"] = ["x" * 12000]
        with self.assertRaisesRegex(ValueError, "token limit"):
            state_module.validate_state_yaml(self.yaml_text(state), 100)

    def test_rejects_unsafe_yaml_tag(self):
        self.require_module()
        text = "!!python/object/apply:os.system ['echo unsafe']\n"
        with self.assertRaisesRegex(ValueError, "invalid YAML"):
            state_module.validate_state_yaml(text, 2000)

    def test_atomic_write_creates_backup_and_replaces_state(self):
        self.require_module()
        path = self.root / "state.yaml"
        path.write_text(self.yaml_text(), encoding="utf-8")
        updated = self.valid_state()
        updated["current_focus"]["status"] = "done"
        updated_text = self.yaml_text(updated)

        backup = state_module.atomic_write_state(path, updated_text, backup_limit=3)

        self.assertIsNotNone(backup)
        self.assertTrue(backup.exists())
        self.assertEqual(path.read_text(encoding="utf-8"), updated_text)
        self.assertEqual(
            yaml.safe_load(backup.read_text(encoding="utf-8"))["current_focus"]["status"],
            "active",
        )

    def test_atomic_write_bounds_backup_count(self):
        self.require_module()
        path = self.root / "state.yaml"
        path.write_text(self.yaml_text(), encoding="utf-8")
        for index in range(4):
            state = self.valid_state()
            state["current_focus"]["status"] = str(index)
            state_module.atomic_write_state(path, self.yaml_text(state), backup_limit=2)

        self.assertEqual(len(list(self.root.glob("state.yaml.bak-*"))), 2)


if __name__ == "__main__":
    unittest.main()
