"""Loads and validates this project's config.json.

`price_mode` and `include_nested_sessions` are the optional keys that
change what is counted and priced, both explicit opt-in; `milestones`
and `day_timezone` only change how the dashboard groups it. `include_nested_sessions` (default false) also counts
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
import re
import zoneinfo
from datetime import timedelta, timezone

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
    named_milestones(data)
    day_timezone(data)
    return data


_OFFSET = re.compile(r"^([+-])(\d{2}):(\d{2})$")


def day_timezone(config):
    """The config's optional `day_timezone`: the zone the "By day" table
    and the per-day charts read each commit's timestamp in, as a tzinfo,
    or None when the key is absent (each commit's own offset, the date
    recorded with it). Accepts "UTC", a fixed offset such as "-03:00", or
    an IANA name such as "America/Sao_Paulo" where the system has a time
    zone database (Windows has none without the tzdata package, so the
    name fails there and an offset is the portable choice; an offset never
    follows daylight saving). Only the grouping uses it: a commit's own
    `date`, the one its recorded cost was priced at, never changes."""
    value = config.get("day_timezone")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("config day_timezone must be \"UTC\", an offset like \"-03:00\", or an IANA name, got %r" % (value,))
    if value == "UTC":
        return timezone.utc
    match = _OFFSET.match(value)
    if match:
        sign, hours, minutes = match.groups()
        offset = timedelta(hours=int(hours), minutes=int(minutes))
        if offset >= timedelta(hours=24):
            raise ValueError("config day_timezone offset out of range: %r" % (value,))
        return timezone(-offset if sign == "-" else offset)
    try:
        return zoneinfo.ZoneInfo(value)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        raise ValueError(
            "config day_timezone %r is not a time zone this system knows; use \"UTC\" or a fixed "
            "offset such as \"-03:00\" (a Windows Python has no IANA names without the tzdata package)"
            % (value,)
        ) from None


def named_milestones(config):
    """The config's optional `milestones`: a list of {"name", "until"},
    in history order, where `until` is the short hash of the last commit
    the milestone covers (see engine.rollup). Absent means none. A
    malformed entry fails loudly instead of being skipped."""
    value = config.get("milestones", [])
    if not isinstance(value, list):
        raise ValueError("config milestones must be a list of {\"name\", \"until\"}, got %r" % (value,))
    for entry in value:
        if not (isinstance(entry, dict)
                and isinstance(entry.get("name"), str) and entry["name"].strip()
                and isinstance(entry.get("until"), str) and entry["until"].strip()):
            raise ValueError("config milestones entry needs a non-empty name and until, got %r" % (entry,))
    return value


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
