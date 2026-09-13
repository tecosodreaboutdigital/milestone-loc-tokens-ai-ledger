import json
import os
import tempfile
import unittest

from engine.config import load_config, REQUIRED_KEYS


class TestLoadConfig(unittest.TestCase):
    def write_config(self, tmpdir, data):
        path = os.path.join(tmpdir, "config.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path

    def test_loads_a_complete_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.write_config(tmpdir, {
                "milestone_folder": "logbook",
                "content_globs": ["*.md"],
                "code_globs": ["engine/"],
                "exclude_globs": ["logbook/"],
                "transcript_reader": "claude_code",
                "price_provider": "anthropic",
                "price_model": "claude-sonnet-5",
                "currency": "USD",
            })
            config = load_config(path)
            self.assertEqual(config["milestone_folder"], "logbook")
            self.assertEqual(config["price_provider"], "anthropic")

    def test_missing_file_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            load_config("/no/such/path/config.json")

    def test_missing_required_key_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data = {k: "x" for k in REQUIRED_KEYS if k != "currency"}
            path = self.write_config(tmpdir, data)
            with self.assertRaises(ValueError) as ctx:
                load_config(path)
            self.assertIn("currency", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
