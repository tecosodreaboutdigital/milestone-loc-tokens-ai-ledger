import unittest

from engine.cost import compute_cost, price_milestone, total_tokens


def entry(date, input_price, output_price, cache_read_price, cache_creation_price, one_hour=None):
    result = {
        "effective_date": date, "input_price": input_price, "output_price": output_price,
        "cache_read_price": cache_read_price, "cache_creation_price": cache_creation_price,
        "sources": [{"name": "s", "url": "u", "checked_at": "2026-09-19"}],
    }
    if one_hour is not None:
        result["cache_creation_1h_price"] = one_hour
    return result


SONNET = {"provider": "anthropic", "model": "claude-sonnet-5", "currency": "USD",
          "entries": [entry("2026-09-13", 2.0, 10.0, 0.2, 2.5, one_hour=4.0)]}
OPUS = {"provider": "anthropic", "model": "claude-opus-5", "currency": "USD",
        "entries": [entry("2026-09-13", 5.0, 25.0, 0.5, 6.25, one_hour=10.0)]}
NO_1H = {"provider": "anthropic", "model": "claude-old", "currency": "USD",
         "entries": [entry("2026-09-13", 3.0, 15.0, 0.3, 3.75)]}
LEDGER = [SONNET, OPUS, NO_1H]


def tokens(input_=0, output=0, cache_read=0, cache_creation=0, cache_creation_1h=0, by_model=None):
    result = {"input": input_, "output": output, "cache_read": cache_read,
              "cache_creation": cache_creation, "cache_creation_1h": cache_creation_1h}
    if by_model is not None:
        result["by_model"] = by_model
    return result


class TestComputeCostWithOneHourWrites(unittest.TestCase):
    def test_splits_cache_writes_into_five_minute_and_one_hour(self):
        t = tokens(cache_creation=1_000_000, cache_creation_1h=400_000)
        # 600k at 2.50 + 400k at 4.00
        self.assertEqual(compute_cost(t, SONNET["entries"][0]), 3.1)

    def test_one_hour_writes_without_a_one_hour_price_are_never_priced_at_the_five_minute_rate(self):
        t = tokens(cache_creation=1_000_000, cache_creation_1h=1)
        self.assertIsNone(compute_cost(t, NO_1H["entries"][0]))

    def test_an_entry_without_a_one_hour_price_still_prices_a_milestone_with_no_one_hour_writes(self):
        t = tokens(input_=1_000_000, cache_creation=1_000_000)
        self.assertEqual(compute_cost(t, NO_1H["entries"][0]), 3.0 + 3.75)

    def test_a_milestone_frozen_before_cache_creation_1h_existed_still_prices(self):
        old_shape = {"input": 1_000_000, "output": 0, "cache_read": 0, "cache_creation": 1_000_000}
        self.assertEqual(compute_cost(old_shape, SONNET["entries"][0]), 2.0 + 2.5)

    def test_total_tokens_counts_every_token_once(self):
        t = tokens(1, 2, 3, 4, cache_creation_1h=4, by_model={"m": tokens(1, 2, 3, 4, 4)})
        self.assertEqual(total_tokens(t), 10)
        self.assertEqual(total_tokens({"input": 1, "output": 1, "cache_read": 1, "cache_creation": 1}), 4)


