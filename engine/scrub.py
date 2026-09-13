"""Strips local machine detail (paths, username, hostname) out of any
string before it is allowed to reach a file this project publishes.
Every free-text field written to data.json passes through here first,
formalising a bug this same engine's ancestor once had and fixed: a
local absolute path leaking into a public, versioned artefact."""

import getpass
import re
import socket
from pathlib import Path


def scrub_text(text, home_dir=None, username=None, hostname=None):
    if text is None:
        return None
    home_dir = home_dir if home_dir is not None else str(Path.home())
    username = username if username is not None else getpass.getuser()
    hostname = hostname if hostname is not None else socket.gethostname()

    result = text
    if home_dir:
        for variant in {home_dir, home_dir.replace("\\", "/"), home_dir.replace("/", "\\")}:
            result = result.replace(variant, "~")
    if hostname:
        result = re.sub(re.escape(hostname), "[host]", result, flags=re.IGNORECASE)
    if username:
        result = re.sub(re.escape(username), "[user]", result, flags=re.IGNORECASE)
    return result
