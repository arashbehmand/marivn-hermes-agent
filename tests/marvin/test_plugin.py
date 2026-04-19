"""Tests for the marvin plugin — cron discrimination, action detection, compilation parsing."""

import sys
from pathlib import Path

import pytest

_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from marvin.store import MarvinStore

# Import plugin internals directly
_plugin_dir = _repo_root / ".hermes" / "plugins" / "marvin"
if str(_plugin_dir) not in sys.path:
    sys.path.insert(0, str(_plugin_dir))

# We need to import from the plugin __init__.py which is a package
import importlib.util

_plugin_spec = importlib.util.spec_from_file_location(
    "marvin_plugin", _plugin_dir / "__init__.py"
)
_plugin_mod = importlib.util.module_from_spec(_plugin_spec)
_plugin_spec.loader.exec_module(_plugin_mod)

_is_cron = _plugin_mod._is_cron
_detect_action_type = _plugin_mod._detect_action_type
_try_process_compilation = _plugin_mod._try_process_compilation


@pytest.fixture
def store(tmp_path):
    return MarvinStore(db_path=tmp_path / "test.db")


class TestIsCron:
    def test_cron_platform(self):
        assert _is_cron(platform="cron") is True

    def test_cron_session_id_prefix(self):
        assert _is_cron(session_id="cron_123_456") is True

    def test_normal_session(self):
        assert _is_cron(platform="cli", session_id="abc123") is False

    def test_empty_args(self):
        assert _is_cron() is False

    def test_telegram_platform(self):
        assert _is_cron(platform="telegram", session_id="user_789") is False


class TestDetectActionType:
    def test_refer(self):
        assert _detect_action_type("Talking to a person might help more than I can") == "refer"

    def test_support(self):
        assert _detect_action_type("Things have been quiet, and that's okay") == "support"

    def test_check_in(self):
        assert _detect_action_type("Just checking in — how are things going?") == "check_in"

    def test_encourage(self):
        assert _detect_action_type("Great work this week, keep it up!") == "encourage"

    def test_nudge(self):
        assert _detect_action_type("Want to try sending one application today?") == "nudge"

    def test_default_is_check_in(self):
        assert _detect_action_type("The sky is blue today.") == "check_in"


class TestTryProcessCompilation:
    def test_new_facts(self, store):
        response = '```json\n{"new_facts": [{"claim": "user is active", "confidence": 0.9}]}\n```'
        _try_process_compilation(store, response)
        facts = store.get_current_facts()
        assert len(facts) == 1
        assert facts[0]["claim"] == "user is active"

    def test_supersede(self, store):
        fid = store.add_fact("old claim", 0.5)
        response = f'{{"supersede": [{{"old_fact_id": {fid}, "new_claim": "updated claim", "confidence": 0.8}}]}}'
        _try_process_compilation(store, response)
        facts = store.get_current_facts()
        claims = [f["claim"] for f in facts]
        assert "updated claim" in claims
        assert "old claim" not in claims

    def test_invalidate(self, store):
        fid = store.add_fact("wrong claim", 0.5)
        response = f'{{"invalidate": [{fid}]}}'
        _try_process_compilation(store, response)
        facts = store.get_current_facts()
        active_claims = [f["claim"] for f in facts if not f["claim"].startswith("[invalidated")]
        assert "wrong claim" not in active_claims

    def test_sets_last_compilation_meta(self, store):
        response = '{"new_facts": [{"claim": "test", "confidence": 0.5}]}'
        _try_process_compilation(store, response)
        assert store.get_meta("last_compilation") is not None

    def test_ignores_non_compilation_json(self, store):
        response = '{"message": "hello there"}'
        _try_process_compilation(store, response)
        facts = store.get_current_facts()
        assert len(facts) == 0

    def test_ignores_non_json(self, store):
        response = "Just a normal text response"
        _try_process_compilation(store, response)
        facts = store.get_current_facts()
        assert len(facts) == 0
