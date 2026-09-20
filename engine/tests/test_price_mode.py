"""price_mode: the explicit opt-in "flat" (price every token with the one
configured series, e.g. a project's own cost basis) beside the default
"per_model", in the config, in price_milestone, in a normal run and in
--reprice."""

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest

from engine.config import DEFAULT_PRICE_MODE, PRICE_MODES, load_config, price_mode
from engine.cost import price_milestone
from engine.generate_metrics import main, reprice_milestones
from engine.prices import load_ledger
from engine.readers.claude_code import encode_project_path

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIPPED = os.path.join(HERE, "prices.json")

BASE_CONFIG = {
    "milestone_folder": "logbook",
    "content_globs": ["*.md"],
    "code_globs": [],
    "exclude_globs": ["logbook/"],
    "transcript_reader": "claude_code",
    "price_provider": "anthropic",
    "price_model": "claude-sonnet-5",
    "currency": "USD",
}
SOURCES = [{"name": "s", "url": "u", "checked_at": "2026-09-19"}]


def entry(date, input_price, output_price, cache_read, cache_creation, one_hour=None):
    result = {"effective_date": date, "input_price": input_price, "output_price": output_price,
              "cache_read_price": cache_read, "cache_creation_price": cache_creation, "sources": SOURCES}
    if one_hour is not None:
        result["cache_creation_1h_price"] = one_hour
    return result


def series(model, provider="anthropic", *entries):
    return {"provider": provider, "model": model, "currency": "USD", "entries": list(entries)}


SONNET = series("claude-sonnet-5", "anthropic", entry("2026-09-13", 2.0, 10.0, 0.2, 2.5, one_hour=4.0))
OPUS = series("claude-opus-5", "anthropic", entry("2026-09-13", 5.0, 25.0, 0.5, 6.25, one_hour=10.0))
NO_1H = series("claude-old", "anthropic", entry("2026-09-13", 3.0, 15.0, 0.3, 3.75))
LEDGER = [SONNET, OPUS, NO_1H]


def fields(input_=0, output=0, cache_read=0, cache_creation=0, cache_creation_1h=0):
    return {"input": input_, "output": output, "cache_read": cache_read,
            "cache_creation": cache_creation, "cache_creation_1h": cache_creation_1h}


def two_model_tokens():
    """1M input by sonnet-5, 1M input by a model that has no series, plus
    500k input from usage rows that named no model."""
    tokens = fields(input_=2_500_000)
    tokens["by_model"] = {
        "claude-sonnet-5": fields(input_=1_000_000),
        "claude-mystery-9": fields(input_=1_000_000),
    }
    return tokens


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def quiet(fn, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(**kwargs)


class TestPriceModeConfig(unittest.TestCase):
    def load(self, **extra):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "config.json")
            write_json(path, {**BASE_CONFIG, **extra})
            return load_config(path)

    def test_the_modes_and_the_default(self):
        self.assertEqual(PRICE_MODES, ("per_model", "flat"))
        self.assertEqual(DEFAULT_PRICE_MODE, "per_model")

    def test_a_config_without_the_key_keeps_working_and_is_per_model(self):
        config = self.load()
        self.assertNotIn("price_mode", config, "loading never injects the key into the config")
        self.assertEqual(price_mode(config), "per_model")

    def test_flat_and_per_model_are_accepted(self):
        self.assertEqual(price_mode(self.load(price_mode="flat")), "flat")
        self.assertEqual(price_mode(self.load(price_mode="per_model")), "per_model")

    def test_anything_else_is_rejected_with_a_clear_error(self):
        for bad in ("Flat", "flat ", "per-model", "perModel", "", "auto", None, 1, True, ["flat"]):
            with self.assertRaises(ValueError, msg=repr(bad)) as raised:
                self.load(price_mode=bad)
            self.assertIn("price_mode", str(raised.exception), msg=repr(bad))
            self.assertIn('"flat"', str(raised.exception), msg=repr(bad))
            self.assertIn('"per_model"', str(raised.exception), msg=repr(bad))

    def test_the_provider_never_implies_a_mode(self):
        # `custom` is not auto-flat: flat is always an explicit choice.
        config = self.load(price_provider="custom", price_model="on-premise")
        self.assertEqual(price_mode(config), "per_model")

    def test_the_shipped_example_config_and_this_repos_own_config_stay_valid(self):
        for path in (os.path.join(HERE, "config.example.json"),
                     os.path.join(os.path.dirname(HERE), "logbook", "config.json")):
            self.assertEqual(price_mode(load_config(path)), "per_model", msg=path)


