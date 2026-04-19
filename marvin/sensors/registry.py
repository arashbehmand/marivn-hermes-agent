"""
Sensor registry — manages all active sensors and routes tool call results to them.
"""

from marvin.sensors.base import SensorAdapter
from marvin.sensors.calendar import CalendarSensor
from marvin.sensors.email import EmailSensor
from marvin.sensors.documents import DocumentSensor
from marvin.store import MarvinStore


class SensorRegistry:
    def __init__(self):
        self._sensors: list[SensorAdapter] = [
            CalendarSensor(),
            EmailSensor(),
            DocumentSensor(),
        ]

    def process_tool_call(self, store: MarvinStore, tool_name: str, args: dict, result: str):
        for sensor in self._sensors:
            try:
                sensor.process(store, tool_name, args, result)
            except Exception:
                pass  # Sensor failures never break the agent loop
