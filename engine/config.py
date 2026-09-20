"""Loads and validates this project's config.json.

`price_mode` is the one optional key, and it is explicit opt-in, never
inferred from the price provider:

  per_model  (default, and what a config without the key gets) each
             model id in a transcript is priced with its own series
             under price_provider; a model with no series is reported
             unpriced.
  flat       every token of a milestone is priced with the one series
             (price_provider / price_model), whatever model wrote it.
             For a project that has its own single cost basis, e.g. the
             shipped `custom` / `on-premise` series.
"""

import json
import os

PRICE_MODES = ("per_model", "flat")
DEFAULT_PRICE_MODE = "per_model"

REQUIRED_KEYS = (
    "milestone_folder",
    "content_globs",
    "code_globs",
    "exclude_globs",
    "transcript_reader",
    "price_provider",
    "price_model",
    "currency",
)


def load_config(path):
    if not os.path.isfile(path):
        raise FileNotFoundError("no config file at %s" % path)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    for key in REQUIRED_KEYS:
        if key not in data:
            raise ValueError("config is missing required key: %s" % key)
    price_mode(data)
    return data


def price_mode(config):
    """The config's price_mode, DEFAULT_PRICE_MODE when the key is
    absent. Raises ValueError for any value that is not one of
    PRICE_MODES, so a typo such as "flat " or "perModel" fails loudly
    instead of silently pricing the way the author did not intend."""
    mode = config.get("price_mode", DEFAULT_PRICE_MODE)
    if mode not in PRICE_MODES:
        raise ValueError(
            "config price_mode must be one of %s, got %r"
            % (", ".join('"%s"' % m for m in PRICE_MODES), mode)
        )
    return mode
