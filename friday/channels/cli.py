"""Local terminal chat with Friday, including interactive approval prompts."""
from __future__ import annotations

import json
import time
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.status import Status
from rich.text import Text

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


_HELP = (
    "Commands: [bold]/help[/bold], [bold]/exit[/bold], "
    "[bold]/reset[/bold] (clear this chat's history), "
    "[bold]/memory[/bold] (show what I remember), "
    "[bold]/reminders[/bold] (list scheduled reminders)."
)


def _show_activity(name: str, tool_input: dict[str, Any]) -> None:
    """Show each tool call as it happens, so read-only tools aren't invisible."""
    summary = json.dumps(tool_input, default=str)
    if len(summary) > 100:
        summary = summary[:100] + "…"
    console.print(Text(f"  → {name} {summary}", style="dim"))


def run_cli(config: Config) -> int:
    global _status
    problems = config.validate()
    if problems:
        for p in problems:
            console.print(Text.assemble(("config error: ", "red"), p))
        console.print("Run [bold]python run.py doctor[/bold] after fixing your .env.")
        return 1
    try:
        config.ensure_dirs()
        memory = Memory(config.db_path)
    except Exception as e:  # noqa: BLE001 — unwritable dir, corrupt DB, ...
        console.print(Text.assemble(("startup error: ", "red"), f"can't open {config.db_path}: {e}"))
        return 1
    agent = Agent(config, memory, channel="cli", approver=_cli_approver, activity_hook=_show_activity)

    console.print(
        Panel(
            "[bold cyan]Friday[/bold cyan] is online. Type your message.\n" + _HELP,
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
            if user.startswith("/"):
                _command(user, memory)
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
                console.print(reply, markup=False)
    finally:
        memory.close()
        console.print("[dim]Friday signing off.[/dim]")
    return 0


def _command(cmd: str, memory: Memory) -> None:
    """Handle a /command. Stored text is shown as plain Text, never as Rich markup."""
    name = cmd.split()[0].lower()
    try:
        if name == "/reset":
            n = memory.clear_channel("cli")
            console.print(f"[dim]cleared {n} messages[/dim]")
        elif name == "/memory":
            console.print(Panel(Text(memory.context_blob()), title="memory", border_style="magenta"))
        elif name == "/reminders":
            items = memory.list_reminders()
            if not items:
                console.print("[dim]No active reminders.[/dim]")
            for r in items:
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["next_run"]))
                console.print(Text(f"#{r['id']}  {when}  [{r['channel']}]  {r['text']}"))
        elif name == "/help":
            console.print(_HELP)
        else:
            console.print(Text(f"Unknown command {name}. ", style="yellow") + Text.from_markup(_HELP))
    except Exception as e:  # noqa: BLE001 — a command must never crash the chat
        console.print(Text(f"command failed: {e}", style="red"))
