"""``python -m orange`` — thin entry point into the CLI handler."""

from __future__ import annotations

import sys

from .handlers.cli import main

if __name__ == "__main__":
    sys.exit(main())
