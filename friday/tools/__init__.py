"""Assemble the full tool registry from all tool modules."""
from __future__ import annotations

from pathlib import Path

from ..memory import Memory
from .base import Risk, Tool, ToolRegistry, ToolResult
from .files import make_file_tools
from .memory_tools import make_memory_tools
from .security import make_security_tools
from .shell import make_exec_tools

__all__ = ["Risk", "Tool", "ToolRegistry", "ToolResult", "build_registry"]


def build_registry(workspace: Path, memory: Memory) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        *make_file_tools(workspace),
        *make_exec_tools(workspace),
        *make_memory_tools(memory),
        *make_security_tools(workspace),
    ):
        registry.register(tool)
    return registry
