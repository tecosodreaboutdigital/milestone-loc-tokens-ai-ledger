import unittest

from engine.cost import compute_cost


class TestComputeCost(unittest.TestCase):
    def test_none_price_entry_returns_none(self):
        self.assertIsNone(compute_cost({"input": 1, "output": 1, "cache_read": 0, "cache_creation": 0}, None))

    def test_computes_cost_per_million_tokens(self):
        price_entry = {
            "input_price": 3.0, "output_price": 15.0,
            "cache_read_price": 0.3, "cache_creation_price": 3.75,
        }
        tokens = {"input": 1_000_000, "output": 1_000_000, "cache_read": 0, "cache_creation": 0}
        self.assertEqual(compute_cost(tokens, price_entry), 18.0)

    def test_rounds_to_four_decimal_places(self):
        price_entry = {
            "input_price": 3.0, "output_price": 15.0,
            "cache_read_price": 0.3, "cache_creation_price": 3.75,
        }
        tokens = {"input": 1234, "output": 0, "cache_read": 0, "cache_creation": 0}
        self.assertEqual(compute_cost(tokens, price_entry), 0.0037)


if __name__ == "__main__":
    unittest.main()
