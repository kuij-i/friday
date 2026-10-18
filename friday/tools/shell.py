"""Shell and Python execution tools. Both are DANGEROUS and always gated."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from .base import Risk, Tool, ToolResult


def make_exec_tools(workspace: Path, timeout: int = 120) -> list[Tool]:
    def run_shell(command: str) -> ToolResult:
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            out = (proc.stdout or "")[-20_000:]
            err = (proc.stderr or "")[-10_000:]
            body = f"exit code: {proc.returncode}\n"
            if out:
                body += f"\n--- stdout ---\n{out}"
            if err:
                body += f"\n--- stderr ---\n{err}"
            return ToolResult(body, is_error=proc.returncode != 0)
        except subprocess.TimeoutExpired:
            return ToolResult(f"Command timed out after {timeout}s", is_error=True)
        except Exception as e:  # noqa: BLE001
            return ToolResult(f"run_shell error: {e}", is_error=True)

    def run_python(code: str) -> ToolResult:
        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".py", dir=str(workspace), delete=False, encoding="utf-8"
            ) as f:
                f.write(code)
                script = f.name
            try:
                proc = subprocess.run(
                    [sys.executable, "-I", script],
                    cwd=str(workspace),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            finally:
                Path(script).unlink(missing_ok=True)
            out = (proc.stdout or "")[-20_000:]
            err = (proc.stderr or "")[-10_000:]
            body = f"exit code: {proc.returncode}\n"
            if out:
                body += f"\n--- stdout ---\n{out}"
            if err:
                body += f"\n--- stderr ---\n{err}"
            return ToolResult(body, is_error=proc.returncode != 0)
        except subprocess.TimeoutExpired:
            return ToolResult(f"Python run timed out after {timeout}s", is_error=True)
        except Exception as e:  # noqa: BLE001
            return ToolResult(f"run_python error: {e}", is_error=True)

    return [
        Tool(
            name="run_shell",
            description=(
                "Run a shell command on the host (cwd = workspace). Use for builds, git, "
                "package installs, and system tasks. Requires user approval."
            ),
            input_schema={
                "type": "object",
                "properties": {"command": {"type": "string", "description": "The shell command to run."}},
                "required": ["command"],
                "additionalProperties": False,
            },
            risk=Risk.DANGEROUS,
            handler=run_shell,
        ),
        Tool(
            name="run_python",
            description=(
                "Execute a Python snippet in a fresh interpreter (cwd = workspace). Use for "
                "quick computation, data work, or scripting. Requires user approval."
            ),
            input_schema={
                "type": "object",
                "properties": {"code": {"type": "string", "description": "Python source to execute."}},
                "required": ["code"],
                "additionalProperties": False,
            },
            risk=Risk.DANGEROUS,
            handler=run_python,
        ),
    ]
