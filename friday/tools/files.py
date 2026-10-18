"""File tools, scoped to the configured workspace directory."""
from __future__ import annotations

from pathlib import Path

from .base import Risk, Tool, ToolResult

_MAX_WRITE = 5_000_000  # 5 MB cap on a single write


def _resolve(workspace: Path, rel: str) -> Path:
    """Resolve a path and refuse to escape the workspace sandbox."""
    if rel is None or str(rel).strip() == "":
        raise ValueError("empty path")
    target = (workspace / str(rel)).resolve()
    if workspace not in target.parents and target != workspace:
        raise ValueError(f"path '{rel}' is outside the workspace sandbox")
    return target


def make_file_tools(workspace: Path) -> list[Tool]:
    def read_file(path: str) -> ToolResult:
        try:
            target = _resolve(workspace, path)
            if not target.is_file():
                return ToolResult(f"No such file: {path}", is_error=True)
            raw = target.read_bytes()
            if b"\x00" in raw[:4096]:
                return ToolResult(
                    f"'{path}' looks like a binary file; not reading it as text.", is_error=True
                )
            text = raw.decode("utf-8", errors="replace")
            if len(text) > 60_000:
                text = text[:60_000] + "\n... [truncated]"
            return ToolResult(text)
        except Exception as e:  # noqa: BLE001
            return ToolResult(f"read_file error: {e}", is_error=True)

    def write_file(path: str, content: str) -> ToolResult:
        try:
            if content is None:
                content = ""
            if len(content) > _MAX_WRITE:
                return ToolResult(
                    f"Refusing to write {len(content)} bytes (cap {_MAX_WRITE}).", is_error=True
                )
            target = _resolve(workspace, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return ToolResult(f"Wrote {len(content)} chars to {path}")
        except Exception as e:  # noqa: BLE001
            return ToolResult(f"write_file error: {e}", is_error=True)

    def list_dir(path: str = ".") -> ToolResult:
        try:
            target = _resolve(workspace, path)
            if not target.is_dir():
                return ToolResult(f"Not a directory: {path}", is_error=True)
            entries = []
            for p in sorted(target.iterdir()):
                kind = "dir " if p.is_dir() else "file"
                size = p.stat().st_size if p.is_file() else ""
                entries.append(f"{kind}  {p.name}  {size}")
            return ToolResult("\n".join(entries) or "(empty)")
        except Exception as e:  # noqa: BLE001
            return ToolResult(f"list_dir error: {e}", is_error=True)

    return [
        Tool(
            name="read_file",
            description="Read a UTF-8 text file from the workspace. Paths are relative to the workspace root.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Path relative to the workspace."}},
                "required": ["path"],
                "additionalProperties": False,
            },
            risk=Risk.SAFE,
            handler=read_file,
        ),
        Tool(
            name="write_file",
            description="Create or overwrite a text file inside the workspace. Use for code, notes, configs.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the workspace."},
                    "content": {"type": "string", "description": "Full file contents to write."},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            risk=Risk.LOW,
            handler=write_file,
        ),
        Tool(
            name="list_dir",
            description="List files and folders in a workspace directory.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Directory relative to workspace. Default '.'"}},
                "required": [],
                "additionalProperties": False,
            },
            risk=Risk.SAFE,
            handler=list_dir,
        ),
    ]