class TestPriceMilestonePriceMode(unittest.TestCase):
    def price(self, tokens, mode=None, ledger=None, provider="anthropic", model="claude-sonnet-5"):
        args = (tokens, ledger or LEDGER, provider, model, "2026-09-13", "USD")
        return price_milestone(*args) if mode is None else price_milestone(*args, price_mode=mode)

    def test_the_default_is_per_model(self):
        default = self.price(two_model_tokens())
        explicit = self.price(two_model_tokens(), "per_model")
        self.assertEqual(default, explicit)
        cost, unpriced = default
        self.assertEqual(cost["amount"], 2.0, "only the model that has a series is priced")
        self.assertEqual({u["model"] for u in unpriced}, {None, "claude-mystery-9"})

    def test_flat_prices_every_token_with_the_one_configured_series(self):
        cost, unpriced = self.price(two_model_tokens(), "flat")
        # 2.5M input at the configured claude-sonnet-5 rate of 2.00
        self.assertEqual(cost, {
            "amount": 5.0, "currency": "USD", "priced_at": "2026-09-13",
            "provider": "anthropic", "model": "claude-sonnet-5",
        })
        self.assertEqual(unpriced, [], "nothing is unpriced under flat, unnamed models and no-model rows included")
        self.assertNotIn("by_model", cost)

    def test_flat_with_a_flat_custom_series_prices_at_its_own_cost_basis(self):
        ledger = [series("on-premise", "custom", entry("2026-09-13", 0.5, 0.5, 0.0, 0.0))]
        tokens = two_model_tokens()
        per_model_cost, per_model_unpriced = self.price(tokens, "per_model", ledger, "custom", "on-premise")
        self.assertIsNone(per_model_cost)
        self.assertEqual(len(per_model_unpriced), 3, "the regression this option answers, still the default")
        cost, unpriced = self.price(tokens, "flat", ledger, "custom", "on-premise")
        self.assertEqual(cost["amount"], 1.25)
        self.assertEqual(cost["model"], "on-premise")
        self.assertEqual(unpriced, [])

    def test_flat_with_the_shipped_custom_series_prices_at_zero_not_unpriced(self):
        ledger = load_ledger(SHIPPED)
        cost, unpriced = self.price(two_model_tokens(), "flat", ledger, "custom", "on-premise")
        self.assertEqual(cost["amount"], 0.0)
        self.assertEqual(unpriced, [])

    def test_flat_still_splits_five_minute_from_one_hour_writes_with_the_series_one_hour_price(self):
        tokens = fields(cache_creation=1_000_000, cache_creation_1h=400_000)
        tokens["by_model"] = {"claude-opus-5": fields(cache_creation=1_000_000, cache_creation_1h=400_000)}
        cost, unpriced = self.price(tokens, "flat")
        # the configured series is sonnet-5: 600k at 2.50 + 400k at 4.00, not opus's rates
        self.assertEqual(cost["amount"], 3.1)
        self.assertEqual(unpriced, [])

    def test_flat_reports_one_hour_writes_unpriced_when_the_series_has_no_one_hour_price(self):
        tokens = fields(input_=1_000_000, cache_creation=1_000_000, cache_creation_1h=400_000)
        tokens["by_model"] = {"claude-sonnet-5": fields(input_=1_000_000, cache_creation=1_000_000, cache_creation_1h=400_000)}
        cost, unpriced = self.price(tokens, "flat", model="claude-old")
        self.assertEqual(cost["amount"], round(3.0 + 3.75 * 0.6, 4))
        self.assertEqual(len(unpriced), 1)
        self.assertEqual(unpriced[0]["tokens"], 400_000)
        self.assertEqual(unpriced[0]["model"], "claude-old")
        self.assertIn("cache_creation_1h_price", unpriced[0]["reason"])

    def test_flat_with_no_price_entry_for_the_date_is_unpriced_with_a_reason(self):
        cost, unpriced = price_milestone(
            two_model_tokens(), LEDGER, "anthropic", "claude-sonnet-5", "2026-01-01", "USD", price_mode="flat",
        )
        self.assertIsNone(cost)
        self.assertEqual(unpriced[0]["model"], "claude-sonnet-5")
        self.assertIn("2026-01-01", unpriced[0]["reason"])

    def test_flat_on_a_milestone_without_by_model_is_the_same_as_per_model(self):
        old_shape = {"input": 1_000_000, "output": 1_000_000, "cache_read": 0, "cache_creation": 0}
        self.assertEqual(self.price(old_shape, "flat"), self.price(old_shape, "per_model"))

    def test_an_invalid_mode_is_rejected_not_silently_treated_as_a_default(self):
        with self.assertRaises(ValueError):
            self.price(two_model_tokens(), "Flat")
        with self.assertRaises(ValueError):
            self.price(two_model_tokens(), "auto")


