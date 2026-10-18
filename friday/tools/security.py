"""Defensive security tools.

Scope is deliberately limited to inspecting the LOCAL machine you run Friday
on: inventory, listening ports, file hashing, and basic hardening checks.
Anything that would reach out and probe another host is intentionally left to
the general `run_shell` tool, which is gated behind your approval — so you
stay in control of what Friday points at the network, and only against
systems you own or are authorized to test.
"""
from __future__ import annotations

import hashlib
import platform
import socket
from pathlib import Path

from .base import Risk, Tool, ToolResult

try:
    import psutil
except ImportError:  # psutil is optional; tools degrade gracefully
    psutil = None  # type: ignore


def make_security_tools(workspace: Path) -> list[Tool]:
    def system_info() -> ToolResult:
        info = {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "machine": platform.machine(),
        }
        if psutil:
            info["cpu_percent"] = psutil.cpu_percent(interval=0.3)
            vm = psutil.virtual_memory()
            info["mem_used_pct"] = vm.percent
            info["boot_time"] = psutil.boot_time()
        body = "\n".join(f"{k}: {v}" for k, v in info.items())
        return ToolResult(body)

    def listening_ports() -> ToolResult:
        if not psutil:
            return ToolResult("psutil not installed; run: pip install psutil", is_error=True)
        try:
            rows = []
            for c in psutil.net_connections(kind="inet"):
                if c.status == psutil.CONN_LISTEN:
                    laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "?"
                    proc = ""
                    if c.pid:
                        try:
                            proc = psutil.Process(c.pid).name()
                        except Exception:  # noqa: BLE001
                            proc = f"pid {c.pid}"
                    rows.append(f"{laddr:<24} {proc}")
            return ToolResult("LISTENING:\n" + ("\n".join(sorted(set(rows))) or "(none)"))
        except (psutil.AccessDenied, PermissionError):
            return ToolResult(
                "Access denied enumerating connections. Run Friday as administrator "
                "for full port visibility.",
                is_error=True,
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(f"listening_ports error: {e}", is_error=True)

    def hash_file(path: str, algorithm: str = "sha256") -> ToolResult:
        try:
            target = (workspace / path).resolve()
            if not target.is_file():
                return ToolResult(f"No such file: {path}", is_error=True)
            if algorithm not in hashlib.algorithms_available:
                return ToolResult(f"Unknown algorithm: {algorithm}", is_error=True)
            h = hashlib.new(algorithm)
            with target.open("rb") as f:
                for chunk in iter(lambda: f.read(1 << 16), b""):
                    h.update(chunk)
            return ToolResult(f"{algorithm}({path}) = {h.hexdigest()}")
        except Exception as e:  # noqa: BLE001
            return ToolResult(f"hash_file error: {e}", is_error=True)

    return [
        Tool(
            name="system_info",
            description="Report OS, host, CPU/memory load and Python version of the local machine.",
            input_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            risk=Risk.SAFE,
            handler=system_info,
        ),
        Tool(
            name="listening_ports",
            description="List local listening TCP/UDP ports and the owning process — a quick attack-surface check of your own machine.",
            input_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            risk=Risk.SAFE,
            handler=listening_ports,
        ),
        Tool(
            name="hash_file",
            description="Compute a cryptographic hash of a workspace file (integrity / IOC checks).",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "algorithm": {"type": "string", "description": "e.g. sha256 (default), sha1, md5."},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            risk=Risk.SAFE,
            handler=hash_file,
        ),
    ]
