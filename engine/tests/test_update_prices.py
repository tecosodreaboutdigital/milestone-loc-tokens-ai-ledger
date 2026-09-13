import json
import os
import tempfile
import unittest

from engine.update_prices import update


class TestUpdatePrices(unittest.TestCase):
    def test_appends_a_new_entry_with_sources(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "prices.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([], fh)

            update(
                path, "anthropic", "claude-sonnet-5", "USD", "2026-09-13",
                3.0, 15.0, 0.3, 3.75,
                sources=[
                    {"name": "Anthropic pricing page", "url": "https://www.anthropic.com/pricing", "checked_at": "2026-09-13"},
                    {"name": "litellm", "url": "https://github.com/BerriAI/litellm", "checked_at": "2026-09-13"},
                ],
            )

            with open(path, encoding="utf-8") as fh:
                ledger = json.load(fh)
            self.assertEqual(len(ledger), 1)
            self.assertEqual(len(ledger[0]["entries"][0]["sources"]), 2)


if __name__ == "__main__":
    unittest.main()
