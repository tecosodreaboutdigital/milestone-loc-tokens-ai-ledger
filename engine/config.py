"""Loads and validates this project's config.json."""

import json
import os

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
    return data