class TestRepricePriceMode(unittest.TestCase):
    def milestone(self):
        """A frozen milestone exactly as a normal per_model run recorded it."""
        tokens = two_model_tokens()
        cost, unpriced = price_milestone(tokens, LEDGER, "anthropic", "claude-sonnet-5", "2026-09-13", "USD")
        return {
            "date": "2026-09-13", "commit": "aaaaaaa", "subject": "s", "words_delta": 1, "loc_delta": 1,
            "tokens": tokens, "cost_recorded": cost, "unpriced": unpriced, "note": None,
        }

    def test_reprice_honours_flat_and_keeps_the_previous_cost_and_unpriced(self):
        m = self.milestone()
        changed = reprice_milestones([m], {**BASE_CONFIG, "price_mode": "flat"}, LEDGER, "t1")
        self.assertEqual(changed, 1)
        self.assertEqual(m["cost_recorded"]["amount"], 5.0)
        self.assertEqual(m["cost_recorded"]["model"], "claude-sonnet-5")
        self.assertNotIn("unpriced", m)
        self.assertEqual(m["repricings"][0]["previous_cost"]["amount"], 2.0)
        self.assertEqual(len(m["repricings"][0]["previous_unpriced"]), 2)
        self.assertEqual(m["tokens"], two_model_tokens(), "tokens are never touched")

    def test_flat_then_back_to_per_model_reprice_restores_the_per_model_view(self):
        m = self.milestone()
        reprice_milestones([m], {**BASE_CONFIG, "price_mode": "flat"}, LEDGER, "t1")
        reprice_milestones([m], {**BASE_CONFIG, "price_mode": "per_model"}, LEDGER, "t2")
        self.assertEqual(m["cost_recorded"]["amount"], 2.0)
        self.assertEqual(len(m["unpriced"]), 2)
        self.assertEqual([r["previous_cost"]["amount"] for r in m["repricings"]], [2.0, 5.0])

    def test_reprice_without_the_key_is_per_model_and_changes_nothing_here(self):
        m = self.milestone()
        self.assertEqual(reprice_milestones([m], dict(BASE_CONFIG), LEDGER, "t1"), 0)
        self.assertNotIn("repricings", m)

    def test_reprice_and_a_normal_price_agree_for_the_same_mode(self):
        for mode in PRICE_MODES:
            config = {**BASE_CONFIG, "price_mode": mode}
            m = self.milestone()
            m["cost_recorded"], m["unpriced"] = None, []
            reprice_milestones([m], config, LEDGER, "t")
            cost, unpriced = price_milestone(
                m["tokens"], LEDGER, "anthropic", "claude-sonnet-5", "2026-09-13", "USD", price_mode=mode,
            )
            self.assertEqual(m["cost_recorded"], cost, msg=mode)
            self.assertEqual(m.get("unpriced", []), unpriced, msg=mode)


