"""
Calendar sensor — extracts interview dates, free blocks, and scheduling patterns.

Processes output from Google Calendar MCP tool calls.
"""

import json
import re
from typing import Any

from marvin.sensors.base import SensorAdapter


class CalendarSensor(SensorAdapter):
    sensor_id = "calendar"

    # Tool names from common Google Calendar MCP servers
    _TOOL_PATTERNS = [
        "calendar", "gcal", "google_calendar",
        "list_events", "get_events", "search_events", "create_event",
    ]

    def match(self, tool_name: str, args: dict) -> bool:
        name_lower = tool_name.lower()
        return any(p in name_lower for p in self._TOOL_PATTERNS)

    def extract(self, tool_name: str, args: dict, result: str) -> list[dict]:
        observations = []

        try:
            data = json.loads(result) if isinstance(result, str) else result
        except (json.JSONDecodeError, TypeError):
            data = {"raw": result[:500] if result else ""}

        events = []
        if isinstance(data, list):
            events = data
        elif isinstance(data, dict):
            events = data.get("events", data.get("items", []))
            if not events and "summary" in data:
                events = [data]

        for event in events:
            if not isinstance(event, dict):
                continue

            summary = event.get("summary", event.get("title", "")).lower()

            # Detect interviews
            if any(kw in summary for kw in ("interview", "screen", "technical", "behavioral", "onsite")):
                observations.append({
                    "category": "interview",
                    "content": {
                        "company": _extract_company(summary),
                        "date": event.get("start", {}).get("dateTime", event.get("start", "")),
                        "type": summary,
                        "source": "calendar",
                    },
                })

            # Detect cancelled events
            status = event.get("status", "")
            if status == "cancelled":
                observations.append({
                    "category": "activity",
                    "content": {
                        "event": "cancelled",
                        "title": summary,
                        "date": event.get("start", {}).get("dateTime", ""),
                    },
                })

        # If it's a write operation (creating an event), record that
        if "create" in tool_name.lower():
            observations.append({
                "category": "activity",
                "content": {
                    "event": "calendar_event_created",
                    "args": {k: str(v)[:100] for k, v in args.items()},
                },
            })

        return observations


def _extract_company(text: str) -> str:
    """Try to extract a company name from an event title."""
    # Common patterns: "Interview with Acme", "Acme - Technical Screen"
    patterns = [
        r"(?:interview|screen|call)\s+(?:with|at|@)\s+(.+)",
        r"(.+?)\s*[-–—]\s*(?:interview|screen|call|technical|behavioral)",
    ]
    for p in patterns:
        match = re.search(p, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return text
