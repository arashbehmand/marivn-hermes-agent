"""Tests for marvin.context — all four context builders."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from marvin.store import MarvinStore


def _days_ago(d):
    return (datetime.now(timezone.utc) - timedelta(days=d)).isoformat()


@pytest.fixture
def store(tmp_path):
    return MarvinStore(db_path=tmp_path / "test.db")


@pytest.fixture
def seeded_store(store):
    """Store with realistic data for context building."""
    store.set_profile("name", "Alex")
    store.set_profile("adhd", "yes")

    store.add_goal("job_search", "Land a senior backend role", {"apps_per_week": 5})

    for i in range(3):
        store.add_observation("manual_log", "application", {"company": f"Co{i}"})
    store.add_observation("manual_log", "interview", {"company": "Acme"})
    store.add_observation("user_message", "interaction", {"message_preview": "hello"})

    store.add_fact("user averages 3 apps/week", 0.8)

    store.add_outcome("nudge", "Try applying to Acme", "telegram")
    oid = store.add_outcome("check_in", "How are things?", "telegram")
    store.update_outcome_response(oid, "engaged", "replied")

    return store


class TestBuildCheckinContext:
    def test_returns_valid_json(self, seeded_store):
        from marvin.context import build_checkin_context

        result = build_checkin_context(seeded_store)
        data = json.loads(result)
        assert isinstance(data, dict)

    def test_contains_required_keys(self, seeded_store):
        from marvin.context import build_checkin_context

        data = json.loads(build_checkin_context(seeded_store))
        assert "activity_metrics" in data
        assert "engagement" in data
        assert "current_facts" in data
        assert "active_goals" in data
        assert "recent_outcomes" in data
        assert "shutdown_signals" in data

    def test_activity_metrics(self, seeded_store):
        from marvin.context import build_checkin_context

        data = json.loads(build_checkin_context(seeded_store))
        metrics = data["activity_metrics"]
        assert metrics["applications_last_7d"] == 3
        assert metrics["interviews_last_14d"] == 1

    def test_profile_included(self, seeded_store):
        from marvin.context import build_checkin_context

        data = json.loads(build_checkin_context(seeded_store))
        assert data["user_profile"]["name"] == "Alex"

    def test_shutdown_signals_present(self, seeded_store):
        from marvin.context import build_checkin_context

        data = json.loads(build_checkin_context(seeded_store))
        signals = data["shutdown_signals"]
        assert "all_activity_zero_14d" in signals
        assert "days_since_user_activity" in signals
        assert signals["all_activity_zero_14d"] is False

    def test_empty_store(self, store):
        from marvin.context import build_checkin_context

        data = json.loads(build_checkin_context(store))
        assert data["activity_metrics"]["applications_last_7d"] == 0
        assert data["current_facts"] == []
        assert data["active_goals"] == []


class TestBuildCompilationContext:
    def test_returns_valid_json(self, seeded_store):
        from marvin.context import build_compilation_context

        data = json.loads(build_compilation_context(seeded_store))
        assert "compilation_window" in data
        assert "observations" in data
        assert "current_facts" in data

    def test_observation_count_matches(self, seeded_store):
        from marvin.context import build_compilation_context

        data = json.loads(build_compilation_context(seeded_store))
        assert data["compilation_window"]["observation_count"] == len(data["observations"])

    def test_respects_last_compilation_meta(self, seeded_store):
        from marvin.context import build_compilation_context

        seeded_store.set_meta("last_compilation", _days_ago(1))
        seeded_store.add_observation("x", "new", {}, observed_at=_days_ago(0))

        data = json.loads(build_compilation_context(seeded_store))
        assert data["compilation_window"]["observation_count"] >= 1

    def test_includes_fact_ids_for_supersession(self, seeded_store):
        from marvin.context import build_compilation_context

        data = json.loads(build_compilation_context(seeded_store))
        for fact in data["current_facts"]:
            assert "id" in fact

    def test_categories_summary(self, seeded_store):
        from marvin.context import build_compilation_context

        data = json.loads(build_compilation_context(seeded_store))
        cats = data["observation_summary_by_category"]
        cat_names = [c["category"] for c in cats]
        assert "application" in cat_names


class TestBuildTransparencyContext:
    def test_returns_valid_json(self, seeded_store):
        from marvin.context import build_transparency_context

        data = json.loads(build_transparency_context(seeded_store))
        assert "week_summary" in data

    def test_week_summary_counts(self, seeded_store):
        from marvin.context import build_transparency_context

        data = json.loads(build_transparency_context(seeded_store))
        assert data["week_summary"]["applications"] == 3
        assert data["week_summary"]["interviews"] == 1

    def test_interventions_this_week(self, seeded_store):
        from marvin.context import build_transparency_context

        data = json.loads(build_transparency_context(seeded_store))
        assert len(data["interventions_this_week"]) >= 1


class TestBuildSessionContext:
    def test_returns_valid_json(self, seeded_store):
        from marvin.context import build_session_context

        data = json.loads(build_session_context(seeded_store))
        assert data["marvin_coaching_context"] is True

    def test_contains_facts_and_goals(self, seeded_store):
        from marvin.context import build_session_context

        data = json.loads(build_session_context(seeded_store))
        assert len(data["current_facts"]) == 1
        assert len(data["active_goals"]) == 1

    def test_lighter_than_checkin(self, seeded_store):
        from marvin.context import build_session_context, build_checkin_context

        session = json.loads(build_session_context(seeded_store))
        checkin = json.loads(build_checkin_context(seeded_store))
        assert "shutdown_signals" not in session
        assert "shutdown_signals" in checkin
