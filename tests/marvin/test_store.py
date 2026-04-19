"""Tests for marvin.store — canonical behavioral ledger."""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marvin.store import MarvinStore, get_marvin_home


def _days_ago(d):
    return (datetime.now(timezone.utc) - timedelta(days=d)).isoformat()


@pytest.fixture
def store(tmp_path):
    return MarvinStore(db_path=tmp_path / "test.db")


class TestObservations:
    def test_add_and_retrieve(self, store):
        oid = store.add_observation("manual_log", "application", {"company": "Acme"})
        assert oid >= 1
        obs = store.get_observations(category="application")
        assert len(obs) == 1
        assert obs[0]["content"]["company"] == "Acme"

    def test_count(self, store):
        since = _days_ago(7)
        store.add_observation("manual_log", "application", {"company": "A"})
        store.add_observation("manual_log", "application", {"company": "B"})
        store.add_observation("manual_log", "interview", {"company": "C"})
        assert store.count_observations("application", since) == 2
        assert store.count_observations("interview", since) == 1

    def test_observations_between(self, store):
        store.add_observation("x", "a", {}, observed_at=_days_ago(10))
        store.add_observation("x", "a", {}, observed_at=_days_ago(5))
        store.add_observation("x", "a", {}, observed_at=_days_ago(1))
        result = store.get_observations_between(_days_ago(7), _days_ago(0))
        assert len(result) == 2

    def test_categories(self, store):
        store.add_observation("x", "app", {})
        store.add_observation("x", "app", {})
        store.add_observation("x", "interview", {})
        cats = store.get_observation_categories()
        assert cats[0]["category"] == "app"
        assert cats[0]["count"] == 2


class TestFacts:
    def test_add_and_retrieve(self, store):
        fid = store.add_fact("user is active", 0.8)
        facts = store.get_current_facts()
        assert len(facts) == 1
        assert facts[0]["claim"] == "user is active"

    def test_supersede(self, store):
        f1 = store.add_fact("user is active", 0.8)
        f2 = store.supersede_fact(f1, "user has gone quiet", 0.9)
        facts = store.get_current_facts()
        claims = [f["claim"] for f in facts]
        assert "user is active" not in claims
        assert "user has gone quiet" in claims


class TestGoals:
    def test_add_and_deactivate(self, store):
        gid = store.add_goal("job_search", "Find a role", {"apps_per_week": 5})
        assert len(store.get_active_goals()) == 1
        store.deactivate_goal(gid)
        assert len(store.get_active_goals()) == 0


class TestOutcomes:
    def test_add_and_response(self, store):
        oid = store.add_outcome("nudge", "Try Acme today", "telegram")
        store.update_outcome_response(oid, "engaged", "user replied")
        outcomes = store.get_recent_outcomes()
        assert outcomes[0]["user_response"] == "engaged"

    def test_unresponded(self, store):
        store.add_outcome("nudge", "msg1", "telegram")
        store.add_outcome("nudge", "msg2", "telegram")
        oid3 = store.add_outcome("nudge", "msg3", "telegram")
        store.update_outcome_response(oid3, "engaged")
        unresponded = store.get_unresponded_outcomes()
        assert len(unresponded) == 2


class TestProfile:
    def test_set_and_get(self, store):
        store.set_profile("name", "Farhad")
        assert store.get_profile("name") == "Farhad"
        assert store.get_profile("missing", "default") == "default"

    def test_get_all(self, store):
        store.set_profile("a", "1")
        store.set_profile("b", "2")
        profile = store.get_all_profile()
        assert profile == {"a": "1", "b": "2"}


class TestMeta:
    def test_set_and_get(self, store):
        store.set_meta("last_compilation", "2026-01-01")
        assert store.get_meta("last_compilation") == "2026-01-01"
        assert store.get_meta("missing") is None


class TestGetMarvinHome:
    def test_respects_hermes_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        assert get_marvin_home() == tmp_path / "marvin"

    def test_default(self, monkeypatch):
        monkeypatch.delenv("HERMES_HOME", raising=False)
        assert get_marvin_home() == Path.home() / ".hermes" / "marvin"
