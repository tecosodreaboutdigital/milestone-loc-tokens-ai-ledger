"""Turns a milestone's token counts plus the price ledger into a cost.
Never estimates: if no price applies to some tokens, those tokens are
reported as unpriced, with the reason, never guessed at another model's
or another cache TTL's rate.

Token shapes this module reads (every field but the first four is
optional, so a milestone frozen by an older version of the engine still
prices):

    {"input", "output", "cache_read", "cache_creation",   # the original four
     "cache_creation_1h",                                  # subset of cache_creation
     "by_model": {model_id: {the same five fields}}}       # the same tokens, split by model

cache_creation is the total of every cache write; cache_creation_1h is
the part of it written with the 1-hour TTL. The 5-minute part is the
difference, and is priced at cache_creation_price."""

from engine.prices import find_series, price_at

# The four original counters. cache_creation_1h is deliberately not in
# this tuple: it is a subset of cache_creation, so adding it to a total
# would count the same tokens twice.
TOKEN_KINDS = ("input", "output", "cache_read", "cache_creation")
MODEL_TOKEN_FIELDS = TOKEN_KINDS + ("cache_creation_1h",)

_M = 1_000_000


def total_tokens(tokens):
    """Every token once: the four original counters, nothing double
    counted (cache_creation_1h is inside cache_creation, by_model is
    the same tokens again, split)."""
    return sum(tokens.get(kind, 0) for kind in TOKEN_KINDS)


def _one_hour_and_five_minute_writes(tokens):
    cache_creation = tokens.get("cache_creation", 0)
    one_hour = min(tokens.get("cache_creation_1h", 0), cache_creation)
    return one_hour, cache_creation - one_hour


def price_tokens(tokens, price_entry):
    """Prices one set of tokens with one ledger entry. Returns
    (amount, unpriced_one_hour_tokens): the unrounded amount for
    everything that could be priced, and how many 1-hour cache write
    tokens could not be because the entry has no cache_creation_1h_price
    (they are left out of the amount, never priced at the 5-minute
    rate)."""
    one_hour, five_minute = _one_hour_and_five_minute_writes(tokens)
    amount = (
        tokens.get("input", 0) / _M * price_entry["input_price"]
        + tokens.get("output", 0) / _M * price_entry["output_price"]
        + tokens.get("cache_read", 0) / _M * price_entry["cache_read_price"]
        + five_minute / _M * price_entry["cache_creation_price"]
    )
    unpriced_one_hour = 0
    if one_hour:
        one_hour_price = price_entry.get("cache_creation_1h_price")
        if one_hour_price is None:
            unpriced_one_hour = one_hour
        else:
            amount += one_hour / _M * one_hour_price
    return amount, unpriced_one_hour


def compute_cost(tokens, price_entry):
    """One cost amount for one set of tokens under one price entry, or
    None when it cannot be given honestly: no entry, or 1-hour cache
    writes with an entry that has no 1-hour price."""
    if price_entry is None:
        return None
    amount, unpriced_one_hour = price_tokens(tokens, price_entry)
    if unpriced_one_hour:
        return None
    return round(amount, 4)


def _unpriced(model, tokens, reason):
    return {"model": model, "tokens": tokens, "reason": reason}


def _why_no_entry(series, provider, model, date):
    if series is None:
        return (
            "no price series for %s / %s in the ledger; add one, with a source, "
            "using engine/update_prices.py" % (provider, model)
        )
    earliest = min(e["effective_date"] for e in series["entries"])
    return (
        "no %s / %s price entry effective on or before %s (the series starts %s)"
        % (provider, model, date, earliest)
    )


def _why_no_one_hour_price(provider, model, effective_date):
    return (
        "1-hour cache writes, but the %s / %s entry effective %s has no "
        "cache_creation_1h_price; they are not priced at the 5-minute rate"
        % (provider, model, effective_date)
    )


def price_milestone(tokens, ledger, provider, fallback_model, date, currency):
    """Prices one milestone. Returns (cost_recorded, unpriced).

    cost_recorded is the dict the data file stores, or None when nothing
    could be priced. unpriced is a list of {"model", "tokens", "reason"},
    one per portion that could not be priced; it is empty when
    everything was. When some portions are priced and others are not,
    cost_recorded.amount covers only the priced ones and `unpriced`
    says what is missing: it is a floor, never a guess.

    A milestone with by_model prices each model's tokens with that
    model's own series under `provider` (exact model id, entry in effect
    on `date`). A milestone without by_model (frozen by an older engine,
    or a transcript whose rows name no model) keeps the original
    behaviour: all its tokens priced with the one configured series,
    `fallback_model`."""
    if total_tokens(tokens) == 0:
        return None, []

    by_model = tokens.get("by_model") or {}
    if not by_model:
        series = find_series(ledger, provider, fallback_model)
        entry = price_at(series, date)
        if entry is None:
            return None, [_unpriced(
                fallback_model, total_tokens(tokens), _why_no_entry(series, provider, fallback_model, date),
            )]
        amount, unpriced_one_hour = price_tokens(tokens, entry)
        unpriced = []
        if unpriced_one_hour:
            unpriced.append(_unpriced(
                fallback_model, unpriced_one_hour,
                _why_no_one_hour_price(provider, fallback_model, entry["effective_date"]),
            ))
        return {
            "amount": round(amount, 4), "currency": currency,
            "priced_at": entry["effective_date"], "provider": provider, "model": fallback_model,
        }, unpriced

    total_amount = 0.0
    priced = {}
    unpriced = []
    for model in sorted(by_model):
        model_tokens = by_model[model]
        if total_tokens(model_tokens) == 0:
            continue
        series = find_series(ledger, provider, model)
        entry = price_at(series, date)
        if entry is None:
            unpriced.append(_unpriced(model, total_tokens(model_tokens), _why_no_entry(series, provider, model, date)))
            continue
        amount, unpriced_one_hour = price_tokens(model_tokens, entry)
        if unpriced_one_hour:
            unpriced.append(_unpriced(
                model, unpriced_one_hour, _why_no_one_hour_price(provider, model, entry["effective_date"]),
            ))
        total_amount += amount
        priced[model] = {"amount": round(amount, 4), "priced_at": entry["effective_date"]}

    # Tokens the milestone counted but no by_model entry accounts for
    # (transcript rows that named no model): they cannot be priced
    # without guessing which model served them.
    leftover = sum(
        max(tokens.get(kind, 0) - sum(t.get(kind, 0) for t in by_model.values()), 0)
        for kind in TOKEN_KINDS
    )
    if leftover:
        unpriced.append(_unpriced(
            None, leftover,
            "usage rows that name no model id; they cannot be priced without guessing the model",
        ))

    if not priced:
        return None, unpriced
    return {
        "amount": round(total_amount, 4), "currency": currency,
        "priced_at": max(p["priced_at"] for p in priced.values()),
        "provider": provider,
        "model": next(iter(priced)) if len(priced) == 1 else "multiple",
        "by_model": priced,
    }, unpriced
