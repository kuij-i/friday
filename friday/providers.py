"""LLM providers: Anthropic (Claude) natively, or NVIDIA's hosted models.

The agent loop speaks the Anthropic Messages shape (content blocks, tool_use /
tool_result, stop_reason). `NvidiaClient` mimics the small part of the
Anthropic client the agent uses (`client.messages.create(...)`) and translates
to and from NVIDIA's OpenAI-compatible chat-completions API, so the agent,
tools, approval gate and memory work unchanged on either provider.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .config import NVIDIA_BASE_URL, NVIDIA_DEFAULT_MODEL

if TYPE_CHECKING:
    from .config import Config

# Hosted NVIDIA models cap output far below Claude's; ask for a size they accept.
_NVIDIA_MAX_TOKENS = 4096
# Some reasoning models put their chain of thought inline in <think> tags.
_THINK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


class ProviderError(Exception):
    """An API failure already phrased for the user."""


def make_client(config: Config) -> Any:
    if config.provider == "nvidia":
        return NvidiaClient(api_key=config.nvidia_api_key, base_url=config.nvidia_base_url)
    import anthropic

    return anthropic.Anthropic(api_key=config.anthropic_api_key)


# ── response objects shaped like the Anthropic SDK's ────────────────────
@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: Any
    type: str = "tool_use"


@dataclass
class StopDetails:
    category: str | None = None
    explanation: str = ""


@dataclass
class Response:
    content: list[Any] = field(default_factory=list)
    stop_reason: str = "end_turn"
    stop_details: StopDetails | None = None


_FINISH_TO_STOP = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}


def _block_value(block: Any, key: str, default: Any = None) -> Any:
    """Read a content block that may be a dict (history) or an object (a response)."""
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


def to_openai_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic-style messages -> OpenAI chat messages."""
    out: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
    for m in messages:
        role, content = m["role"], m["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        texts: list[str] = []
        calls: list[dict[str, Any]] = []
        for block in content:
            kind = _block_value(block, "type")
            if kind == "text":
                texts.append(_block_value(block, "text", ""))
            elif kind == "tool_use":
                calls.append({
                    "id": _block_value(block, "id"),
                    "type": "function",
                    "function": {
                        "name": _block_value(block, "name"),
                        "arguments": json.dumps(_block_value(block, "input") or {}),
                    },
                })
            elif kind == "tool_result":
                result = _block_value(block, "content", "")
                if _block_value(block, "is_error"):
                    result = f"ERROR: {result}"
                out.append({"role": "tool", "tool_call_id": _block_value(block, "tool_use_id"),
                            "content": str(result)})
        if role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": "\n".join(texts) or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        elif texts:
            out.append({"role": role, "content": "\n".join(texts)})
    return out


def to_openai_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Client tools -> OpenAI function tools. Anthropic server tools have no equivalent."""
    out = []
    for t in tools or []:
        if "input_schema" not in t:  # e.g. Claude's server-side web_search
            continue
        out.append({"type": "function", "function": {
            "name": t["name"], "description": t.get("description", ""), "parameters": t["input_schema"],
        }})
    return out


def from_openai_response(completion: Any) -> Response:
    choice = completion.choices[0]
    msg = choice.message
    blocks: list[Any] = []
    text = _THINK.sub("", msg.content or "").strip()
    if text:
        blocks.append(TextBlock(text))
    for call in msg.tool_calls or []:
        raw = call.function.arguments or "{}"
        try:
            args: Any = json.loads(raw)
        except json.JSONDecodeError:
            args = raw  # the agent reports a non-object input back to the model
        blocks.append(ToolUseBlock(id=call.id, name=call.function.name, input=args))
    stop = _FINISH_TO_STOP.get(choice.finish_reason or "stop", "end_turn")
    if stop != "tool_use" and any(b.type == "tool_use" for b in blocks):
        stop = "tool_use"  # some servers report "stop" alongside tool calls
    details = StopDetails(category="content_filter") if stop == "refusal" else None
    return Response(content=blocks, stop_reason=stop, stop_details=details)


class _Messages:
    def __init__(self, owner: NvidiaClient) -> None:
        self._owner = owner

    def create(self, *, model: str, max_tokens: int, system: str = "",
               messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
               **_ignored: Any) -> Response:
        # thinking / output_config are Claude-only and dropped here.
        return self._owner._complete(
            model=model,
            max_tokens=min(max_tokens, _NVIDIA_MAX_TOKENS),
            messages=to_openai_messages(system, messages),
            tools=to_openai_tools(tools),
        )


class NvidiaClient:
    """Drop-in for the part of `anthropic.Anthropic` the agent uses."""

    def __init__(self, api_key: str, base_url: str = NVIDIA_BASE_URL, client: Any | None = None) -> None:
        if client is None:
            import openai

            client = openai.OpenAI(api_key=api_key, base_url=base_url, max_retries=2, timeout=300)
        self._client = client
        self.messages = _Messages(self)

    def _complete(self, *, model: str, max_tokens: int, messages: list[dict[str, Any]],
                  tools: list[dict[str, Any]]) -> Response:
        import openai

        kwargs: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        try:
            return from_openai_response(self._client.chat.completions.create(**kwargs))
        except (openai.AuthenticationError, openai.PermissionDeniedError):
            # NVIDIA answers a bad or revoked key with 403, not 401.
            raise ProviderError("NVIDIA rejected the API key — check NVIDIA_API_KEY in your .env.") from None
        except openai.NotFoundError:
            raise ProviderError(
                f"NVIDIA doesn't serve the model '{model}' — pick one from build.nvidia.com "
                "and set FRIDAY_MODEL."
            ) from None
        except openai.RateLimitError:
            raise ProviderError("NVIDIA is rate-limiting requests right now. Give it a moment and retry.") from None
        except openai.BadRequestError as e:
            detail = str(getattr(e, "message", e))
            if tools and "tool" in detail.lower():
                raise ProviderError(
                    f"The model '{model}' doesn't support tool calling on NVIDIA. Set FRIDAY_MODEL to "
                    f"one that does (e.g. {NVIDIA_DEFAULT_MODEL}). Details: {detail}"
                ) from None
            raise ProviderError(f"NVIDIA API error: {detail}") from None
        except openai.APIConnectionError:
            raise ProviderError("I couldn't reach the NVIDIA API — check the network and try again.") from None
        except openai.APIStatusError as e:
            if e.status_code >= 500:
                raise ProviderError("The NVIDIA API had a server error. Try again shortly.") from None
            raise ProviderError(f"NVIDIA API error ({e.status_code}): {e.message}") from None

