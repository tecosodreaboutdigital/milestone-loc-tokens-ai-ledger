"""CLI wrapper around engine.prices.append_entry. Run by the
maintainer, on the maintainer's own machine, never by the published
page. Requires at least one source and recommends two. Never edits an
entry: a wrong price is fixed by appending an entry with --corrects."""

import argparse
import os
import sys

# Allow this file to run as a direct script (python engine/update_prices.py)
# from any working directory, not only as a module (python -m
# engine.update_prices) or via python -m unittest. Direct script
# execution puts only this file's own directory on sys.path, so the
# repository root, and therefore the engine package itself, would
# otherwise not be importable.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from engine.prices import append_entry


def update(ledger_path, provider, model, currency, effective_date,
           input_price, output_price, cache_read_price, cache_creation_price, sources,
           cache_creation_1h_price=None, corrects=None, note=None):
    prices = {
        "input_price": input_price, "output_price": output_price,
        "cache_read_price": cache_read_price, "cache_creation_price": cache_creation_price,
    }
    if cache_creation_1h_price is not None:
        prices["cache_creation_1h_price"] = cache_creation_1h_price
    append_entry(
        ledger_path, provider, model, currency, effective_date,
        prices, sources, corrects=corrects, note=note,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Append a new dated price entry to prices.json.")
    parser.add_argument("--ledger", default="engine/prices.json")
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--effective-date", required=True)
    parser.add_argument("--input-price", type=float, required=True)
    parser.add_argument("--output-price", type=float, required=True)
    parser.add_argument("--cache-read-price", type=float, required=True)
    parser.add_argument(
        "--cache-creation-price", type=float, required=True,
        help="Price of a 5-minute cache write, US$ per million tokens.",
    )
    parser.add_argument(
        "--cache-creation-1h-price", type=float, default=None,
        help=(
            "Optional: price of a 1-hour cache write, US$ per million tokens. Without it, "
            "1-hour cache writes under this entry are reported unpriced, never priced at "
            "the 5-minute rate."
        ),
    )
    parser.add_argument(
        "--corrects", default=None, metavar="EFFECTIVE_DATE",
        help=(
            "Marks this entry as the correction of an earlier entry in the same series that "
            "recorded a WRONG price (not a changed one). Give that entry's effective_date; "
            "it must equal --effective-date. Follow with `generate_metrics.py --reprice`."
        ),
    )
    parser.add_argument(
        "--note", default=None,
        help="Optional free text: what was wrong (for --corrects), or a caveat about the sources.",
    )
    parser.add_argument("--source-name", action="append", required=True)
    parser.add_argument("--source-url", action="append", required=True)
    parser.add_argument("--checked-at", required=True)
    args = parser.parse_args()

    if len(args.source_name) != len(args.source_url):
        raise SystemExit("--source-name and --source-url must be given the same number of times")

    sources = [
        {"name": name, "url": url, "checked_at": args.checked_at}
        for name, url in zip(args.source_name, args.source_url)
    ]
    update(
        args.ledger, args.provider, args.model, args.currency, args.effective_date,
        args.input_price, args.output_price, args.cache_read_price, args.cache_creation_price,
        sources,
        cache_creation_1h_price=args.cache_creation_1h_price, corrects=args.corrects, note=args.note,
    )
