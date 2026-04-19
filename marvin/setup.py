"""
Marvin setup — installs cron scripts and prints instructions.

Run: python -m marvin setup
"""

import os
import shutil
from pathlib import Path

from marvin.store import get_marvin_home
from marvin.prompts import CHECKIN_PROMPT, COMPILATION_PROMPT, TRANSPARENCY_PROMPT


CRON_SCRIPTS = {
    "check_in.py": "marvin_check_in.py",
    "compile_facts.py": "marvin_compile_facts.py",
    "weekly_transparency.py": "marvin_weekly_transparency.py",
}


def _get_hermes_home() -> Path:
    val = os.environ.get("HERMES_HOME", "").strip()
    return Path(val) if val else Path.home() / ".hermes"


def _escape_prompt(prompt: str) -> str:
    """Escape a prompt for safe shell embedding in a single-quoted heredoc."""
    return prompt.replace("'", "'\\''")


def setup():
    hermes_home = _get_hermes_home()
    scripts_dir = hermes_home / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    repo_root = Path(__file__).resolve().parent.parent
    cron_dir = repo_root / ".hermes" / "plugins" / "marvin" / "cron_scripts"

    print("=== Marvin Setup ===\n")

    # 1. Install cron scripts
    print(f"Installing cron scripts to {scripts_dir}/...")
    for src_name, dest_name in CRON_SCRIPTS.items():
        source = cron_dir / src_name
        dest = scripts_dir / dest_name
        if source.exists():
            shutil.copy2(source, dest)
            print(f"  {dest_name}")
        else:
            print(f"  WARNING: {src_name} not found at {source}")

    # 2. Create documents folder
    docs_dir = get_marvin_home() / "documents"
    docs_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nDocuments folder: {docs_dir}")
    print("  Place your resume here for automated analysis.")

    # 3. Write prompt files for reference
    prompts_dir = get_marvin_home() / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "checkin.txt").write_text(CHECKIN_PROMPT)
    (prompts_dir / "compilation.txt").write_text(COMPILATION_PROMPT)
    (prompts_dir / "transparency.txt").write_text(TRANSPARENCY_PROMPT)
    print(f"Prompt files written to {prompts_dir}/")

    # 4. Print cron job setup instructions
    print("\n=== Cron Job Setup ===\n")
    print("Enable project plugins (add to your .env or shell profile):")
    print("  export HERMES_ENABLE_PROJECT_PLUGINS=true")
    print(f"  export MARVIN_REPO_ROOT={repo_root}")
    if os.environ.get("HERMES_HOME"):
        print(f"  export HERMES_HOME={os.environ['HERMES_HOME']}")

    print("\nCreate cron jobs (run these in hermes):\n")

    # Truncated prompts for display, but full prompts written to files
    checkin_short = CHECKIN_PROMPT.split("\n")[0]
    compile_short = COMPILATION_PROMPT.split("\n")[0]
    transparency_short = TRANSPARENCY_PROMPT.split("\n")[0]

    print("1. Daily check-in (morning coaching nudge):")
    print('   hermes cron create \\')
    print('     --name "marvin-checkin" \\')
    print('     --schedule "0 9 * * *" \\')
    print('     --script "marvin_check_in.py" \\')
    print('     --deliver "telegram" \\')
    print(f'     --prompt "$(cat {prompts_dir / "checkin.txt"})"')
    print()

    print("2. Nightly fact compilation (behavioral analysis):")
    print('   hermes cron create \\')
    print('     --name "marvin-compile" \\')
    print('     --schedule "0 2 * * *" \\')
    print('     --script "marvin_compile_facts.py" \\')
    print(f'     --prompt "$(cat {prompts_dir / "compilation.txt"})"')
    print()

    print("3. Weekly transparency ritual (Sunday evening summary):")
    print('   hermes cron create \\')
    print('     --name "marvin-weekly" \\')
    print('     --schedule "0 18 * * 0" \\')
    print('     --script "marvin_weekly_transparency.py" \\')
    print('     --deliver "telegram" \\')
    print(f'     --prompt "$(cat {prompts_dir / "transparency.txt"})"')
    print()

    print("=== Quick Start ===\n")
    print("1. Start hermes with: HERMES_ENABLE_PROJECT_PLUGINS=true hermes")
    print("2. Run: /marvin setup <your intro>")
    print("3. Run: /marvin goal <your goal>")
    print("4. Log activity: /marvin applied <company>")
    print("5. Try a session: /marvin session interview")


if __name__ == "__main__":
    setup()
