import json
import os
import subprocess
import sys
import tempfile
import unittest

from engine.update_prices import update

UPDATE_PRICES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "update_prices.py"
)


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

    def test_runs_as_a_direct_script_from_another_working_directory(self):
        # The documented usage is `python engine/update_prices.py
        # --provider ... --model ...`, invoked directly rather than
        # through `python -m unittest`. That code path only puts this
        # file's own directory on sys.path, so `from engine.prices
        # import append_entry` would fail with ModuleNotFoundError
        # unless the script bootstraps the repository root onto
        # sys.path itself. --help exits 0 without requiring any of the
        # other required arguments, and running from
        # tempfile.gettempdir() (never the repository root) proves the
        # fix does not depend on an inherited working directory.
        result = subprocess.run(
            [sys.executable, UPDATE_PRICES_PATH, "--help"],
            cwd=tempfile.gettempdir(),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertNotIn("ModuleNotFoundError", result.stderr)


if __name__ == "__main__":
    unittest.main()
