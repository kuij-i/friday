"""Lightweight fakes so the agent loop can be tested without the network."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    name: str
    input: dict[str, Any]
    id: str = "tool_1"
    type: str = "tool_use"


@dataclass
class StopDetails:
    category: str | None = None
    explanation: str = ""


@dataclass
class FakeResponse:
    content: list[Any]
    stop_reason: str
    stop_details: StopDetails | None = None


class FakeMessages:
    def __init__(self, responses: list[FakeResponse]):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs) -> FakeResponse:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeMessages ran out of scripted responses")
        return self._responses.pop(0)


class FakeClient:
    """Mimics anthropic.Anthropic for the subset agent.py uses."""

    def __init__(self, responses: list[FakeResponse]):
        self.messages = FakeMessages(responses)
