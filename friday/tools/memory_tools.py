"""Tools that let Friday read and write its own long-term memory."""
from __future__ import annotations

from ..memory import Memory
from .base import Risk, Tool, ToolResult


def make_memory_tools(memory: Memory) -> list[Tool]:
    def remember(topic: str, content: str) -> ToolResult:
        memory.remember_fact(topic, content)
        return ToolResult(f"Remembered under '{topic}': {content}")

    def recall(topic: str = "") -> ToolResult:
        facts = memory.recall_facts(topic or None)
        if not facts:
            return ToolResult("Nothing remembered for that topic.")
        return ToolResult("\n".join(f"[{f['topic']}] {f['content']}" for f in facts))

    def set_preference(key: str, value: str) -> ToolResult:
        memory.set_preference(key, value)
        return ToolResult(f"Preference set: {key} = {value}")

    def forget(content_substring: str) -> ToolResult:
        n = memory.forget_fact(content_substring)
        return ToolResult(f"Forgot {n} matching fact(s).")

    return [
        Tool(
            name="remember",
            description=(
                "Store a durable fact about the user, their projects, or your working context, "
                "so you recall it in future sessions. Use whenever you learn something lasting."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Short category, e.g. 'projects', 'preferences', 'system'."},
                    "content": {"type": "string", "description": "The fact to remember."},
                },
                "required": ["topic", "content"],
                "additionalProperties": False,
            },
            risk=Risk.LOW,
            handler=remember,
        ),
        Tool(
            name="recall",
            description="Look up previously remembered facts, optionally filtered by topic.",
            input_schema={
                "type": "object",
                "properties": {"topic": {"type": "string", "description": "Optional topic filter."}},
                "required": [],
                "additionalProperties": False,
            },
            risk=Risk.SAFE,
            handler=recall,
        ),
        Tool(
            name="set_preference",
            description="Record a user preference about how you should behave (e.g. 'reply_style = terse').",
            input_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["key", "value"],
                "additionalProperties": False,
            },
            risk=Risk.LOW,
            handler=set_preference,
        ),
        Tool(
            name="forget",
            description="Delete remembered facts whose content contains the given substring.",
            input_schema={
                "type": "object",
                "properties": {"content_substring": {"type": "string"}},
                "required": ["content_substring"],
                "additionalProperties": False,
            },
            risk=Risk.LOW,
            handler=forget,
        ),
    ]
