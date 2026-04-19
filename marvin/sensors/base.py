"""
Base sensor adapter — converts MCP tool outputs into typed Observations.

Sensors wrap hermes MCP tool results (captured via post_tool_call hook)
and extract structured metadata as Observations in the canonical store.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional

from marvin.store import MarvinStore


class SensorAdapter(ABC):
    """Base class for sensor adapters that process MCP tool results."""

    sensor_id: str

    @abstractmethod
    def match(self, tool_name: str, args: dict) -> bool:
        """Return True if this sensor should process this tool call."""
        ...

    @abstractmethod
    def extract(self, tool_name: str, args: dict, result: str) -> list[dict]:
        """Extract observations from a tool call result.

        Returns a list of observation dicts with keys:
            category, content, source (defaults to sensor_id)
        """
        ...

    def process(self, store: MarvinStore, tool_name: str, args: dict, result: str):
        if not self.match(tool_name, args):
            return
        observations = self.extract(tool_name, args, result)
        for obs in observations:
            store.add_observation(
                source=obs.get("source", self.sensor_id),
                category=obs["category"],
                content=obs.get("content", {}),
            )
