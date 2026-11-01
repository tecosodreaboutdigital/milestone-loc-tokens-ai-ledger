"""Rollups over the per-commit ledger: totals per day and per named
milestone (a range of commits declared in config.json), plus every
commit's cost at today's price beside its frozen recorded cost."""

import unittest

from engine.config import named_milestones
from engine.rollup import group_by_day, group_by_named_milestone


def _commit(commit, date, words=0, loc=0, tokens=0, recorded=None, today=None, unpriced=False):
    m = {
        "date": date,
        "commit": commit,
        "subject": "s",
        "words_delta": words,
        "loc_delta": loc,
        "tokens": {"input": tokens, "output": 0, "cache_read": 0, "cache_creation": 0},
        "cost_recorded": None if recorded is None else {"amount": recorded, "currency": "USD"},
        "cost_at_today": None if today is None else {"amount": today, "currency": "USD"},
    }
    if unpriced:
        m["unpriced"] = [{"model": "x", "tokens": 1, "reason": "no series"}]
    return m


LEDGER = [
    _commit("a000001", "2026-11-01", words=10, loc=5, tokens=100, recorded=1.0, today=1.5),
    _commit("a000002", "2026-11-01", words=-2, loc=20, tokens=50, recorded=0.5, today=0.75),
    _commit("a000003", "2026-11-02", words=4, loc=1, tokens=10),
    _commit("a000004", "2026-11-03", words=1, loc=1, tokens=7, recorded=0.1, today=0.1, unpriced=True),
]


class TestGroupByDay(unittest.TestCase):
    def test_sums_each_day_in_commit_order(self):
        days = group_by_day(LEDGER)
        self.assertEqual([d["date"] for d in days], ["2026-11-01", "2026-11-02", "2026-11-03"])
        first = days[0]
        self.assertEqual((first["commits"], first["words_delta"], first["loc_delta"], first["tokens"]), (2, 8, 25, 150))
        self.assertAlmostEqual(first["cost_recorded"], 1.5)
        self.assertAlmostEqual(first["cost_at_today"], 2.25)
        self.assertFalse(first["partial"])

    def test_a_day_with_nothing_priced_has_no_cost_rather_than_zero(self):
        self.assertIsNone(group_by_day(LEDGER)[1]["cost_recorded"])
        self.assertIsNone(group_by_day(LEDGER)[1]["cost_at_today"])

    def test_a_day_with_unpriced_usage_is_partial(self):
        self.assertTrue(group_by_day(LEDGER)[2]["partial"])

    def test_today_is_partial_on_its_own_unpriced_not_on_the_recorded_one(self):
        # A model with no price on the commit's date but priced today: the
        # recorded cost is partial, today's is not.
        m = _commit("b000001", "2026-10-30", tokens=5, recorded=None, today=0.2, unpriced=True)
        m["unpriced_at_today"] = False
        day = group_by_day([m])[0]
        self.assertTrue(day["partial"])
        self.assertFalse(day["partial_at_today"])
        m["unpriced_at_today"] = True
        self.assertTrue(group_by_day([m])[0]["partial_at_today"])


class TestGroupByNamedMilestone(unittest.TestCase):
    def test_each_milestone_covers_the_commits_after_the_previous_one_up_to_its_own(self):
        groups = group_by_named_milestone(
            LEDGER, [{"name": "First", "until": "a000002"}, {"name": "Second", "until": "a000003"}]
        )
        self.assertEqual([g["name"] for g in groups], ["First", "Second", "In progress"])
        self.assertEqual([(g["from"], g["until"], g["commits"]) for g in groups],
                         [("a000001", "a000002", 2), ("a000003", "a000003", 1), ("a000004", "a000004", 1)])
        self.assertEqual((groups[0]["first_date"], groups[0]["last_date"]), ("2026-11-01", "2026-11-01"))
        self.assertEqual(groups[0]["tokens"], 150)

    def test_no_in_progress_group_when_the_last_milestone_ends_at_the_last_commit(self):
        groups = group_by_named_milestone(LEDGER, [{"name": "All", "until": "a000004"}])
        self.assertEqual([g["name"] for g in groups], ["All"])
        self.assertTrue(groups[0]["partial"])

    def test_an_unknown_commit_fails_loudly(self):
        with self.assertRaises(ValueError):
            group_by_named_milestone(LEDGER, [{"name": "Typo", "until": "fffffff"}])

    def test_milestones_out_of_commit_order_fail_loudly(self):
        with self.assertRaises(ValueError):
            group_by_named_milestone(
                LEDGER, [{"name": "B", "until": "a000003"}, {"name": "A", "until": "a000001"}]
            )

    def test_a_full_hash_matches_its_short_form(self):
        groups = group_by_named_milestone(LEDGER, [{"name": "Full", "until": "a000002ffffffffffffffffffffffffffffffff"}])
        self.assertEqual(groups[0]["until"], "a000002")

    def test_without_named_milestones_there_are_no_groups(self):
        self.assertEqual(group_by_named_milestone(LEDGER, []), [])


class TestNamedMilestonesConfig(unittest.TestCase):
    def test_absent_means_none(self):
        self.assertEqual(named_milestones({}), [])

    def test_each_entry_needs_a_name_and_an_until(self):
        for bad in ([{"name": "x"}], [{"until": "abc1234"}], [{"name": "", "until": "abc1234"}], {"name": "x"}, ["x"]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                named_milestones({"milestones": bad})

    def test_a_valid_list_is_returned_as_is(self):
        value = [{"name": "S1 part 1", "until": "76b178e"}]
        self.assertEqual(named_milestones({"milestones": value}), value)


class TestCostAtToday(unittest.TestCase):
    CONFIG = {"price_provider": "anthropic", "price_model": "claude-x", "currency": "USD"}
    LEDGER = [{
        "provider": "anthropic", "model": "claude-x", "currency": "USD",
        "entries": [
            {"effective_date": "2026-01-01", "input_price": 1.0, "output_price": 0, "cache_read_price": 0,
             "cache_creation_price": 0, "sources": [{"name": "s", "url": "u", "checked_at": "2026-01-01"}]},
            {"effective_date": "2026-06-01", "input_price": 3.0, "output_price": 0, "cache_read_price": 0,
             "cache_creation_price": 0, "sources": [{"name": "s", "url": "u", "checked_at": "2026-06-01"}]},
        ],
    }]

    def test_prices_at_the_run_date_and_leaves_the_recorded_cost_frozen(self):
        from engine.generate_metrics import price_at_today
        m = _commit("a000001", "2026-02-01", tokens=1_000_000, recorded=1.0)
        m["tokens"]["by_model"] = {"claude-x": {"input": 1_000_000, "output": 0, "cache_read": 0,
                                                "cache_creation": 0, "cache_creation_1h": 0}}
        del m["cost_at_today"]
        before = dict(m)
        costs, unpriced = price_at_today([m], self.CONFIG, self.LEDGER, "2026-11-01")
        self.assertAlmostEqual(costs["a000001"]["amount"], 3.0)
        self.assertEqual(unpriced, [])
        # The frozen record is never touched: --enrich promises a refused
        # milestone stays byte for byte what it was.
        self.assertEqual(m, before)

    def test_a_commit_without_tokens_has_no_cost_at_today(self):
        from engine.generate_metrics import price_at_today
        m = _commit("a000001", "2026-02-01")
        self.assertIsNone(price_at_today([m], self.CONFIG, self.LEDGER, "2026-11-01")[0]["a000001"])

    def test_a_model_no_series_covers_today_is_listed_as_unpriced_at_today(self):
        from engine.generate_metrics import price_at_today
        m = _commit("a000001", "2026-02-01", tokens=10)
        m["tokens"]["by_model"] = {"claude-unknown": {"input": 10, "output": 0, "cache_read": 0,
                                                      "cache_creation": 0, "cache_creation_1h": 0}}
        costs, unpriced = price_at_today([m], self.CONFIG, self.LEDGER, "2026-11-01")
        self.assertIsNone(costs["a000001"])
        self.assertEqual(unpriced, ["a000001"])


class TestRollupRendering(unittest.TestCase):
    def test_day_rows_show_totals_and_mark_partial(self):
        from engine.generate_metrics import render_day_rows
        html = render_day_rows(group_by_day(LEDGER))
        self.assertIn("<td>2026-11-01</td><td>2</td><td>8</td><td>25</td><td>150</td><td>$1.5000</td><td>$2.2500</td>", html)
        self.assertIn("$0.1000 (partial)", html)
        # Tokens nothing priced say so, rather than a "-" that reads as no usage.
        self.assertIn("<td>2026-11-02</td><td>1</td><td>4</td><td>1</td><td>10</td><td>unpriced</td><td>unpriced</td>", html)

    def test_named_rows_escape_the_name_and_show_the_range(self):
        from engine.generate_metrics import render_named_rows
        groups = group_by_named_milestone(LEDGER, [{"name": "S1 <parte 1>", "until": "a000002"}])
        html = render_named_rows(groups)
        self.assertIn("S1 &lt;parte 1&gt;", html)
        self.assertIn("a000001..a000002", html)
        self.assertIn("2026-11-01", html)


if __name__ == "__main__":
    unittest.main()
