"""The optional ledger fields (cache_creation_1h_price, corrects, note),
the same-date correction mechanism, update_prices' new flags, and the
shipped ledger's own corrected content."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from engine.prices import append_entry, find_series, load_ledger, price_at
from engine.update_prices import update

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIPPED = os.path.join(HERE, "prices.json")
UPDATE_PRICES_PATH = os.path.join(HERE, "update_prices.py")

SOURCES = [
    {"name": "a", "url": "u1", "checked_at": "2026-09-19"},
    {"name": "b", "url": "u2", "checked_at": "2026-09-19"},
]
WRONG = {"input_price": 3.0, "output_price": 15.0, "cache_read_price": 0.3, "cache_creation_price": 3.75}
RIGHT = {"input_price": 2.0, "output_price": 10.0, "cache_read_price": 0.2, "cache_creation_price": 2.5,
         "cache_creation_1h_price": 4.0}


class TestOptionalEntryFields(unittest.TestCase):
    def ledger_path(self, tmpdir):
        return os.path.join(tmpdir, "prices.json")

    def test_an_entry_without_the_optional_fields_stays_valid_and_gets_none_of_them(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.ledger_path(tmpdir)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([], fh)
            append_entry(path, "anthropic", "m", "USD", "2026-09-13", WRONG, SOURCES)
            entry = load_ledger(path)[0]["entries"][0]
            for field in ("cache_creation_1h_price", "corrects", "note"):
                self.assertNotIn(field, entry)

    def test_optional_fields_are_recorded_when_given(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.ledger_path(tmpdir)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([], fh)
            append_entry(path, "anthropic", "m", "USD", "2026-09-13", RIGHT, SOURCES, note="a caveat")
            entry = load_ledger(path)[0]["entries"][0]
            self.assertEqual(entry["cache_creation_1h_price"], 4.0)
            self.assertEqual(entry["note"], "a caveat")
            self.assertNotIn("corrects", entry)

    def test_a_correcting_entry_for_the_same_date_is_appended_and_wins_price_at(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.ledger_path(tmpdir)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([], fh)
            append_entry(path, "anthropic", "m", "USD", "2026-09-13", WRONG, SOURCES)
            append_entry(
                path, "anthropic", "m", "USD", "2026-09-13", RIGHT, SOURCES,
                corrects="2026-09-13", note="the first entry recorded the wrong price",
            )
            series = find_series(load_ledger(path), "anthropic", "m")
            # The wrong entry stays, untouched, as a record.
            self.assertEqual(len(series["entries"]), 2)
            self.assertEqual(series["entries"][0]["input_price"], 3.0)
            self.assertNotIn("corrects", series["entries"][0])
            # The correcting entry keeps its insertion order after it...
            self.assertEqual(series["entries"][1]["input_price"], 2.0)
            self.assertEqual(series["entries"][1]["corrects"], "2026-09-13")
            # ...which is what lets price_at prefer it on the tie.
            self.assertEqual(price_at(series, "2026-09-13")["input_price"], 2.0)
            self.assertEqual(price_at(series, "2026-12-31")["input_price"], 2.0)

    def test_insertion_order_is_kept_for_equal_dates_even_beside_other_dates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.ledger_path(tmpdir)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([], fh)
            append_entry(path, "anthropic", "m", "USD", "2026-10-01", WRONG, SOURCES)
            append_entry(path, "anthropic", "m", "USD", "2026-09-13", WRONG, SOURCES)
            append_entry(path, "anthropic", "m", "USD", "2026-09-13", RIGHT, SOURCES, corrects="2026-09-13")
            entries = find_series(load_ledger(path), "anthropic", "m")["entries"]
            self.assertEqual([e["effective_date"] for e in entries], ["2026-09-13", "2026-09-13", "2026-10-01"])
            self.assertEqual(entries[1]["input_price"], 2.0)

    def test_corrects_must_match_an_existing_entry_in_the_series(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.ledger_path(tmpdir)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([], fh)
            append_entry(path, "anthropic", "m", "USD", "2026-09-13", WRONG, SOURCES)
            with self.assertRaises(ValueError):
                append_entry(path, "anthropic", "m", "USD", "2026-09-20", RIGHT, SOURCES, corrects="2026-09-20")
            with self.assertRaises(ValueError):
                append_entry(path, "anthropic", "other-model", "USD", "2026-09-13", RIGHT, SOURCES, corrects="2026-09-13")
            self.assertEqual(len(find_series(load_ledger(path), "anthropic", "m")["entries"]), 1)

    def test_a_correction_must_carry_the_date_it_corrects(self):
        # A correcting entry starting on another date would leave the
        # milestones in between on the wrong price.
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.ledger_path(tmpdir)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([], fh)
            append_entry(path, "anthropic", "m", "USD", "2026-09-13", WRONG, SOURCES)
            with self.assertRaises(ValueError):
                append_entry(path, "anthropic", "m", "USD", "2026-09-14", RIGHT, SOURCES, corrects="2026-09-13")

    def test_still_requires_a_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.ledger_path(tmpdir)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([], fh)
            with self.assertRaises(ValueError):
                append_entry(path, "anthropic", "m", "USD", "2026-09-13", RIGHT, [], corrects=None)


class TestUpdatePricesNewFlags(unittest.TestCase):
    def test_update_passes_the_new_fields_through(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "prices.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([], fh)
            update(path, "anthropic", "m", "USD", "2026-09-13", 3.0, 15.0, 0.3, 3.75, SOURCES)
            update(
                path, "anthropic", "m", "USD", "2026-09-13", 2.0, 10.0, 0.2, 2.5, SOURCES,
                cache_creation_1h_price=4.0, corrects="2026-09-13", note="wrong first time",
            )
            entries = load_ledger(path)[0]["entries"]
            self.assertNotIn("cache_creation_1h_price", entries[0])
            self.assertEqual(entries[1]["cache_creation_1h_price"], 4.0)
            self.assertEqual(entries[1]["corrects"], "2026-09-13")
            self.assertEqual(entries[1]["note"], "wrong first time")

    def test_the_cli_accepts_the_new_flags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "prices.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([], fh)
            base = [
                sys.executable, UPDATE_PRICES_PATH, "--ledger", path,
                "--provider", "anthropic", "--model", "m", "--effective-date", "2026-09-13",
                "--source-name", "one", "--source-url", "u1",
                "--source-name", "two", "--source-url", "u2",
                "--checked-at", "2026-09-19",
            ]
            first = subprocess.run(
                base + ["--input-price", "3", "--output-price", "15",
                        "--cache-read-price", "0.3", "--cache-creation-price", "3.75"],
                cwd=tempfile.gettempdir(), capture_output=True, text=True,
            )
            self.assertEqual(first.returncode, 0, msg=first.stderr)
            second = subprocess.run(
                base + ["--input-price", "2", "--output-price", "10",
                        "--cache-read-price", "0.2", "--cache-creation-price", "2.5",
                        "--cache-creation-1h-price", "4", "--corrects", "2026-09-13",
                        "--note", "first entry was wrong"],
                cwd=tempfile.gettempdir(), capture_output=True, text=True,
            )
            self.assertEqual(second.returncode, 0, msg=second.stderr)
            entries = load_ledger(path)[0]["entries"]
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[1]["cache_creation_1h_price"], 4.0)
            self.assertEqual(entries[1]["corrects"], "2026-09-13")
            self.assertEqual(entries[1]["note"], "first entry was wrong")
            self.assertEqual(len(entries[1]["sources"]), 2)

    def test_the_cli_reports_a_bad_corrects_date_as_an_error_not_a_silent_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "prices.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([], fh)
            result = subprocess.run(
                [sys.executable, UPDATE_PRICES_PATH, "--ledger", path,
                 "--provider", "anthropic", "--model", "m", "--effective-date", "2026-09-13",
                 "--input-price", "2", "--output-price", "10",
                 "--cache-read-price", "0.2", "--cache-creation-price", "2.5",
                 "--corrects", "2026-09-13",
                 "--source-name", "one", "--source-url", "u1", "--checked-at", "2026-09-19"],
                cwd=tempfile.gettempdir(), capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(load_ledger(path), [])


class TestShippedLedger(unittest.TestCase):
    # The shipped engine/prices.json is data, but a wrong number in it
    # is exactly the bug this file exists to catch.
    def setUp(self):
        self.ledger = load_ledger(SHIPPED)

    def test_every_entry_in_every_series_names_at_least_one_source(self):
        for series in self.ledger:
            for entry in series["entries"]:
                self.assertGreaterEqual(len(entry["sources"]), 1, msg=(series["model"], entry["effective_date"]))

    def test_sonnet_5_is_two_dollars_in_ten_out_from_its_first_day(self):
        series = find_series(self.ledger, "anthropic", "claude-sonnet-5")
        entry = price_at(series, "2026-09-13")
        self.assertEqual(
            (entry["input_price"], entry["output_price"], entry["cache_read_price"],
             entry["cache_creation_price"], entry["cache_creation_1h_price"]),
            (2.0, 10.0, 0.2, 2.5, 4.0),
        )
        self.assertEqual(entry["corrects"], "2026-09-13")
        self.assertIn("probable", entry["note"].lower())
        self.assertGreaterEqual(len(entry["sources"]), 2)

    def test_the_wrong_sonnet_5_entry_is_kept_as_a_record(self):
        series = find_series(self.ledger, "anthropic", "claude-sonnet-5")
        first = series["entries"][0]
        self.assertEqual((first["input_price"], first["output_price"]), (3.0, 15.0))
        self.assertNotIn("corrects", first)

    def test_opus_5_and_haiku_4_5_have_sourced_series(self):
        expected = {
            "claude-opus-5": (5.0, 25.0, 0.5, 6.25, 10.0),
            "claude-haiku-4-5-20251001": (1.0, 5.0, 0.1, 1.25, 2.0),
        }
        for model, prices in expected.items():
            series = find_series(self.ledger, "anthropic", model)
            self.assertIsNotNone(series, msg=model)
            entry = price_at(series, "2026-09-13")
            self.assertEqual(
                (entry["input_price"], entry["output_price"], entry["cache_read_price"],
                 entry["cache_creation_price"], entry["cache_creation_1h_price"]),
                prices, msg=model,
            )
            self.assertGreaterEqual(len(entry["sources"]), 2, msg=model)
            for source in entry["sources"]:
                self.assertTrue(source["url"].startswith("https://"), msg=model)


if __name__ == "__main__":
    unittest.main()
