"""Minimal, dependency-free ``.env`` loader.

Understands ``KEY=VALUE`` lines with optional ``export`` and surrounding quotes, blank
lines and ``#`` comments. Variables already present in the process environment always
win, so a file can never silently override real shell or CI configuration.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_FILE_VAR = "ORANGE_ENV_FILE"
DEFAULT_PATH = ".env"


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def load_env(path: str | Path | None = None, *, override: bool = False) -> dict[str, str]:
    """Load ``.env`` (or ``ORANGE_ENV_FILE``) into ``os.environ``.

    Returns only the variables actually applied. A missing or unreadable file is not
    an error: env configuration is optional by design.
    """
    env_path = Path(path or os.environ.get(ENV_FILE_VAR) or DEFAULT_PATH)
    if not env_path.is_file():
        return {}
    try:
        text = env_path.read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    applied: dict[str, str] = {}
    for key, value in parse_env(text).items():
        if override or key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied
