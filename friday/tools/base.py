"""Tool infrastructure: risk levels, results, and the registry."""
from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class Risk(enum.IntEnum):
    SAFE = 0      # read-only, no side effects (recall memory, list files, sysinfo)
    LOW = 1       # minor side effects (remember a fact, write inside workspace)
    DANGEROUS = 2 # arbitrary effects (shell, python exec) — always gated


@dataclass
class ToolResult:
    content: str
    is_error: bool = False


# An approver decides whether a DANGEROUS/LOW action may run.
# Signature: (tool_name, tool_input, risk) -> (approved: bool, reason: str)
Approver = Callable[[str, dict[str, Any], Risk], "tuple[bool, str]"]


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    risk: Risk
    handler: Callable[..., ToolResult]

    def api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def api_schemas(self) -> list[dict[str, Any]]:
        return [t.api_schema() for t in self._tools.values()]