class TestPriceMilestoneByModel(unittest.TestCase):
    def price(self, t, date="2026-09-13"):
        return price_milestone(t, LEDGER, "anthropic", "claude-sonnet-5", date, "USD")

    def test_each_models_tokens_are_priced_with_that_models_own_series(self):
        t = tokens(
            input_=2_000_000, output=1_000_000,
            by_model={
                "claude-sonnet-5": tokens(input_=1_000_000),
                "claude-opus-5": tokens(input_=1_000_000, output=1_000_000),
            },
        )
        cost, unpriced = self.price(t)
        # sonnet 1M in at 2.00, opus 1M in at 5.00 and 1M out at 25.00
        self.assertEqual(cost["amount"], 2.0 + 5.0 + 25.0)
        self.assertEqual(unpriced, [])
        self.assertEqual(cost["by_model"]["claude-sonnet-5"]["amount"], 2.0)
        self.assertEqual(cost["by_model"]["claude-opus-5"]["amount"], 30.0)
        self.assertEqual(cost["model"], "multiple")
        self.assertEqual(cost["provider"], "anthropic")
        self.assertEqual(cost["priced_at"], "2026-09-13")
        self.assertEqual(cost["currency"], "USD")

    def test_one_hour_writes_are_priced_at_the_models_one_hour_price(self):
        t = tokens(
            cache_creation=1_000_000, cache_creation_1h=1_000_000,
            by_model={"claude-opus-5": tokens(cache_creation=1_000_000, cache_creation_1h=1_000_000)},
        )
        cost, unpriced = self.price(t)
        self.assertEqual(cost["amount"], 10.0)
        self.assertEqual(unpriced, [])
        self.assertEqual(cost["model"], "claude-opus-5")

    def test_a_model_with_no_series_is_unpriced_with_a_reason_never_priced_at_another_models_rate(self):
        t = tokens(
            input_=2_000_000,
            by_model={
                "claude-sonnet-5": tokens(input_=1_000_000),
                "claude-mystery-9": tokens(input_=1_000_000),
            },
        )
        cost, unpriced = self.price(t)
        self.assertEqual(cost["amount"], 2.0, "only the model that has a series is priced")
        self.assertNotIn("claude-mystery-9", cost["by_model"])
        self.assertEqual(len(unpriced), 1)
        self.assertEqual(unpriced[0]["model"], "claude-mystery-9")
        self.assertEqual(unpriced[0]["tokens"], 1_000_000)
        self.assertIn("no price series", unpriced[0]["reason"])
        self.assertIn("claude-mystery-9", unpriced[0]["reason"])
        self.assertIn("update_prices.py", unpriced[0]["reason"])

    def test_the_lookup_is_by_exact_model_id_under_the_configured_provider(self):
        # Same model id, but the configured provider is a different one:
        # anthropic's series must not price it.
        t = tokens(input_=1_000_000, by_model={"claude-sonnet-5": tokens(input_=1_000_000)})
        cost, unpriced = price_milestone(t, LEDGER, "custom", "on-premise", "2026-09-13", "USD")
        self.assertIsNone(cost)
        self.assertEqual(len(unpriced), 1)
        self.assertIn("custom / claude-sonnet-5", unpriced[0]["reason"])

        # A near miss of the id is not a match either.
        t = tokens(input_=1_000_000, by_model={"claude-sonnet-5[1m]": tokens(input_=1_000_000)})
        cost, unpriced = self.price(t)
        self.assertIsNone(cost)
        self.assertEqual(unpriced[0]["model"], "claude-sonnet-5[1m]")

    def test_a_model_whose_series_starts_after_the_milestone_date_is_unpriced_with_that_reason(self):
        t = tokens(input_=1_000_000, by_model={"claude-opus-5": tokens(input_=1_000_000)})
        cost, unpriced = self.price(t, date="2026-09-01")
        self.assertIsNone(cost)
        self.assertIn("2026-09-01", unpriced[0]["reason"])
        self.assertIn("starts 2026-09-13", unpriced[0]["reason"])

    def test_one_hour_writes_under_an_entry_without_a_one_hour_price_are_left_unpriced(self):
        t = tokens(
            input_=1_000_000, cache_creation=1_000_000, cache_creation_1h=400_000,
            by_model={"claude-old": tokens(input_=1_000_000, cache_creation=1_000_000, cache_creation_1h=400_000)},
        )
        cost, unpriced = self.price(t)
        # input 3.00 + 600k 5-minute writes at 3.75; the 400k 1-hour writes are left out
        self.assertEqual(cost["amount"], round(3.0 + 3.75 * 0.6, 4))
        self.assertEqual(len(unpriced), 1)
        self.assertEqual(unpriced[0]["model"], "claude-old")
        self.assertEqual(unpriced[0]["tokens"], 400_000)
        self.assertIn("cache_creation_1h_price", unpriced[0]["reason"])

    def test_tokens_that_name_no_model_beside_a_by_model_split_are_reported_unpriced(self):
        t = tokens(
            input_=1_500_000,
            by_model={"claude-sonnet-5": tokens(input_=1_000_000)},
        )
        cost, unpriced = self.price(t)
        self.assertEqual(cost["amount"], 2.0)
        self.assertEqual(len(unpriced), 1)
        self.assertIsNone(unpriced[0]["model"])
        self.assertEqual(unpriced[0]["tokens"], 500_000)
        self.assertIn("no model id", unpriced[0]["reason"])

    def test_a_model_with_all_zero_tokens_is_ignored(self):
        t = tokens(input_=1_000_000, by_model={
            "claude-sonnet-5": tokens(input_=1_000_000),
            "<synthetic>": tokens(),
        })
        cost, unpriced = self.price(t)
        self.assertEqual(unpriced, [])
        self.assertEqual(list(cost["by_model"]), ["claude-sonnet-5"])

    def test_nothing_priced_means_no_cost_recorded(self):
        t = tokens(input_=1, by_model={"claude-mystery-9": tokens(input_=1)})
        cost, unpriced = self.price(t)
        self.assertIsNone(cost)
        self.assertEqual(len(unpriced), 1)

    def test_a_same_day_price_correction_wins_for_by_model_pricing_too(self):
        wrong = entry("2026-09-13", 3.0, 15.0, 0.3, 3.75)
        right = entry("2026-09-13", 2.0, 10.0, 0.2, 2.5, one_hour=4.0)
        ledger = [{"provider": "anthropic", "model": "claude-sonnet-5", "currency": "USD", "entries": [wrong, right]}]
        t = tokens(input_=1_000_000, by_model={"claude-sonnet-5": tokens(input_=1_000_000)})
        cost, _ = price_milestone(t, ledger, "anthropic", "claude-sonnet-5", "2026-09-13", "USD")
        self.assertEqual(cost["amount"], 2.0)


