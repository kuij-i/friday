"""Local terminal chat with Friday, including interactive approval prompts."""
from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from ..agent import Agent
from ..config import Config
from ..memory import Memory
from ..tools.base import Risk

console = Console()


def _cli_approver(name: str, tool_input: dict[str, Any], risk: Risk) -> tuple[bool, str]:
    label = {Risk.LOW: "yellow", Risk.DANGEROUS: "red"}.get(risk, "cyan")
    preview = json.dumps(tool_input, indent=2)
    if len(preview) > 1500:
        preview = preview[:1500] + "\n..."
    console.print(
        Panel(
            f"[bold]{name}[/bold]\n\n{preview}",
            title=f"[{label}]approval needed ({risk.name})[/{label}]",
            border_style=label,
        )
    )
    answer = console.input("[bold]Allow? [y/N] [/bold]").strip().lower()
    return (answer in {"y", "yes"}), "cli prompt"


def run_cli(config: Config) -> None:
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

            with console.status("[cyan]thinking...[/cyan]"):
                reply = agent.send(user)
            console.print("[bold cyan]friday ›[/bold cyan]")
            console.print(Markdown(reply))
    finally:
        memory.close()
        console.print("[dim]Friday signing off.[/dim]")
