"""Local terminal chat with Friday, including interactive approval prompts."""
from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.status import Status

from ..agent import Agent
from ..config import Config
from ..memory import Memory
from ..tools.base import Risk

console = Console()

# The "thinking..." spinner, paused while an approval prompt is on screen.
_status: Status | None = None
_LOW_PREVIEW = 1500


def _approval_preview(tool_input: dict[str, Any], risk: Risk) -> str:
    # json.dumps escapes control characters, so terminal escape sequences in
    # the input can't rewrite what the user sees.
    preview = json.dumps(tool_input, indent=2, default=str)
    # DANGEROUS actions are always shown in full: approving a command you can
    # only partly see is not approval. LOW ones (e.g. file contents) may be long.
    if risk < Risk.DANGEROUS and len(preview) > _LOW_PREVIEW:
        hidden = len(preview) - _LOW_PREVIEW
        preview = preview[:_LOW_PREVIEW] + f"\n... ({hidden} more chars not shown)"
    return preview


def _cli_approver(name: str, tool_input: dict[str, Any], risk: Risk) -> tuple[bool, str]:
    label = {Risk.LOW: "yellow", Risk.DANGEROUS: "red"}.get(risk, "cyan")
    # Escape everything model-controlled: unescaped Rich markup such as
    # [conceal]...[/conceal] could hide part of a command from the prompt.
    body = f"[bold]{escape(name)}[/bold]\n\n{escape(_approval_preview(tool_input, risk))}"
    if _status is not None:
        _status.stop()
    try:
        console.print(
            Panel(body, title=f"[{label}]approval needed ({risk.name})[/{label}]", border_style=label)
        )
        answer = console.input("[bold]Allow? \\[y/N] [/bold]").strip().lower()
    finally:
        if _status is not None:
            _status.start()
    return (answer in {"y", "yes"}), "cli prompt"


def run_cli(config: Config) -> None:
    global _status
    config.ensure_dirs()
    problems = config.validate()
    if problems:
        for p in problems:
            console.print(f"[red]config error:[/red] {p}")
        return

    memory = Memory(config.db_path)
    agent = Agent(config, memory, channel="cli", approver=_cli_approver)

    console.print(
        Panel(
            "[bold cyan]Friday[/bold cyan] is online. Type your message.\n"
            "Commands: [bold]/exit[/bold], [bold]/reset[/bold] (clear this chat's history), "
            "[bold]/memory[/bold] (show what I remember).",
            border_style="cyan",
        )
    )

    try:
        while True:
            try:
                user = console.input("[bold green]you ›[/bold green] ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not user:
                continue
            if user in {"/exit", "/quit"}:
                break
            if user == "/reset":
                n = memory.clear_channel("cli")
                console.print(f"[dim]cleared {n} messages[/dim]")
                continue
            if user == "/memory":
                console.print(Panel(memory.context_blob(), title="memory", border_style="magenta"))
                continue

            try:
                with console.status("[cyan]thinking...[/cyan]") as status:
                    _status = status
                    reply = agent.send(user)
            except KeyboardInterrupt:
                console.print("[dim](interrupted)[/dim]")
                continue
            finally:
                _status = None
            console.print("[bold cyan]friday ›[/bold cyan]")
            try:
                console.print(Markdown(reply))
            except Exception:  # noqa: BLE001 — never let rendering crash the chat
                console.print(reply)
    finally:
        memory.close()
        console.print("[dim]Friday signing off.[/dim]")
