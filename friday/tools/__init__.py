"""Assemble the full tool registry from all tool modules."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..memory import Memory
from .base import Risk, Tool, ToolRegistry, ToolResult
from .files import make_file_tools
from .memory_tools import make_memory_tools
from .schedule import make_schedule_tools
from .security import make_security_tools
from .shell import make_exec_tools

if TYPE_CHECKING:
    from ..config import Config

__all__ = ["Risk", "Tool", "ToolRegistry", "ToolResult", "build_registry"]


def build_registry(config: Config, memory: Memory, channel: str = "cli") -> ToolRegistry:
    registry = ToolRegistry()
    tools = [
        *make_file_tools(config.workspace),
        *make_exec_tools(config.workspace),
        *make_memory_tools(memory),
        *make_security_tools(config.workspace),
        *make_schedule_tools(memory, channel),
    ]
    if getattr(config, "enable_self_coding", False):
        # Imported lazily so the heavy git/test path only loads when enabled.
        from .selfcode import make_selfcode_tools

        tools.extend(make_selfcode_tools(config))
    for tool in tools:
        registry.register(tool)
    return registry
