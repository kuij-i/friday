"""Phase 2 (scaffold): guarded self-coding.

The design, deliberately conservative:

  1. Friday proposes a change to its OWN source as a unified diff written to a
     scratch file — it never edits its live code directly.
  2. The change is applied on a dedicated git branch, never on your main branch.
  3. You review the diff and the test results, then approve a merge by hand.

Nothing here runs automatically. This module exposes the primitives; wiring it
to the agent as a tool is a conscious next step you take once you trust Friday,
not something that happens on day one. That is what "guarded" means.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Proposal:
    title: str
    rationale: str
    diff: str  # unified diff, applyable with `git apply`


class SelfImprover:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
        )

    def stage_proposal(self, proposal: Proposal) -> dict[str, str]:
        """Create a branch, apply the proposed diff, and run tests.

        Returns a report dict. Does NOT merge. Leaves you on the new branch with
        the change applied so you can inspect `git diff main` and decide.
        """
        branch = f"friday/self-improve-{int(time.time())}"
        created = self._git("checkout", "-b", branch)
        if created.returncode != 0:
            return {"ok": "false", "stage": "branch", "error": created.stderr}

        patch = self.repo_root / ".friday_proposal.patch"
        patch.write_text(proposal.diff, encoding="utf-8")
        applied = self._git("apply", "--index", str(patch))
        patch.unlink(missing_ok=True)
        if applied.returncode != 0:
            return {"ok": "false", "stage": "apply", "branch": branch, "error": applied.stderr}

        tests = subprocess.run(
            ["python", "-m", "pytest", "-q"],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
        )
        return {
            "ok": "true" if tests.returncode == 0 else "false",
            "stage": "tested",
            "branch": branch,
            "title": proposal.title,
            "rationale": proposal.rationale,
            "test_output": (tests.stdout + tests.stderr)[-4000:],
            "next_step": (
                f"Review with: git diff main..{branch}\n"
                f"Approve with: git checkout main && git merge {branch}\n"
                f"Reject with:  git checkout main && git branch -D {branch}"
            ),
        }
