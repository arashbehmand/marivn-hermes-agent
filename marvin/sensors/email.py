"""
Email sensor — detects application confirmations, recruiter replies, and rejections.

Processes output from Gmail/email MCP tool calls.
"""

import json
import re

from marvin.sensors.base import SensorAdapter


class EmailSensor(SensorAdapter):
    sensor_id = "email"

    _TOOL_PATTERNS = [
        "gmail", "email", "mail",
        "list_messages", "search_messages", "get_message", "read_email",
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

        messages = []
        if isinstance(data, list):
            messages = data
        elif isinstance(data, dict):
            messages = data.get("messages", data.get("emails", []))
            if not messages and "subject" in data:
                messages = [data]

        for msg in messages:
            if not isinstance(msg, dict):
                continue

            subject = msg.get("subject", "").lower()
            sender = msg.get("from", msg.get("sender", "")).lower()
            snippet = msg.get("snippet", msg.get("body", ""))[:300].lower()

            # Application confirmation
            if any(kw in subject for kw in ("application received", "application submitted", "thank you for applying", "we received your")):
                company = _extract_company_from_email(sender, subject)
                observations.append({
                    "category": "application",
                    "content": {
                        "company": company,
                        "type": "confirmation",
                        "source": "email",
                        "subject": msg.get("subject", "")[:100],
                    },
                })

            # Recruiter outreach / interview scheduling
            elif any(kw in subject + snippet for kw in ("schedule an interview", "interview invitation", "next steps", "phone screen", "like to chat")):
                company = _extract_company_from_email(sender, subject)
                observations.append({
                    "category": "interview",
                    "content": {
                        "company": company,
                        "type": "scheduling",
                        "source": "email",
                        "subject": msg.get("subject", "")[:100],
                    },
                })

            # Rejection
            elif any(kw in subject + snippet for kw in ("unfortunately", "decided not to move forward", "other candidates", "not a fit", "position has been filled")):
                company = _extract_company_from_email(sender, subject)
                observations.append({
                    "category": "rejection",
                    "content": {
                        "company": company,
                        "source": "email",
                        "subject": msg.get("subject", "")[:100],
                    },
                })

        return observations


def _extract_company_from_email(sender: str, subject: str) -> str:
    """Try to extract company name from sender or subject."""
    # From address often has company: "jobs@acme.com", "recruiting@globex.io"
    domain_match = re.search(r"@([\w-]+)\.", sender)
    if domain_match:
        domain = domain_match.group(1)
        if domain not in ("gmail", "yahoo", "hotmail", "outlook", "protonmail"):
            return domain.capitalize()

    # Try subject
    patterns = [
        r"(?:from|at)\s+(\w[\w\s]*?)(?:\s*[-–—]|$)",
        r"(\w[\w\s]*?)\s*(?:application|interview|position)",
    ]
    for p in patterns:
        match = re.search(p, subject, re.IGNORECASE)
        if match:
            return match.group(1).strip()

    return "Unknown"
