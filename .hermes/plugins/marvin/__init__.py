"""
Marvin plugin for hermes-agent.

Bridges the marvin coaching package into the hermes runtime via:
- Lifecycle hooks (observation collection, context injection, outcome recording)
- Slash commands (/marvin)
- Fact compilation processing (parses LLM JSON responses from compilation cron)
"""

import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("marvin.plugin")

_repo_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from marvin.store import MarvinStore
from marvin.context import build_session_context
from marvin.sensors.registry import SensorRegistry

_store: MarvinStore = None
_sensors: SensorRegistry = None


def _get_store() -> MarvinStore:
    global _store
    if _store is None:
        _store = MarvinStore()
    return _store


def _get_sensors() -> SensorRegistry:
    global _sensors
    if _sensors is None:
        _sensors = SensorRegistry()
    return _sensors


def _days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _detect_action_type(response: str) -> str:
    """Detect the intervention type from the LLM response content."""
    lower = response.lower()
    if any(phrase in lower for phrase in (
        "talking to a person", "therapist", "counselor",
        "professional support", "might help more than i can",
    )):
        return "refer"
    if any(phrase in lower for phrase in (
        "things have been quiet", "no pressure", "that's okay",
        "i'm here when", "take your time", "no rush at all",
    )):
        return "support"
    if any(phrase in lower for phrase in (
        "checking in", "how are things", "how's it going",
        "just wanted to", "haven't heard",
    )):
        return "check_in"
    if any(phrase in lower for phrase in (
        "nice", "great", "solid", "good work", "well done",
        "keep it up", "momentum", "rhythm", "progress",
    )):
        return "encourage"
    if any(phrase in lower for phrase in (
        "want to", "how about", "try", "consider",
        "one application", "send one",
    )):
        return "nudge"
    return "check_in"


