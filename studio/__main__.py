"""Entry point: `python -m studio [GoPro.MP4 ...]` (also `pixi run studio`)."""

import sys

from .app import main

if __name__ == "__main__":
    sys.exit(main())
