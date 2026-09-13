import json
import os
import tempfile
import unittest

from engine.prices import append_entry, find_series, load_ledger, price_at


class TestPriceLedger(unittest.TestCase):
    def test_load_ledger_reads_the_shipped_file(self):
        here = os.path.dirname(os.path.dirname(__file__))
        ledger = load_ledger(os.path.join(here, "prices.json"))
        self.assertTrue(any(s["provider"] == "anthropic" for s in ledger))

    def test_find_series_returns_none_when_absent(self):
        self.assertIsNone(find_series([], "anthropic", "claude-sonnet-5"))

    def test_price_at_picks_the_latest_entry_on_or_before_the_date(self):
        series = {
            "provider": "anthropic", "model": "claude-sonnet-5", "currency": "USD",
            "entries": [
                {"effective_date": "2026-01-01", "input_price": 5.0, "output_price": 20.0,
                 "cache_read_price": 0.5, "cache_creation_price": 6.0, "sources": []},
                {"effective_date": "2026-09-13", "input_price": 3.0, "output_price": 15.0,
                 "cache_read_price": 0.3, "cache_creation_price": 3.75, "sources": []},
            ],
        }
        self.assertEqual(price_at(series, "2026-05-01")["input_price"], 5.0)
        self.assertEqual(price_at(series, "2026-09-13")["input_price"], 3.0)
        self.assertEqual(price_at(series, "2026-12-31")["input_price"], 3.0)

    def test_price_at_returns_none_before_any_entry(self):
        series = {
            "provider": "x", "model": "y", "currency": "USD",
            "entries": [{"effective_date": "2026-09-13", "input_price": 1, "output_price": 1,
                         "cache_read_price": 0, "cache_creation_price": 0, "sources": []}],
        }
        self.assertIsNone(price_at(series, "2026-01-01"))

    def test_append_entry_never_removes_an_existing_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "prices.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([{
                    "provider": "anthropic", "model": "claude-sonnet-5", "currency": "USD",
                    "entries": [{"effective_date": "2026-01-01", "input_price": 5.0,
                                 "output_price": 20.0, "cache_read_price": 0.5,
                                 "cache_creation_price": 6.0, "sources": []}],
                }], fh)

            append_entry(
                path, "anthropic", "claude-sonnet-5", "USD", "2026-09-13",
                {"input_price": 3.0, "output_price": 15.0, "cache_read_price": 0.3, "cache_creation_price": 3.75},
                [{"name": "a", "url": "u1", "checked_at": "2026-09-13"},
                 {"name": "b", "url": "u2", "checked_at": "2026-09-13"}],
            )

            ledger = load_ledger(path)
            series = find_series(ledger, "anthropic", "claude-sonnet-5")
            self.assertEqual(len(series["entries"]), 2)
            self.assertEqual(series["entries"][0]["effective_date"], "2026-01-01")
            self.assertEqual(series["entries"][1]["effective_date"], "2026-09-13")

    def test_append_entry_requires_at_least_one_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "prices.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([], fh)
            with self.assertRaises(ValueError):
                append_entry(
                    path, "anthropic", "claude-sonnet-5", "USD", "2026-09-13",
                    {"input_price": 3.0, "output_price": 15.0, "cache_read_price": 0.3, "cache_creation_price": 3.75},
                    [],
                )


if __name__ == "__main__":
    unittest.main()
