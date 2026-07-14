import unittest

import atlas_config


class AtlasConfigTests(unittest.TestCase):
    def test_approved_configuration(self):
        self.assertEqual(atlas_config.PROJECT, "Atlas")
        self.assertEqual(atlas_config.PORT, 4317)
        self.assertEqual(atlas_config.RETRY_COUNT, 4)
        self.assertEqual(atlas_config.BACKOFF_MS, [250, 500, 1000, 2000])
        self.assertEqual(atlas_config.DATABASE_MODE, "SQLite WAL")
        self.assertEqual(atlas_config.FEATURE_FLAG, "JOBSMITH_SAFE_EXPORT")
        self.assertEqual(atlas_config.RETENTION_DAYS, 30)
        self.assertEqual(atlas_config.OWNER, "Release Engineering")


if __name__ == "__main__":
    unittest.main()
