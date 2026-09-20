"""The price ledger: dated, multi-source, append-only. A changed price
never overwrites the entry it replaces; it becomes a new entry with a
later effective_date, so a milestone priced in the past keeps the
price that was actually true on the day it was recorded.

An entry may also carry three optional fields:
  cache_creation_1h_price  the 1-hour cache write price (US$/MTok);
                           cache_creation_price stays the 5-minute one
  corrects                 the effective_date of an earlier entry in the
                           same series that recorded a WRONG price (not a
                           changed one); the correcting entry is appended
                           with that same effective_date, and price_at
                           lets the later entry win the tie
  note                     free text: what was wrong, or a caveat
An entry without them stays valid."""

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


def append_entry(ledger_path, provider, model, currency, effective_date, prices, sources,
                 corrects=None, note=None):
    """Appends one dated entry, never edits or removes an existing one.

    `prices` needs the four original keys and may carry
    cache_creation_1h_price. `corrects`, if given, names the
    effective_date of the earlier entry this one replaces because that
    entry was wrong; it must match an entry already in the series and
    equal this entry's own effective_date (a correction that starts on
    another date would leave some milestones on the wrong price)."""
    if len(sources) < 1:
        raise ValueError("at least one source is required to append a price entry")
    if len(sources) < 2:
        print("warning: only one source given, a second independent source is recommended")

    ledger = load_ledger(ledger_path)
    series = find_series(ledger, provider, model)

    if corrects is not None:
        if corrects != effective_date:
            raise ValueError(
                "a correcting entry must carry the same effective_date as the entry it "
                "corrects (%s), got %s" % (corrects, effective_date)
            )
        if series is None or not any(e["effective_date"] == corrects for e in series["entries"]):
            raise ValueError(
                "corrects=%s matches no existing entry in %s / %s" % (corrects, provider, model)
            )
        if not note:
            print("warning: --corrects given without a note saying what was wrong; add one")

    entry = {
        "effective_date": effective_date,
        "input_price": prices["input_price"],
        "output_price": prices["output_price"],
        "cache_read_price": prices["cache_read_price"],
        "cache_creation_price": prices["cache_creation_price"],
    }
    if prices.get("cache_creation_1h_price") is not None:
        entry["cache_creation_1h_price"] = prices["cache_creation_1h_price"]
    if corrects is not None:
        entry["corrects"] = corrects
    if note:
        entry["note"] = note
    entry["sources"] = sources

    if series is None:
        ledger.append({
            "provider": provider, "model": model, "currency": currency,
            "entries": [entry],
        })
    else:
        series["entries"].append(entry)
        # list.sort is stable: entries with an equal effective_date keep
        # their insertion order, so a correcting entry stays after the
        # entry it corrects, which is what lets price_at prefer it.
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
