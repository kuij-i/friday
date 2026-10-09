"""Friday's core: a Claude-powered agentic tool-use loop."""
from __future__ import annotations

import json
from typing import Any, Callable

import anthropic

from .config import Config
from .memory import Memory
from .prompts import build_system_prompt
from .tools import Risk, ToolRegistry, build_registry
from .tools.base import Approver

# Optional callback to surface tool activity to a UI: (tool_name, tool_input).
ActivityHook = Callable[[str, dict[str, Any]], None]


def _auto_approver(_name: str, _input: dict[str, Any], _risk: Risk) -> tuple[bool, str]:
    return True, "auto-approved"


class Agent:
    """One Agent instance serves one logical conversation (a 'channel')."""

    def __init__(
        self,
        config: Config,
        memory: Memory,
        channel: str = "cli",
        approver: Approver | None = None,
        activity_hook: ActivityHook | None = None,
    ) -> None:
        self.config = config
        self.memory = memory
        self.channel = channel
        self.approver: Approver = approver or _auto_approver
        self.activity_hook = activity_hook
        self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        self.registry: ToolRegistry = build_registry(config.workspace, memory)

    # ── tool execution with the approval gate ──────────────────────────
    def _execute_tool(self, name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
        tool = self.registry.get(name)
        if tool is None:
            return f"Unknown tool: {name}", True

        if self.activity_hook:
            self.activity_hook(name, tool_input)

        # Gate anything with side effects unless auto-approve is on.
        needs_approval = tool.risk >= Risk.LOW and not self.config.auto_approve
        if tool.risk >= Risk.DANGEROUS:
            needs_approval = not self.config.auto_approve
        if needs_approval:
            approved, reason = self.approver(name, tool_input, tool.risk)
            if not approved:
                return f"User declined this action ({reason}). Do not retry; adapt.", True

        try:
            result = tool.handler(**tool_input)
            return result.content, result.is_error
        except TypeError as e:
            return f"Bad arguments for {name}: {e}", True
        except Exception as e:  # noqa: BLE001
            return f"Tool {name} crashed: {e}", True

    # ── the main turn ──────────────────────────────────────────────────
    def send(self, user_message: str) -> str:
        """Run one user turn to completion, returning Friday's final text."""
        self.memory.log_message(self.channel, "user", user_message)

        system = build_system_prompt(self.memory, self.channel)
        history = self.memory.recent_messages(self.channel, limit=20)
        # recent_messages already includes the just-logged user turn.
        messages: list[dict[str, Any]] = [
            {"role": m["role"], "content": m["content"]} for m in history
        ]
        # The API requires the first message to be from the user; a truncated
        # window could begin with an assistant turn, so drop any leading ones.
        while messages and messages[0]["role"] != "user":
            messages.pop(0)

        final_text = ""
        for _ in range(self.config.max_iterations):
            response = self.client.messages.create(
                model=self.config.model,
                max_tokens=16000,
                system=system,
                tools=self.registry.api_schemas(),
                thinking={"type": "adaptive"},
                output_config={"effort": self.config.effort},
                messages=messages,
            )

            if response.stop_reason == "refusal":
                detail = getattr(response, "stop_details", None)
                cat = getattr(detail, "category", None)
                final_text = (
                    "I can't help with that part"
                    + (f" (category: {cat})" if cat else "")
                    + ". Let me know if there's a defensive or authorized angle I can take instead."
                )
                break

            # Collect assistant text for this step.
            step_text = "".join(b.text for b in response.content if b.type == "text")

            if response.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": response.content})
                continue

            if response.stop_reason != "tool_use":
                final_text = step_text
                break

            # Execute tool calls, feed results back.
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    out, is_error = self._execute_tool(block.name, dict(block.input))
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": out,
                            "is_error": is_error,
                        }
                    )
            messages.append({"role": "user", "content": tool_results})
        else:
            final_text = (
                final_text
                or "I hit my step limit for this task. Tell me how you'd like me to continue."
            )

        self.memory.log_message(self.channel, "assistant", final_text)
        return final_text
