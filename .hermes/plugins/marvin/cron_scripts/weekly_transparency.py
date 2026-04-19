#!/usr/bin/env python3
"""
Marvin weekly transparency ritual cron script.

Outputs the week's behavioral summary for the LLM to present to the client.
Scheduled weekly (e.g., Sunday evening).

Copy to ~/.hermes/scripts/marvin_weekly_transparency.py for hermes cron execution.
"""

import json
import sys
from pathlib import Path

_script_dir = Path(__file__).resolve().parent
for candidate in [
    _script_dir.parent.parent.parent.parent,
    _script_dir.parent,
    Path.home() / ".hermes" / "scripts",
]:
    marvin_init = candidate / "marvin" / "__init__.py"
    if marvin_init.exists():
        if str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
        break

import os
repo_root = os.environ.get("MARVIN_REPO_ROOT")
if repo_root and repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from marvin.store import MarvinStore
    from marvin.context import build_transparency_context
except ImportError as e:
    print(json.dumps({"error": f"Cannot import marvin package: {e}"}))
    sys.exit(1)


def main():
    store = MarvinStore()
    context = build_transparency_context(store)
    print(context)


if __name__ == "__main__":
    main()
