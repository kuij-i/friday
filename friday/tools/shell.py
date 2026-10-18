"""Shell and Python execution tools. Both are DANGEROUS and always gated."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from .base import Risk, Tool, ToolResult

_MAX_STDOUT = 20_000
_MAX_STDERR = 10_000


def _tail(f, limit: int) -> str:
    """Read at most the last `limit` bytes of a spooled output file."""
    f.flush()
    size = f.seek(0, os.SEEK_END)
    f.seek(max(0, size - limit))
    return f.read().decode("utf-8", errors="replace")


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the process and everything it spawned (shell=True forks children)."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001 — already gone, or no permission; fall back
        pass
    try:
        proc.kill()
    except OSError:
        pass


def _run(args: str | list[str], *, shell: bool, cwd: Path, timeout: int) -> tuple[int | None, str, str]:
    """Run a process with no stdin, bounded output, and a hard timeout.

    Output goes to temp files rather than pipes: memory stays bounded however
    much a command prints, and a backgrounded grandchild holding the pipe open
    can't make us hang after the timeout. Returns (returncode | None on
    timeout, stdout tail, stderr tail).
    """
    popen_kwargs: dict = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    with tempfile.TemporaryFile() as out_f, tempfile.TemporaryFile() as err_f:
        proc = subprocess.Popen(
            args,
            shell=shell,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,  # never let a command grab the terminal
            stdout=out_f,
            stderr=err_f,
            **popen_kwargs,
        )
        try:
            rc: int | None = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            rc = None
        return rc, _tail(out_f, _MAX_STDOUT), _tail(err_f, _MAX_STDERR)


def _format(rc: int, out: str, err: str) -> ToolResult:
    body = f"exit code: {rc}\n"
    if out:
        body += f"\n--- stdout ---\n{out}"
    if err:
        body += f"\n--- stderr ---\n{err}"
    return ToolResult(body, is_error=rc != 0)


def make_exec_tools(workspace: Path, timeout: int = 120) -> list[Tool]:
    def run_shell(command: str) -> ToolResult:
        if not isinstance(command, str) or not command.strip():
            return ToolResult("command must be a non-empty string.", is_error=True)
        try:
            rc, out, err = _run(command, shell=True, cwd=workspace, timeout=timeout)
            if rc is None:
                return ToolResult(f"Command timed out after {timeout}s (process tree killed).\n{out}{err}",
                                  is_error=True)
            return _format(rc, out, err)
        except Exception as e:  # noqa: BLE001
            return ToolResult(f"run_shell error: {e}", is_error=True)

    def run_python(code: str) -> ToolResult:
        if not isinstance(code, str) or not code.strip():
            return ToolResult("code must be a non-empty string.", is_error=True)
        script = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".py", dir=str(workspace), delete=False, encoding="utf-8"
            ) as f:
                f.write(code)
                script = f.name
            rc, out, err = _run([sys.executable, "-I", script], shell=False, cwd=workspace, timeout=timeout)
            if rc is None:
                return ToolResult(f"Python run timed out after {timeout}s (process tree killed).\n{out}{err}",
                                  is_error=True)
            return _format(rc, out, err)
        except Exception as e:  # noqa: BLE001
            return ToolResult(f"run_python error: {e}", is_error=True)
        finally:
            if script:
                Path(script).unlink(missing_ok=True)

    return [
        Tool(
            name="run_shell",
            description=(
                "Run a shell command on the host (cwd = workspace). Use for builds, git, "
                "package installs, and system tasks. Requires user approval. There is no "
                "stdin: interactive prompts get EOF, so pass non-interactive flags."
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
