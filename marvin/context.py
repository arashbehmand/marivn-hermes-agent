"""
Marvin context builder — assembles behavioral state for LLM prompt injection.

Queries the canonical store and produces a structured JSON blob that gives
the LLM everything it needs to make coaching decisions.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from marvin.store import MarvinStore


def _days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def build_checkin_context(store: MarvinStore) -> str:
    """Build the context blob for a scheduled check-in.

    Returns a JSON string summarizing the user's behavioral state:
    observations, activity metrics, current facts, goals, and outcome history.
    """
    now = datetime.now(timezone.utc)

    apps_7d = store.count_observations("application", _days_ago(7))
    apps_14d = store.count_observations("application", _days_ago(14))
    apps_28d = store.count_observations("application", _days_ago(28))

    recent_obs = store.get_observations(limit=30)
    current_facts = store.get_current_facts()
    active_goals = store.get_active_goals()
    recent_outcomes = store.get_recent_outcomes(limit=10)

    outcomes_7d = [
        o for o in recent_outcomes
        if o.get("delivered_at", "") >= _days_ago(7)
    ]
    engaged = sum(1 for o in outcomes_7d if o.get("user_response") == "engaged")
    total_delivered = len(outcomes_7d)
    response_rate = engaged / total_delivered if total_delivered > 0 else None

    last_engagement = None
    for o in recent_outcomes:
        if o.get("user_response") == "engaged":
            last_engagement = o.get("delivered_at")
            break

    context = {
        "timestamp": now.isoformat(),
        "activity_metrics": {
            "applications_last_7d": apps_7d,
            "applications_last_14d": apps_14d,
            "applications_last_28d": apps_28d,
        },
        "engagement": {
            "response_rate_7d": response_rate,
            "interventions_delivered_7d": total_delivered,
            "last_engagement": last_engagement,
        },
        "recent_observations": [
            {
                "observed_at": o["observed_at"],
                "source": o["source"],
                "category": o["category"],
                "content": o["content"],
            }
            for o in recent_obs[:15]
        ],
        "current_facts": [
            {"claim": f["claim"], "confidence": f["confidence"]}
            for f in current_facts
        ],
        "active_goals": [
            {
                "area": g["area"],
                "description": g["description"],
                "targets": g["targets"],
            }
            for g in active_goals
        ],
        "recent_outcomes": [
            {
                "action_type": o["action_type"],
                "content": o["content"][:200],
                "delivered_at": o["delivered_at"],
                "user_response": o["user_response"],
            }
            for o in recent_outcomes[:5]
        ],
    }

    profile = store.get_all_profile()
    if profile:
        context["user_profile"] = profile

    # Trend: week-over-week application delta
    apps_prev_7d = store.count_observations("application", _days_ago(14)) - apps_7d
    if apps_prev_7d > 0:
        context["activity_metrics"]["trend_wow"] = round(
            (apps_7d - apps_prev_7d) / apps_prev_7d, 2
        )
    elif apps_7d > 0:
        context["activity_metrics"]["trend_wow"] = 1.0
    else:
        context["activity_metrics"]["trend_wow"] = 0.0

    # Days since last observation of any kind
    if recent_obs:
        last_obs_time = recent_obs[0].get("observed_at", "")
        try:
            last_dt = datetime.fromisoformat(last_obs_time)
            context["activity_metrics"]["days_since_last_observation"] = (now - last_dt).days
        except (ValueError, TypeError):
            pass

    # Interview count
    interviews_14d = store.count_observations("interview", _days_ago(14))
    context["activity_metrics"]["interviews_last_14d"] = interviews_14d

    # Shutdown detection signals
    outcomes_14d = [o for o in store.get_recent_outcomes(limit=30) if o.get("delivered_at", "") >= _days_ago(14)]
    engaged_14d = sum(1 for o in outcomes_14d if o.get("user_response") == "engaged")
    total_14d = len(outcomes_14d)
    response_rate_14d = engaged_14d / total_14d if total_14d > 0 else None

    # Compute days since any user-initiated observation (not system-generated)
    user_obs = store.get_observations(limit=50)
    last_user_activity = None
    for o in user_obs:
        if o.get("source") in ("manual_log", "user_message"):
            last_user_activity = o.get("observed_at")
            break

    days_since_user_activity = None
    if last_user_activity:
        try:
            last_dt = datetime.fromisoformat(last_user_activity)
            days_since_user_activity = (now - last_dt).days
        except (ValueError, TypeError):
            pass

    context["shutdown_signals"] = {
        "response_rate_14d": response_rate_14d,
        "days_since_user_activity": days_since_user_activity,
        "applications_last_28d": apps_28d,
        "all_activity_zero_14d": (
            apps_14d == 0
            and interviews_14d == 0
            and (days_since_user_activity is None or days_since_user_activity >= 14)
        ),
    }

    # Check if a support or refer message was already sent recently
    for o in recent_outcomes:
        if o.get("action_type") in ("support", "refer"):
            context["shutdown_signals"]["last_support_sent"] = o.get("delivered_at")
            context["shutdown_signals"]["last_support_response"] = o.get("user_response")
            break

    return json.dumps(context, indent=2)


def build_compilation_context(store: MarvinStore) -> str:
    """Build context for the nightly fact compilation pass.

    Includes observations since last compilation, current facts, goals, and profile.
    """
    last_compiled = store.get_meta("last_compilation")
    since = last_compiled or _days_ago(90)

    observations = store.get_observations(since=since, limit=200)
    current_facts = store.get_current_facts()
    active_goals = store.get_active_goals()
    profile = store.get_all_profile()
    categories = store.get_observation_categories(since=since)

    context = {
        "compilation_window": {
            "since": since,
            "until": datetime.now(timezone.utc).isoformat(),
            "observation_count": len(observations),
        },
        "observation_summary_by_category": [
            {"category": c["category"], "count": c["count"]}
            for c in categories
        ],
        "observations": [
            {
                "id": o["id"],
                "observed_at": o["observed_at"],
                "source": o["source"],
                "category": o["category"],
                "content": o["content"],
            }
            for o in observations
        ],
        "current_facts": [
            {"id": f["id"], "claim": f["claim"], "confidence": f["confidence"]}
            for f in current_facts
        ],
        "active_goals": [
            {"area": g["area"], "description": g["description"], "targets": g["targets"]}
            for g in active_goals
        ],
    }

    if profile:
        context["user_profile"] = profile

    return json.dumps(context, indent=2)


def build_transparency_context(store: MarvinStore) -> str:
    """Build context for the weekly transparency ritual.

    Includes everything the client should see: this week's observations,
    current facts, goals, outcomes, and profile.
    """
    week_ago = _days_ago(7)
    observations = store.get_observations(since=week_ago, limit=50)
    current_facts = store.get_current_facts()
    active_goals = store.get_active_goals()
    outcomes = store.get_recent_outcomes(limit=15)
    outcomes_this_week = [o for o in outcomes if o.get("delivered_at", "") >= week_ago]
    profile = store.get_all_profile()

    apps_7d = store.count_observations("application", week_ago)
    interviews_7d = store.count_observations("interview", week_ago)

    context = {
        "week_summary": {
            "applications": apps_7d,
            "interviews": interviews_7d,
            "total_observations": len(observations),
        },
        "observations_this_week": [
            {
                "observed_at": o["observed_at"],
                "category": o["category"],
                "content": o["content"],
            }
            for o in observations[:20]
        ],
        "current_facts": [
            {"id": f["id"], "claim": f["claim"], "confidence": f["confidence"]}
            for f in current_facts
        ],
        "active_goals": [
            {"area": g["area"], "description": g["description"], "targets": g["targets"]}
            for g in active_goals
        ],
        "interventions_this_week": [
            {
                "action_type": o["action_type"],
                "content": o["content"][:200],
                "user_response": o["user_response"],
            }
            for o in outcomes_this_week
        ],
    }

    if profile:
        context["user_profile"] = profile

    return json.dumps(context, indent=2)


def build_session_context(store: MarvinStore) -> str:
    """Build lighter context for injection during interactive chat sessions.

    Includes current facts and goals but fewer raw observations,
    since the user is actively engaged and providing real-time signals.
    """
    current_facts = store.get_current_facts()
    active_goals = store.get_active_goals()
    recent_obs = store.get_observations(limit=10)

    context = {
        "marvin_coaching_context": True,
        "current_facts": [
            {"claim": f["claim"], "confidence": f["confidence"]}
            for f in current_facts
        ],
        "active_goals": [
            {
                "area": g["area"],
                "description": g["description"],
                "targets": g["targets"],
            }
            for g in active_goals
        ],
        "recent_activity_summary": [
            {
                "category": o["category"],
                "content": o["content"],
                "observed_at": o["observed_at"],
            }
            for o in recent_obs
        ],
    }

    profile = store.get_all_profile()
    if profile:
        context["user_profile"] = profile

    return json.dumps(context, indent=2)
