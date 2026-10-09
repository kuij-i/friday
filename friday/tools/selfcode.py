"""Gated self-coding tool (phase 2).

OFF unless FRIDAY_SELF_CODING=true. Even then it never touches your working
branch: it stages the proposed diff on a fresh `friday/self-improve-*` branch,
runs the test suite, and reports back. You review and merge by hand. The tool
is DANGEROUS, so it also passes through the approval gate.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..selfimprove.proposer import Proposal, SelfImprover
from .base import Risk, Tool, ToolResult

if TYPE_CHECKING:
    from ..config import Config


def make_selfcode_tools(config: Config) -> list[Tool]:
    improver = SelfImprover(config.repo_root)

    def propose_self_change(title: str, rationale: str, diff: str) -> ToolResult:
        if not diff or not diff.strip():
            return ToolResult("A proposal needs a unified diff.", is_error=True)
        report = improver.stage_proposal(
            Proposal(title=title or "untitled", rationale=rationale or "", diff=diff)
        )
        return ToolResult(json.dumps(report, indent=2), is_error=report.get("ok") != "true")

    return [
        Tool(
            name="propose_self_change",
            description=(
                "Propose a change to Friday's OWN source code as a unified diff. It is "
                "applied on a new git branch and tested — never merged automatically. "
                "The user reviews and merges. Use only for genuine self-improvements."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short title for the change."},
                    "rationale": {"type": "string", "description": "Why this change helps."},
                    "diff": {"type": "string", "description": "Unified diff applyable with `git apply`."},
                },
                "required": ["title", "diff"],
                "additionalProperties": False,
            },
            risk=Risk.DANGEROUS,
            handler=propose_self_change,
        )
    ]
