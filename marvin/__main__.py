"""python -m marvin — CLI entry point for Marvin setup and diagnostics."""

import sys

if len(sys.argv) > 1 and sys.argv[1] == "setup":
    from marvin.setup import setup
    setup()
else:
    print("Usage:")
    print("  python -m marvin setup    — install cron script and show setup instructions")
