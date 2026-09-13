"""CLI wrapper around engine.prices.append_entry. Run by the
maintainer, on the maintainer's own machine, never by the published
page. Requires at least one source and recommends two."""

import argparse

from engine.prices import append_entry


def update(ledger_path, provider, model, currency, effective_date,
           input_price, output_price, cache_read_price, cache_creation_price, sources):
    append_entry(
        ledger_path, provider, model, currency, effective_date,
        {
            "input_price": input_price, "output_price": output_price,
            "cache_read_price": cache_read_price, "cache_creation_price": cache_creation_price,
        },
        sources,
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
    parser.add_argument("--cache-creation-price", type=float, required=True)
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
    )