class TestPriceMilestoneWithoutByModel(unittest.TestCase):
    # A milestone frozen by an older engine, or whose transcript rows
    # name no model, keeps pricing every token with the one configured
    # series, exactly as before.
    def test_prices_all_tokens_with_the_configured_series(self):
        old_shape = {"input": 1_000_000, "output": 1_000_000, "cache_read": 0, "cache_creation": 0}
        cost, unpriced = price_milestone(old_shape, LEDGER, "anthropic", "claude-sonnet-5", "2026-09-13", "USD")
        self.assertEqual(cost, {
            "amount": 12.0, "currency": "USD", "priced_at": "2026-09-13",
            "provider": "anthropic", "model": "claude-sonnet-5",
        })
        self.assertEqual(unpriced, [])

    def test_an_empty_by_model_is_the_same_as_none(self):
        t = tokens(input_=1_000_000, by_model={})
        cost, _ = price_milestone(t, LEDGER, "anthropic", "claude-sonnet-5", "2026-09-13", "USD")
        self.assertEqual(cost["amount"], 2.0)

    def test_no_usage_is_no_cost_and_nothing_unpriced(self):
        self.assertEqual(
            price_milestone(tokens(), LEDGER, "anthropic", "claude-sonnet-5", "2026-09-13", "USD"),
            (None, []),
        )

    def test_no_price_entry_for_the_date_is_unpriced_with_a_reason(self):
        cost, unpriced = price_milestone(
            {"input": 10, "output": 0, "cache_read": 0, "cache_creation": 0},
            LEDGER, "anthropic", "claude-sonnet-5", "2026-01-01", "USD",
        )
        self.assertIsNone(cost)
        self.assertEqual(unpriced[0]["model"], "claude-sonnet-5")
        self.assertEqual(unpriced[0]["tokens"], 10)
        self.assertIn("2026-01-01", unpriced[0]["reason"])


if __name__ == "__main__":
    unittest.main()
