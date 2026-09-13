"""Turns a milestone's token counts plus one price ledger entry into a
single cost amount. Never estimates: if no price entry applies, the
cost is None, not a guess."""


def compute_cost(tokens, price_entry):
    if price_entry is None:
        return None
    amount = (
        tokens.get("input", 0) / 1_000_000 * price_entry["input_price"]
        + tokens.get("output", 0) / 1_000_000 * price_entry["output_price"]
        + tokens.get("cache_read", 0) / 1_000_000 * price_entry["cache_read_price"]
        + tokens.get("cache_creation", 0) / 1_000_000 * price_entry["cache_creation_price"]
    )
    return round(amount, 4)
