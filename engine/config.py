"""Loads and validates this project's config.json.

`price_mode` and `include_nested_sessions` are the optional keys, both
explicit opt-in. `include_nested_sessions` (default false) also counts
the sessions opened in worktrees and subfolders of the repository; see
include_nested_sessions below. `price_mode` is never inferred from the
price provider:

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
    include_nested_sessions(data)
    return data


def include_nested_sessions(config):
    """The config's include_nested_sessions, False when the key is absent.

    Opt-in, never inferred: True also reads the sessions Claude Code
    filed under worktrees and subfolders of this repository (see
    readers.claude_code.find_nested_project_session_jsonl), counting only
    the messages whose cwd is inside the repository. Anything but a JSON
    true or false fails loudly, so "yes" or 1 is never read as a choice
    the author did not make."""
    value = config.get("include_nested_sessions", False)
    if not isinstance(value, bool):
        raise ValueError("config include_nested_sessions must be true or false, got %r" % (value,))
    return value


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
