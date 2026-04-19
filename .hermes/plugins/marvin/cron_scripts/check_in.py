#!/usr/bin/env python3
"""
Marvin check-in cron script.

This script is intended to be COPIED to ~/.hermes/scripts/marvin_check_in.py
so hermes cron can execute it. It queries the Marvin canonical store and
outputs a structured context blob for the LLM to use in coaching decisions.

Usage as cron job:
    hermes cron create --prompt "..." --script marvin_check_in.py --schedule "0 9 * * *"
"""

import json
import sys
from pathlib import Path

# Find the marvin package — it lives at the hermes-agent repo root.
# When run by hermes cron, cwd is this script's parent dir (per scheduler.py:538).
# Walk up to find marvin/.
_script_dir = Path(__file__).resolve().parent
for candidate in [
    _script_dir.parent.parent.parent.parent,  # from .hermes/plugins/marvin/cron_scripts/
    _script_dir.parent,                         # from ~/.hermes/scripts/
    Path.home() / ".hermes" / "scripts",        # fallback
]:
    marvin_init = candidate / "marvin" / "__init__.py"
    if marvin_init.exists():
        if str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
        break

# Also try a MARVIN_REPO_ROOT env var for explicit configuration.
import os
repo_root = os.environ.get("MARVIN_REPO_ROOT")
if repo_root and repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from marvin.store import MarvinStore
    from marvin.context import build_checkin_context
except ImportError as e:
    print(json.dumps({
        "error": f"Cannot import marvin package: {e}",
        "hint": "Set MARVIN_REPO_ROOT env var to the hermes-agent repo root.",
    }))
    sys.exit(1)


def main():
    store = MarvinStore()
    context = build_checkin_context(store)
    print(context)


if __name__ == "__main__":
    main()
