import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts import context_memory_journal as journal
from scripts.context_memory_runtime import default_config


class ContextMemoryJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Path(self.temp_dir.name) / "events.sqlite"
        self.config = default_config()

    def tearDown(self):
        self.temp_dir.cleanup()

    def event(self, prompt="hello"):
        return {
            "adapter": "codex-cli",
            "event": "user_prompt_submit",
            "framework_event": "UserPromptSubmit",
            "action": "inject",
            "cwd": str(self.db.parent),
            "prompt": prompt,
            "summary": "",
        }

    def fetch_event(self, event_id):
        with sqlite3.connect(self.db) as con:
            con.row_factory = sqlite3.Row
            return dict(con.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone())

    def test_redacts_authorization_api_key_password_and_private_key(self):
        prompt = (
            "Authorization: Bearer abc123\n"
            "OPENAI_API_KEY=sk-secret-value\n"
            "password: hunter2\n"
            "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----"
        )

        event_id = journal.append_event(self.db, self.event(prompt), self.config)
        row = self.fetch_event(event_id)

        self.assertNotIn("abc123", row["prompt"])
        self.assertNotIn("sk-secret-value", row["prompt"])
        self.assertNotIn("hunter2", row["prompt"])
        self.assertNotIn("BEGIN PRIVATE KEY", row["prompt"])
        self.assertGreaterEqual(row["redaction_count"], 4)

    def test_capture_prompts_false_stores_no_prompt(self):
        self.config["fill_table"]["journal"]["capture_prompts"] = False

        event_id = journal.append_event(self.db, self.event("do not store"), self.config)

        self.assertEqual(self.fetch_event(event_id)["prompt"], "")

    def test_unprocessed_events_start_after_cursor(self):
        first = journal.append_event(self.db, self.event("one"), self.config)
        second = journal.append_event(self.db, self.event("two"), self.config)
        journal.update_worker_state(self.db, last_processed_event_id=first)

        events = journal.read_unprocessed_events(self.db, 10)

        self.assertEqual([item["id"] for item in events], [second])

    def test_retention_never_deletes_unprocessed_events(self):
        self.config["fill_table"]["journal"]["max_event_count"] = 1
        ids = [
            journal.append_event(self.db, self.event(str(index)), self.config)
            for index in range(3)
        ]

        with sqlite3.connect(self.db) as con:
            remaining = [row[0] for row in con.execute("SELECT id FROM events ORDER BY id")]

        self.assertEqual(remaining, ids)

    def test_retention_prunes_processed_events_first(self):
        config = default_config()
        config["fill_table"]["journal"]["max_event_count"] = 2
        first = journal.append_event(self.db, self.event("one"), config)
        journal.update_worker_state(self.db, last_processed_event_id=first)
        second = journal.append_event(self.db, self.event("two"), config)
        third = journal.append_event(self.db, self.event("three"), config)

        with sqlite3.connect(self.db) as con:
            remaining = [row[0] for row in con.execute("SELECT id FROM events ORDER BY id")]

        self.assertEqual(remaining, [second, third])

    def test_existing_v1_database_is_migrated(self):
        con = sqlite3.connect(self.db)
        try:
            con.execute(
                """
                CREATE TABLE events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts_utc TEXT NOT NULL,
                  protocol TEXT NOT NULL,
                  adapter TEXT NOT NULL,
                  event TEXT NOT NULL,
                  framework_event TEXT NOT NULL,
                  action TEXT NOT NULL,
                  cwd TEXT NOT NULL,
                  prompt TEXT NOT NULL,
                  summary TEXT NOT NULL,
                  payload_json TEXT NOT NULL
                )
                """
            )
            con.commit()
        finally:
            con.close()

        event_id = journal.append_event(self.db, self.event(), self.config)

        self.assertEqual(self.fetch_event(event_id)["redaction_count"], 0)
        self.assertEqual(journal.get_worker_state(self.db)["last_status"], "never_run")


if __name__ == "__main__":
    unittest.main()
