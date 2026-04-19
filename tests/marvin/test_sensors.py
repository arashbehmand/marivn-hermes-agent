"""Tests for marvin.sensors — calendar, email, documents, and registry."""

import json

import pytest

from marvin.store import MarvinStore
from marvin.sensors.calendar import CalendarSensor
from marvin.sensors.email import EmailSensor
from marvin.sensors.documents import DocumentSensor
from marvin.sensors.registry import SensorRegistry


@pytest.fixture
def store(tmp_path):
    return MarvinStore(db_path=tmp_path / "test.db")


class TestCalendarSensor:
    def test_match_calendar_tools(self):
        s = CalendarSensor()
        assert s.match("google_calendar_list_events", {}) is True
        assert s.match("gcal_search", {}) is True
        assert s.match("create_event", {}) is True
        assert s.match("send_email", {}) is False

    def test_extract_interview(self):
        s = CalendarSensor()
        result = json.dumps([
            {"summary": "Interview with Acme", "start": {"dateTime": "2026-04-20T10:00:00Z"}}
        ])
        obs = s.extract("list_events", {}, result)
        assert len(obs) == 1
        assert obs[0]["category"] == "interview"
        assert obs[0]["content"]["company"].lower() == "acme"

    def test_extract_cancelled_event(self):
        s = CalendarSensor()
        result = json.dumps([
            {"summary": "Team standup", "status": "cancelled", "start": {"dateTime": "2026-04-20T09:00:00Z"}}
        ])
        obs = s.extract("list_events", {}, result)
        assert any(o["category"] == "activity" and o["content"]["event"] == "cancelled" for o in obs)

    def test_extract_create_event(self):
        s = CalendarSensor()
        result = json.dumps({"status": "confirmed"})
        obs = s.extract("create_event", {"summary": "Coffee chat"}, result)
        assert any(o["content"]["event"] == "calendar_event_created" for o in obs)

    def test_extract_no_interview_keywords(self):
        s = CalendarSensor()
        result = json.dumps([{"summary": "Dentist appointment", "start": {"dateTime": "2026-04-20"}}])
        obs = s.extract("list_events", {}, result)
        assert not any(o["category"] == "interview" for o in obs)

    def test_process_writes_to_store(self, store):
        s = CalendarSensor()
        result = json.dumps([
            {"summary": "Technical screen with Globex", "start": {"dateTime": "2026-04-21T14:00:00Z"}}
        ])
        s.process(store, "list_events", {}, result)
        obs = store.get_observations(category="interview")
        assert len(obs) == 1

    def test_bad_json_result(self):
        s = CalendarSensor()
        obs = s.extract("list_events", {}, "not json at all")
        assert isinstance(obs, list)


class TestEmailSensor:
    def test_match_email_tools(self):
        s = EmailSensor()
        assert s.match("gmail_search_messages", {}) is True
        assert s.match("read_email", {}) is True
        assert s.match("list_events", {}) is False

    def test_extract_application_confirmation(self):
        s = EmailSensor()
        result = json.dumps([{
            "subject": "Application Received - Software Engineer",
            "from": "jobs@acme.com",
            "snippet": "Thank you for applying",
        }])
        obs = s.extract("gmail_search_messages", {}, result)
        assert len(obs) == 1
        assert obs[0]["category"] == "application"
        assert obs[0]["content"]["company"] == "Acme"

    def test_extract_interview_scheduling(self):
        s = EmailSensor()
        result = json.dumps([{
            "subject": "Next steps in your application",
            "from": "recruiting@globex.io",
            "snippet": "We'd like to schedule an interview",
        }])
        obs = s.extract("gmail_search_messages", {}, result)
        assert len(obs) == 1
        assert obs[0]["category"] == "interview"

    def test_extract_rejection(self):
        s = EmailSensor()
        result = json.dumps([{
            "subject": "Update on your application",
            "from": "hr@bigco.com",
            "snippet": "Unfortunately, we have decided not to move forward",
        }])
        obs = s.extract("gmail_search_messages", {}, result)
        assert len(obs) == 1
        assert obs[0]["category"] == "rejection"

    def test_extract_generic_sender(self):
        s = EmailSensor()
        result = json.dumps([{
            "subject": "Application Received",
            "from": "noreply@gmail.com",
            "snippet": "We received your application",
        }])
        obs = s.extract("gmail_search_messages", {}, result)
        assert len(obs) == 1
        assert obs[0]["content"]["company"] != "Gmail"

    def test_no_match_for_normal_email(self):
        s = EmailSensor()
        result = json.dumps([{
            "subject": "Meeting notes",
            "from": "coworker@company.com",
            "snippet": "Here are the notes from today's meeting",
        }])
        obs = s.extract("gmail_search_messages", {}, result)
        assert len(obs) == 0


class TestDocumentSensor:
    def test_match_resume_read(self):
        s = DocumentSensor()
        assert s.match("read_file", {"path": "/home/user/resume.pdf"}) is True
        assert s.match("read_file", {"file_path": "/docs/my_cv.docx"}) is True
        assert s.match("read_file", {"path": "/docs/notes.txt"}) is False

    def test_match_requires_tool_pattern(self):
        s = DocumentSensor()
        assert s.match("send_email", {"path": "/home/user/resume.pdf"}) is False

    def test_extract_resume_metadata(self):
        s = DocumentSensor()
        content = (
            "John Doe\nSenior Backend Engineer\n\n"
            "Experience\nAcme Corp — built a system handling 10000 users\n\n"
            "Education\nBS Computer Science\n\n"
            "Skills\nPython, Go, PostgreSQL"
        )
        obs = s.extract("read_file", {"path": "/home/user/resume.pdf"}, content)
        assert len(obs) == 1
        meta = obs[0]["content"]
        assert meta["type"] == "resume"
        assert "experience" in meta["sections_found"]
        assert "education" in meta["sections_found"]
        assert "skills" in meta["sections_found"]
        assert meta["has_quantified_metrics"] is True
        assert meta["word_count"] > 0

    def test_extract_cover_letter_type(self):
        s = DocumentSensor()
        obs = s.extract("read_file", {"path": "/docs/cover_letter.pdf"}, "Dear hiring manager")
        assert obs[0]["content"]["type"] == "cover_letter"

    def test_no_metrics_detected(self):
        s = DocumentSensor()
        obs = s.extract("read_file", {"path": "/docs/resume.pdf"}, "I did things and stuff")
        assert obs[0]["content"]["has_quantified_metrics"] is False


class TestSensorRegistry:
    def test_routes_to_correct_sensor(self, store):
        registry = SensorRegistry()
        result = json.dumps([{
            "summary": "Interview with TestCo",
            "start": {"dateTime": "2026-04-22T10:00:00Z"},
        }])
        registry.process_tool_call(store, "list_events", {}, result)
        obs = store.get_observations(category="interview")
        assert len(obs) == 1

    def test_ignores_unmatched_tools(self, store):
        registry = SensorRegistry()
        registry.process_tool_call(store, "unknown_tool", {}, "some result")
        obs = store.get_observations()
        assert len(obs) == 0

    def test_sensor_failure_isolated(self, store):
        registry = SensorRegistry()
        registry.process_tool_call(store, "list_events", {}, "{invalid json content")
        obs = store.get_observations()
        assert len(obs) == 0
