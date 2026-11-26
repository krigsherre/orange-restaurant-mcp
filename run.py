#!/usr/bin/env python3
"""Zero-install entry point: ./run.py --budget 1000 --radius 5"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))

from orange.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
