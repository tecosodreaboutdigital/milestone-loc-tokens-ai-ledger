"""The price ledger: dated, multi-source, append-only. A changed price
never overwrites the entry it replaces; it becomes a new entry with a
later effective_date, so a milestone priced in the past keeps the
price that was actually true on the day it was recorded."""

import json
import os
import tempfile


def load_ledger(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def find_series(ledger, provider, model):
    for series in ledger:
        if series["provider"] == provider and series["model"] == model:
            return series
    return None


def price_at(series, target_date):
    if series is None:
        return None
    eligible = [e for e in series["entries"] if e["effective_date"] <= target_date]
    if not eligible:
        return None
    best = eligible[0]
    for entry in eligible[1:]:
        if entry["effective_date"] >= best["effective_date"]:
            best = entry
    return best


def append_entry(ledger_path, provider, model, currency, effective_date, prices, sources):
    if len(sources) < 1:
        raise ValueError("at least one source is required to append a price entry")
    if len(sources) < 2:
        print("warning: only one source given, a second independent source is recommended")

    ledger = load_ledger(ledger_path)
    series = find_series(ledger, provider, model)
    entry = {
        "effective_date": effective_date,
        "input_price": prices["input_price"],
        "output_price": prices["output_price"],
        "cache_read_price": prices["cache_read_price"],
        "cache_creation_price": prices["cache_creation_price"],
        "sources": sources,
    }
    if series is None:
        ledger.append({
            "provider": provider, "model": model, "currency": currency,
            "entries": [entry],
        })
    else:
        series["entries"].append(entry)
        series["entries"].sort(key=lambda e: e["effective_date"])

    ledger_dir = os.path.dirname(ledger_path) or "."
    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(ledger_path) + ".", suffix=".tmp", dir=ledger_dir
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_path, ledger_path)
    except BaseException:
        os.remove(tmp_path)
        raise
