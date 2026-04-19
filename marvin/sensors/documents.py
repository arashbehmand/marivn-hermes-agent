"""
Document sensor — detects resume/document analysis tool calls.

Processes output from file-reading MCP tools when documents are analyzed.
Extracts structural metadata as observations (not raw content).
"""

import json
import re

from marvin.sensors.base import SensorAdapter


class DocumentSensor(SensorAdapter):
    sensor_id = "documents"

    _TOOL_PATTERNS = [
        "read_file", "read_document", "analyze_document",
        "file_read", "get_file", "list_files",
    ]

    _RESUME_PATTERNS = [
        "resume", "cv", "cover.letter", "coverletter",
    ]

    def match(self, tool_name: str, args: dict) -> bool:
        name_lower = tool_name.lower()
        if not any(p in name_lower for p in self._TOOL_PATTERNS):
            return False

        # Only capture if the file looks like a resume/cover letter
        file_path = str(args.get("path", args.get("file_path", args.get("filename", "")))).lower()
        return any(p in file_path for p in self._RESUME_PATTERNS)

    def extract(self, tool_name: str, args: dict, result: str) -> list[dict]:
        observations = []

        file_path = str(args.get("path", args.get("file_path", args.get("filename", ""))))

        # Extract structural metadata — NOT content
        word_count = len(result.split()) if result else 0
        line_count = result.count('\n') + 1 if result else 0

        # Detect section presence
        sections_found = []
        content_lower = result.lower() if result else ""
        for section in ("experience", "education", "skills", "summary", "objective",
                        "projects", "certifications", "awards", "languages"):
            if section in content_lower:
                sections_found.append(section)

        # Detect quantified achievements (numbers in context of results)
        has_metrics = bool(re.search(r'\d+[%x]|\$\d|\d+\s*(?:users|customers|revenue|growth|increase|decrease)', content_lower))

        observations.append({
            "category": "document_analysis",
            "content": {
                "file": file_path,
                "type": _detect_doc_type(file_path),
                "word_count": word_count,
                "line_count": line_count,
                "sections_found": sections_found,
                "has_quantified_metrics": has_metrics,
            },
        })

        return observations


def _detect_doc_type(path: str) -> str:
    path_lower = path.lower()
    if "cover" in path_lower:
        return "cover_letter"
    if any(kw in path_lower for kw in ("resume", "cv")):
        return "resume"
    return "document"
