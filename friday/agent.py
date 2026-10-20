"""Friday's core: a Claude-powered agentic tool-use loop.

Robustness choices:
  * API errors become friendly messages instead of crashing the channel; the
    SDK already retries transient failures (429/5xx/network) with backoff.
  * `max_tokens` truncation is continued rather than silently cut off.
  * Tool inputs are coerced to dicts and validated against required keys
    before dispatch, so a malformed tool call can't take down the turn.
  * Every path logs a final assistant message, so history stays consistent.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import anthropic

from .config import Config
from .memory import Memory
from .prompts import build_system_prompt
from .providers import ProviderError, make_client
from .tools import Risk, ToolRegistry, build_registry
from .tools.base import Approver

# Optional callback to surface tool activity to a UI: (tool_name, tool_input).
ActivityHook = Callable[[str, dict[str, Any]], None]

log = logging.getLogger("friday.agent")

_MAX_TOKENS = 16_000
# Budgets that keep a request inside the context window no matter what was
# stored earlier: one oversized message or tool result must not wedge a chat.
_HISTORY_CHAR_BUDGET = 150_000
_MAX_TOOL_RESULT = 100_000


class Agent:
    """One Agent instance serves one logical conversation (a 'channel')."""

    def __init__(
        self,
        config: Config,
        memory: Memory,
        channel: str = "cli",
        approver: Approver | None = None,
        activity_hook: ActivityHook | None = None,
        client: Any | None = None,
    ) -> None:
        self.config = config
        self.memory = memory
        self.channel = channel
        # Fail closed: without an approver, gated tools are denied. Only
        # FRIDAY_AUTO_APPROVE (checked in _execute_tool) skips the gate.
        self.approver: Approver = approver or self._deny_without_approver
        self._warned_no_approver = False
        self.activity_hook = activity_hook
        # `client` injection point keeps the agent unit-testable without network.
        self.client = client or make_client(config)
        self.registry: ToolRegistry = build_registry(config, memory, channel)

    def _deny_without_approver(self, name: str, _input: dict[str, Any], _risk: Risk) -> tuple[bool, str]:
        if not self._warned_no_approver:
            self._warned_no_approver = True
            log.warning("channel %r has no approver; denying gated tool %s", self.channel, name)
        return False, "no approver configured for this channel"

    def _tool_params(self) -> list[dict[str, Any]]:
        """Client tools (registry) plus any enabled server-side tools."""
        tools: list[dict[str, Any]] = list(self.registry.api_schemas())
        if self.config.web_search_available:
            tools.append(
                {
                    "type": "web_search_20260209",
                    "name": "web_search",
                    "max_uses": self.config.web_search_max_uses,
                }
            )
        return tools

    # ── tool execution with the approval gate ──────────────────────────
    def _execute_tool(self, name: str, tool_input: Any) -> tuple[str, bool]:
        tool = self.registry.get(name)
        if tool is None:
            return f"Unknown tool: {name}", True

        # Defensive: tool input must be a JSON object. Never trust its shape.
        if not isinstance(tool_input, dict):
            return f"Tool {name} received non-object input; ignoring.", True

        # Validate required parameters up front for a clear error message.
        required = tool.input_schema.get("required", []) if isinstance(tool.input_schema, dict) else []
        missing = [k for k in required if k not in tool_input]
        if missing:
            return f"Tool {name} missing required argument(s): {', '.join(missing)}", True

        if self.activity_hook:
            try:
                self.activity_hook(name, tool_input)
            except Exception:  # noqa: BLE001 — a UI hook must never break a turn
                pass

        # Gate anything with side effects unless auto-approve is on.
        if tool.risk >= Risk.LOW and not self.config.auto_approve:
            try:
                approved, reason = self.approver(name, tool_input, tool.risk)
            except Exception as e:  # noqa: BLE001 — treat approver failure as denial
                return f"Approval check failed ({e}); action not taken.", True
            if not approved:
                return f"User declined this action ({reason}). Do not retry; adapt.", True

        try:
            result = tool.handler(**tool_input)
            content = result.content if isinstance(result.content, str) else str(result.content)
            if len(content) > _MAX_TOOL_RESULT:
                content = content[:_MAX_TOOL_RESULT] + "\n... [tool output truncated]"
            return content or "(no output)", bool(result.is_error)
        except TypeError as e:
            return f"Bad arguments for {name}: {e}", True
        except Exception as e:  # noqa: BLE001
            return f"Tool {name} crashed: {e}", True

    def _create(self, system: str, messages: list[dict[str, Any]]):
        return self.client.messages.create(
            model=self.config.model,
            max_tokens=_MAX_TOKENS,
            system=system,
            tools=self._tool_params(),
            thinking={"type": "adaptive"},
            output_config={"effort": self.config.effort},
            messages=messages,
        )

    # ── the main turn ──────────────────────────────────────────────────
    def send(self, user_message: str) -> str:
        """Run one user turn to completion, returning Friday's final text.

        Never raises on an API/tool/storage failure — returns a readable
        message so the calling channel can always reply to the user.
        """
        user_message = str(user_message or "").strip()
        if not user_message:
            return "I didn't catch a message there — try again?"

        final_text = ""
        try:
            self.memory.log_message(self.channel, "user", user_message)
            system = build_system_prompt(self.memory, self.channel, web_search=self.config.web_search_available)
            messages = self._history_messages(user_message)
            final_text = self._run_loop(system, messages)
        except anthropic.APIStatusError as e:
            final_text = self._api_error_text(e)
        except ProviderError as e:
            final_text = str(e)
        except anthropic.APIConnectionError:
            final_text = "I couldn't reach the Claude API — check the network and try again."
        except Exception as e:  # noqa: BLE001 — last-resort guard
            log.exception("agent turn failed")
            final_text = f"Something went wrong handling that: {e}"

        final_text = final_text or "(no response)"
        try:
            self.memory.log_message(self.channel, "assistant", final_text)
        except Exception:  # noqa: BLE001 — still deliver the reply if logging fails
            log.exception("failed to log assistant message")
        return final_text

    def _history_messages(self, user_message: str) -> list[dict[str, Any]]:
        """Recent history as API messages, bounded to a character budget."""
        history = self.memory.recent_messages(self.channel, limit=20)
        messages: list[dict[str, Any]] = []
        used = 0
        # Walk newest → oldest so the budget keeps the most recent context.
        for m in reversed(history):
            content = m["content"] or ""
            if m["role"] not in {"user", "assistant"} or not content.strip():
                continue
            room = _HISTORY_CHAR_BUDGET - used
            if room <= 0:
                break
            if len(content) > room:
                if messages:  # older message doesn't fit; stop here
                    break
                content = content[-room:]  # newest message alone is huge: keep its tail
            messages.append({"role": m["role"], "content": content})
            used += len(content)
        messages.reverse()
        # The API requires the first message to be from the user; a truncated
        # window could begin with an assistant turn, so drop any leading ones.
        while messages and messages[0]["role"] != "user":
            messages.pop(0)
        if not messages:  # history somehow empty — fall back to this turn
            messages = [{"role": "user", "content": user_message[-_HISTORY_CHAR_BUDGET:]}]
        return messages

    def _run_loop(self, system: str, messages: list[dict[str, Any]]) -> str:
        final_text = ""
        max_token_continues = 0
        for _ in range(self.config.max_iterations):
            response = self._create(system, messages)
            step_text = "".join(b.text for b in response.content if b.type == "text")
            reason = response.stop_reason

            if reason == "refusal":
                detail = getattr(response, "stop_details", None)
                cat = getattr(detail, "category", None)
                return (
                    "I can't help with that part"
                    + (f" (category: {cat})" if cat else "")
                    + ". Tell me if there's a defensive or authorized angle and I'll take it."
                )

            if reason == "pause_turn":
                messages.append({"role": "assistant", "content": response.content})
                continue

            if reason == "max_tokens":
                # Continue the truncated answer a bounded number of times.
                # Append only the completed text so a half-emitted tool_use block
                # can't dangle without a matching tool_result on the next call.
                if step_text:
                    messages.append({"role": "assistant", "content": step_text})
                if max_token_continues >= 3 or not step_text:
                    return (step_text or final_text or "") + "\n\n[response truncated]"
                max_token_continues += 1
                messages.append({"role": "user", "content": "Continue."})
                final_text = step_text or final_text
                continue

            if reason != "tool_use":
                return step_text or final_text or "(no response)"

            # Execute client tool calls, feed results back. Server tools (e.g.
            # web_search) run on Anthropic's side and need no local handling.
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    raw_input = block.input
                    try:
                        parsed = dict(raw_input) if raw_input is not None else {}
                    except (TypeError, ValueError):
                        parsed = raw_input
                    out, is_error = self._execute_tool(block.name, parsed)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": out,
                            "is_error": is_error,
                        }
                    )
            if not tool_results:
                # Only server-side tools ran; nothing to return locally.
                return step_text or final_text or "(no response)"
            messages.append({"role": "user", "content": tool_results})
            final_text = step_text or final_text

        return final_text or "I hit my step limit for this task. Tell me how to continue."

    @staticmethod
    def _api_error_text(e: anthropic.APIStatusError) -> str:
        code = getattr(e, "status_code", None)
        if code == 401:
            return "My Claude API key was rejected — check ANTHROPIC_API_KEY in your .env."
        if code == 429:
            return "I'm being rate-limited by the API right now. Give it a moment and retry."
        if code and code >= 500:
            return "The Claude API had a server error. Try again shortly."
        return f"API error ({code}): {getattr(e, 'message', str(e))}"