def register(ctx):
    logger.info("Marvin plugin registering")

    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("post_llm_call", _on_post_llm_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("on_session_start", _on_session_start)

    ctx.register_command("marvin", _handle_marvin_command, description="Marvin life coach commands")

    logger.info("Marvin plugin registered")


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------

def _is_cron(platform: str = "", session_id: str = "") -> bool:
    """Detect whether this invocation is a cron job, not a real user."""
    return platform == "cron" or session_id.startswith("cron_")


def _on_session_start(session_id: str = "", **kwargs):
    platform = kwargs.get("platform", "unknown")

    # Cron runs get fresh session IDs — they are NOT user re-engagement.
    if _is_cron(platform, session_id):
        return

    store = _get_store()
    store.add_observation(
        source="system",
        category="session",
        content={"event": "session_start", "platform": platform},
        session_id=session_id,
    )

    # Mark recent unresponded outcomes as "engaged" — only for real user sessions.
    unresponded = store.get_unresponded_outcomes(limit=5)
    for o in unresponded:
        delivered = o.get("delivered_at", "")
        if delivered >= _days_ago(3):
            store.update_outcome_response(o["id"], "engaged", "user started a new session")


def _on_post_tool_call(
    tool_name: str = "",
    args: dict = None,
    result: str = "",
    **kwargs,
):
    """Route MCP tool results through sensor adapters to extract observations."""
    store = _get_store()
    sensors = _get_sensors()
    sensors.process_tool_call(store, tool_name, args or {}, result or "")


def _on_pre_llm_call(session_id: str = "", user_message: str = "", **kwargs):
    platform = kwargs.get("platform", "")

    # Cron jobs already have their prompt + script context. Don't inject session context.
    if _is_cron(platform, session_id):
        return None

    store = _get_store()

    has_goals = len(store.get_active_goals()) > 0
    has_facts = len(store.get_current_facts()) > 0
    if not has_goals and not has_facts:
        return None

    active_session = store.get_meta("active_session_mode")
    if active_session:
        from marvin.prompts import (
            SESSION_INTERVIEW_PROMPT,
            SESSION_RESUME_PROMPT,
            SESSION_PLANNING_PROMPT,
        )
        session_prompts = {
            "interview": SESSION_INTERVIEW_PROMPT,
            "resume": SESSION_RESUME_PROMPT,
            "planning": SESSION_PLANNING_PROMPT,
        }
        session_prompt = session_prompts.get(active_session, "")
        if session_prompt:
            context_json = build_session_context(store)
            context_block = (
                f"[Marvin Session Mode: {active_session}]\n\n"
                f"{session_prompt}\n\n"
                f"## Coaching Context\n{context_json}"
            )
            return {"context": context_block}

    context_json = build_session_context(store)
    context_block = (
        "[Marvin Coaching Context — this is background context about the user's "
        "life coaching goals and behavioral patterns. Use it to inform your responses "
        "when relevant, but don't mention Marvin or this context block explicitly "
        "unless the user asks about their coaching.]\n\n"
        f"{context_json}"
    )
    return {"context": context_block}


def _on_post_llm_call(
    session_id: str = "",
    user_message: str = "",
    assistant_response: str = "",
    **kwargs,
):
    store = _get_store()
    platform = kwargs.get("platform", "cli")
    is_cron = _is_cron(platform, session_id)

    # Only record real user messages as observations — NOT cron prompts.
    if user_message and not is_cron:
        store.add_observation(
            source="user_message",
            category="interaction",
            content={"message_preview": user_message[:300], "platform": platform},
            session_id=session_id,
        )

    if not assistant_response:
        return

    # Process fact compilation responses (from the compilation cron job)
    if is_cron:
        _try_process_compilation(store, assistant_response)

    # Record cron-delivered coaching messages as outcomes
    if is_cron and "[SILENT]" not in assistant_response:
        action_type = _detect_action_type(assistant_response)
        store.add_outcome(
            action_type=action_type,
            content=assistant_response[:500],
            channel="cron",
        )


def _try_process_compilation(store: MarvinStore, response: str):
    """Attempt to parse and process a fact compilation response."""
    # Extract JSON from response (may be wrapped in markdown fences)
    json_match = re.search(r'\{[\s\S]*\}', response)
    if not json_match:
        return

    try:
        data = json.loads(json_match.group())
    except json.JSONDecodeError:
        return

    # Must have at least one compilation-specific key
    if not any(k in data for k in ("new_facts", "supersede", "invalidate")):
        return

    processed = False

    for fact in data.get("new_facts", []):
        claim = fact.get("claim", "").strip()
        confidence = float(fact.get("confidence", 0.5))
        if claim:
            store.add_fact(claim, confidence)
            processed = True

    for entry in data.get("supersede", []):
        old_id = entry.get("old_fact_id")
        new_claim = entry.get("new_claim", "").strip()
        confidence = float(entry.get("confidence", 0.5))
        if old_id and new_claim:
            store.supersede_fact(old_id, new_claim, confidence)
            processed = True

    for fact_id in data.get("invalidate", []):
        if isinstance(fact_id, int):
            # Supersede with an empty marker — effectively invalidates
            store.supersede_fact(fact_id, f"[invalidated: was fact #{fact_id}]", 0.0)
            processed = True

    if processed:
        store.set_meta("last_compilation", datetime.now(timezone.utc).isoformat())
        logger.info("Marvin: processed fact compilation response")


# ---------------------------------------------------------------------------
# Slash command: /marvin
# ---------------------------------------------------------------------------

def _handle_marvin_command(raw_args: str) -> str:
    args = raw_args.strip()
    if not args or args == "help":
        return _help_text()

    parts = args.split(None, 1)
    cmd = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    commands = {
        "applied": _cmd_applied,
        "interview": _cmd_interview,
        "status": _cmd_status,
        "goal": _cmd_goal,
        "setup": _cmd_setup,
        "profile": _cmd_profile,
        "session": _cmd_session,
        "note": _cmd_note,
        "reset": _cmd_reset_session,
    }

    handler = commands.get(cmd)
    if handler:
        return handler(rest)
    return f"Unknown command: {cmd}\n\n{_help_text()}"


def _help_text() -> str:
    return (
        "**Marvin — Life Coach**\n\n"
        "**Logging:**\n"
        "  `/marvin applied <company>` — log a job application\n"
        "  `/marvin interview <company> [notes]` — log an interview\n"
        "  `/marvin note <text>` — log a free-form observation\n"
        "\n**Coaching:**\n"
        "  `/marvin setup` — guided onboarding\n"
        "  `/marvin session <interview|resume|planning>` — start a coaching session\n"
        "  `/marvin session end` — end the current session\n"
        "\n**Info:**\n"
        "  `/marvin status` — current coaching state\n"
        "  `/marvin profile` — your profile and preferences\n"
        "  `/marvin goal <description>` — set a new goal\n"
    )


def _cmd_applied(rest: str) -> str:
    if not rest:
        return "Usage: `/marvin applied <company name>`"
    store = _get_store()
    store.add_observation(
        source="manual_log",
        category="application",
        content={"company": rest},
    )
    count_7d = store.count_observations("application", _days_ago(7))
    return f"Logged application to **{rest}**. ({count_7d} this week)"


def _cmd_interview(rest: str) -> str:
    if not rest:
        return "Usage: `/marvin interview <company> [notes]`"
    parts = rest.split(None, 1)
    company = parts[0]
    notes = parts[1] if len(parts) > 1 else None
    store = _get_store()
    store.add_observation(
        source="manual_log",
        category="interview",
        content={"company": company, "notes": notes},
    )
    return f"Logged interview with **{company}**."


def _cmd_note(rest: str) -> str:
    if not rest:
        return "Usage: `/marvin note <free-form observation>`"
    store = _get_store()
    store.add_observation(
        source="manual_log",
        category="note",
        content={"text": rest},
    )
    return "Noted."


def _cmd_status(rest: str = "") -> str:
    store = _get_store()

    goals = store.get_active_goals()
    facts = store.get_current_facts()
    profile = store.get_all_profile()
    apps_7d = store.count_observations("application", _days_ago(7))
    apps_28d = store.count_observations("application", _days_ago(28))
    interviews_14d = store.count_observations("interview", _days_ago(14))
    recent_outcomes = store.get_recent_outcomes(limit=5)
    active_session = store.get_meta("active_session_mode")

    lines = ["**Marvin Status**\n"]

    if active_session:
        lines.append(f"**Active session:** {active_session}\n")

    if profile:
        name = profile.get("name", "")
        if name:
            lines.append(f"**Client:** {name}")

    if goals:
        lines.append("**Goals:**")
        for g in goals:
            lines.append(f"  - [{g['area']}] {g['description']}")
        lines.append("")

    lines.append(f"**Activity:** {apps_7d} applications (7d), {apps_28d} (28d), {interviews_14d} interviews (14d)")

    if facts:
        lines.append("\n**Current beliefs:**")
        for f in facts[:8]:
            conf = int(f["confidence"] * 100)
            lines.append(f"  - {f['claim']} ({conf}%)")

    if recent_outcomes:
        lines.append("\n**Recent interventions:**")
        for o in recent_outcomes[:3]:
            resp = o.get("user_response") or "pending"
            lines.append(f"  - [{o['action_type']}] {o['content'][:80]}... ({resp})")

    if not goals and not facts:
        lines.append("\nGet started: `/marvin setup`")

    return "\n".join(lines)


def _cmd_goal(rest: str) -> str:
    if not rest:
        return "Usage: `/marvin goal <description>`\nExample: `/marvin goal Land a senior backend role by Q3 2026`"
    store = _get_store()

    # Try to detect area from common keywords
    area = "general"
    rest_lower = rest.lower()
    if any(w in rest_lower for w in ("job", "role", "position", "hire", "interview", "application", "career")):
        area = "job_search"
    elif any(w in rest_lower for w in ("focus", "productivity", "distract", "deep work")):
        area = "focus"
    elif any(w in rest_lower for w in ("fitness", "exercise", "gym", "weight", "health", "sleep")):
        area = "fitness"
    elif any(w in rest_lower for w in ("money", "budget", "saving", "financial", "debt", "invest")):
        area = "finance"

    store.add_goal(area=area, description=rest)
    return f"Goal set [{area}]: **{rest}**"


def _cmd_setup(rest: str) -> str:
    """Guided onboarding — sets profile and initial goals."""
    store = _get_store()

    if not rest:
        existing = store.get_all_profile()
        if existing:
            return (
                "You're already set up. To update your profile:\n"
                "  `/marvin profile set name <your name>`\n"
                "  `/marvin profile set adhd yes`\n"
                "  `/marvin profile set coaching_style direct`\n"
                "\nOr start fresh: `/marvin setup start`"
            )
        return (
            "Welcome to Marvin. Let's get you set up.\n\n"
            "Tell me about yourself in one message. Include:\n"
            "- Your name\n"
            "- What you're working on (job search, focus, fitness, etc.)\n"
            "- Any context that matters (ADHD, timezone, preferences)\n\n"
            "Example: `/marvin setup I'm Alex, looking for a senior backend role. "
            "I have ADHD and work best with short, direct messages. Timezone: EST.`"
        )

    # Parse setup message for profile fields
    text = rest
    if text.lower() == "start":
        # Reset profile for fresh start
        store.set_profile("onboarded", "false")
        return (
            "Starting fresh. Tell me about yourself:\n"
            "  `/marvin setup <your info>`"
        )

    # Extract what we can
    store.set_profile("raw_intro", text)
    store.set_profile("onboarded", "true")

    # ADHD detection
    if "adhd" in text.lower():
        store.set_profile("adhd", "yes")
        store.set_profile("message_style", "short_direct")

    # Name extraction (simple heuristic)
    name_patterns = [
        r"(?:I'm|I am|name is|call me)\s+(\w+)",
        r"^(\w+)[,.]",
    ]
    for pattern in name_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            store.set_profile("name", match.group(1))
            break

    # Timezone detection
    tz_match = re.search(r"(?:timezone|tz)[:\s]+(\w+)", text, re.IGNORECASE)
    if tz_match:
        store.set_profile("timezone", tz_match.group(1))

    store.add_observation(
        source="system",
        category="onboarding",
        content={"intro": text},
    )

    profile = store.get_all_profile()
    name = profile.get("name", "there")
    adhd = profile.get("adhd") == "yes"

    response = f"Got it, {name}. "
    if adhd:
        response += "ADHD noted — I'll keep messages short and one thing at a time. "
    response += (
        "\n\nNow set your main goal:\n"
        "  `/marvin goal <what you want to achieve>`\n\n"
        "Example: `/marvin goal Land a senior backend role by Q3 2026, 5 applications per week`"
    )
    return response


def _cmd_profile(rest: str) -> str:
    store = _get_store()

    if not rest or rest == "show":
        profile = store.get_all_profile()
        if not profile:
            return "No profile set. Run `/marvin setup` to get started."
        lines = ["**Your Profile**\n"]
        for k, v in sorted(profile.items()):
            if k == "raw_intro":
                continue
            lines.append(f"  **{k}:** {v}")
        return "\n".join(lines)

    if rest.startswith("set "):
        parts = rest[4:].split(None, 1)
        if len(parts) < 2:
            return "Usage: `/marvin profile set <key> <value>`"
        key, value = parts[0].lower(), parts[1]
        store.set_profile(key, value)
        return f"Profile updated: **{key}** = {value}"

    return "Usage: `/marvin profile` or `/marvin profile set <key> <value>`"


def _cmd_session(rest: str) -> str:
    store = _get_store()

    if not rest:
        active = store.get_meta("active_session_mode")
        if active:
            return f"Active session: **{active}**. Type `/marvin session end` to finish."
        return (
            "Start a coaching session:\n"
            "  `/marvin session interview` — mock interview practice\n"
            "  `/marvin session resume` — resume review walkthrough\n"
            "  `/marvin session planning` — weekly planning\n"
        )

    mode = rest.lower().strip()

    if mode == "end":
        active = store.get_meta("active_session_mode")
        if not active:
            return "No active session."
        store.set_meta("active_session_mode", "")
        store.add_observation(
            source="system",
            category="session_end",
            content={"mode": active},
        )
        return f"Session ended: **{active}**. Your progress has been recorded."

    valid_modes = {"interview", "resume", "planning"}
    if mode not in valid_modes:
        return f"Unknown session type. Choose: {', '.join(sorted(valid_modes))}"

    store.set_meta("active_session_mode", mode)
    store.add_observation(
        source="system",
        category="session_start",
        content={"mode": mode},
    )

    starters = {
        "interview": "Interview practice mode activated. I'll ask you questions and give feedback. Ready? Let's start with a common opener.",
        "resume": "Resume review mode activated. I'll walk through feedback on your resume section by section. Let's start with the biggest impact change.",
        "planning": "Planning mode activated. Let's review your week and set goals. How did things go since our last check-in?",
    }
    return starters[mode]


def _cmd_reset_session(rest: str) -> str:
    store = _get_store()
    store.set_meta("active_session_mode", "")
    return "Session mode cleared."