class TestFlatEndToEnd(unittest.TestCase):
    def make_repo(self, tmpdir, price_mode_value):
        def git(*args):
            subprocess.run(["git", *args], cwd=tmpdir, check=True, capture_output=True)
        git("init")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "T")
        with open(os.path.join(tmpdir, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("hello world from the only milestone")
        git("add", "README.md")
        git("commit", "-m", "Only milestone")
        os.makedirs(os.path.join(tmpdir, "logbook"))
        config = dict(BASE_CONFIG)
        if price_mode_value is not None:
            config["price_mode"] = price_mode_value
        write_json(os.path.join(tmpdir, "logbook", "config.json"), config)

        from engine.git_source import commits
        iso = commits(tmpdir)[0]["iso"]
        projects_dir = os.path.join(tmpdir, "claude_projects")
        project_dir = os.path.join(projects_dir, encode_project_path(tmpdir))
        os.makedirs(project_dir)
        rows = [
            {"timestamp": iso, "message": {"id": "msg_1", "model": "claude-sonnet-5",
                                           "usage": {"input_tokens": 1_000_000}}},
            {"timestamp": iso, "message": {"id": "msg_2", "model": "claude-mystery-9",
                                           "usage": {"input_tokens": 1_000_000}}},
        ]
        with open(os.path.join(project_dir, "session.jsonl"), "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        ledger_path = os.path.join(tmpdir, "prices.json")
        write_json(ledger_path, [SONNET])
        return projects_dir, ledger_path, iso[:10]

    def run_main(self, tmpdir, projects_dir, ledger_path, **kwargs):
        from unittest.mock import patch
        with patch("engine.generate_metrics.SHIPPED_PRICES", ledger_path):
            quiet(main, repo_root=tmpdir, claude_projects_dir=projects_dir, **kwargs)
        return read_json(os.path.join(tmpdir, "logbook", "data.json"))["milestones"][0]

    def test_a_normal_run_prices_every_model_at_the_configured_series_when_flat(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir, ledger_path, day = self.make_repo(tmpdir, "flat")
            # the sonnet-5 series must be in effect on the commit's date
            write_json(ledger_path, [series("claude-sonnet-5", "anthropic", entry(day, 2.0, 10.0, 0.2, 2.5, one_hour=4.0))])
            m = self.run_main(tmpdir, projects_dir, ledger_path)
            self.assertEqual(m["cost_recorded"]["amount"], 4.0)
            self.assertEqual(m["cost_recorded"]["model"], "claude-sonnet-5")
            self.assertNotIn("unpriced", m)
            # by_model is still recorded: price_mode changes the pricing, not what is kept
            self.assertEqual(sorted(m["tokens"]["by_model"]), ["claude-mystery-9", "claude-sonnet-5"])

    def test_the_same_run_without_the_key_is_per_model_and_reports_the_gap(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir, ledger_path, day = self.make_repo(tmpdir, None)
            write_json(ledger_path, [series("claude-sonnet-5", "anthropic", entry(day, 2.0, 10.0, 0.2, 2.5, one_hour=4.0))])
            m = self.run_main(tmpdir, projects_dir, ledger_path)
            self.assertEqual(m["cost_recorded"]["amount"], 2.0)
            self.assertEqual([u["model"] for u in m["unpriced"]], ["claude-mystery-9"])

    def test_switching_the_config_to_flat_then_reprice_fills_the_frozen_milestone(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir, ledger_path, day = self.make_repo(tmpdir, None)
            write_json(ledger_path, [series("claude-sonnet-5", "anthropic", entry(day, 2.0, 10.0, 0.2, 2.5, one_hour=4.0))])
            first = self.run_main(tmpdir, projects_dir, ledger_path)
            self.assertEqual(first["cost_recorded"]["amount"], 2.0)

            write_json(os.path.join(tmpdir, "logbook", "config.json"), {**BASE_CONFIG, "price_mode": "flat"})
            from unittest.mock import patch
            with patch("engine.generate_metrics.SHIPPED_PRICES", ledger_path):
                # a normal run never changes a frozen cost, whatever the mode
                after_normal = self.run_main(tmpdir, projects_dir, ledger_path)
                self.assertEqual(after_normal["cost_recorded"]["amount"], 2.0)
                quiet(main, repo_root=tmpdir, reprice=True)
            m = read_json(os.path.join(tmpdir, "logbook", "data.json"))["milestones"][0]
            self.assertEqual(m["cost_recorded"]["amount"], 4.0)
            self.assertNotIn("unpriced", m)
            self.assertEqual(m["repricings"][0]["previous_cost"]["amount"], 2.0)

    def test_an_invalid_price_mode_stops_the_run_with_the_config_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir, ledger_path, _ = self.make_repo(tmpdir, "Flat")
            with self.assertRaises(ValueError) as raised:
                quiet(main, repo_root=tmpdir, claude_projects_dir=projects_dir)
            self.assertIn("price_mode", str(raised.exception))
            with self.assertRaises(ValueError):
                quiet(main, repo_root=tmpdir, reprice=True)


if __name__ == "__main__":
    unittest.main()
