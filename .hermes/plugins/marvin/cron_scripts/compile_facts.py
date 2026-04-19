#!/usr/bin/env python3
"""
Marvin fact compilation cron script.

Queries observations since last compilation, outputs context for the LLM
to synthesize into facts. The LLM response (JSON) is then processed by
the post_llm_call hook to write facts to the canonical store.

Copy to ~/.hermes/scripts/marvin_compile_facts.py for hermes cron execution.
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
    from marvin.context import build_compilation_context
except ImportError as e:
    print(json.dumps({"error": f"Cannot import marvin package: {e}"}))
    sys.exit(1)


def main():
    store = MarvinStore()

    last_compiled = store.get_meta("last_compilation")
    obs_since = store.get_observations(since=last_compiled, limit=1) if last_compiled else store.get_observations(limit=1)

    if not obs_since:
        print(json.dumps({"status": "no_new_observations"}))
        return

    context_str = build_compilation_context(store)
    context_data = json.loads(context_str)

    # Check if there are document-related observations that should trigger doc analysis
    doc_folder = store.get_profile("documents_folder")
    if doc_folder:
        doc_path = Path(doc_folder)
        if doc_path.exists():
            resume_files = list(doc_path.glob("*resume*")) + list(doc_path.glob("*cv*"))
            if resume_files:
                context_data["documents"] = {
                    "folder": str(doc_folder),
                    "resume_files": [str(f) for f in resume_files[:3]],
                    "hint": "Consider reading and analyzing these documents for resume quality facts.",
                }

    # Check for web search hints based on goals
    goals = store.get_active_goals()
    job_goals = [g for g in goals if g.get("area") == "job_search"]
    if job_goals:
        target = job_goals[0].get("description", "")
        context_data["web_search_hint"] = (
            f"Client is targeting: {target}. "
            "Consider searching for current hiring trends to ground your analysis."
        )

    print(json.dumps(context_data, indent=2))


if __name__ == "__main__":
    main()
