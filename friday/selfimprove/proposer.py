"""Phase 2: guarded self-coding.

The design, deliberately conservative:

  1. Friday proposes a change to its OWN source as a unified diff — it never
     edits its live code directly.
  2. The change is applied and committed on a dedicated git branch inside a
     temporary `git worktree`, so your checkout (and the code Friday is
     running from) is never switched, modified, or staged.
  3. The test suite runs in that worktree. The worktree is then removed; only
     the branch remains, for you to review and merge (or delete) by hand.

Nothing here merges. That is what "guarded" means.
"""
from __future__ import annotations

import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

_MAX_DIFF = 1_000_000  # 1 MB; a self-change bigger than this is not a "proposal"


@dataclass
class Proposal:
    title: str
    rationale: str
    diff: str  # unified diff, applyable with `git apply`


class SelfImprover:
    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root)

    def _git(self, *args: str, cwd: Path | None = None, stdin: str | None = None) -> subprocess.CompletedProcess:
        # Bytes, not text mode: on Windows text mode rewrites "\n" to "\r\n" on
        # stdin, so a patch no longer matches LF files and `git apply` fails.
        # core.autocrlf=false keeps the worktree byte-identical to the repo for
        # the same reason (Git for Windows defaults it to true).
        argv = ["git", "-c", "core.autocrlf=false", *args]
        try:
            proc = subprocess.run(
                argv,
                cwd=str(cwd or self.repo_root),
                input=stdin.encode("utf-8") if stdin is not None else None,
                stdin=None if stdin is not None else subprocess.DEVNULL,
                capture_output=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            return subprocess.CompletedProcess(argv, 1, "", str(e))
        return subprocess.CompletedProcess(
            argv,
            proc.returncode,
            proc.stdout.decode("utf-8", errors="replace"),
            proc.stderr.decode("utf-8", errors="replace"),
        )

    def stage_proposal(self, proposal: Proposal) -> dict[str, str]:
        """Commit the proposed diff on a new branch (in a temp worktree) and test it.

        Returns a report dict. Does NOT merge and does NOT touch the current
        checkout; review with `git diff <base>..<branch>`.
        """
        inside = self._git("rev-parse", "--is-inside-work-tree")
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return {"ok": "false", "stage": "precheck",
                    "error": "repo_root is not a git working tree"}
        if not proposal.diff or not proposal.diff.strip():
            return {"ok": "false", "stage": "precheck", "error": "empty diff"}
        if len(proposal.diff) > _MAX_DIFF:
            return {"ok": "false", "stage": "precheck", "error": f"diff larger than {_MAX_DIFF} bytes"}

        head = self._git("rev-parse", "HEAD")
        if head.returncode != 0:
            return {"ok": "false", "stage": "precheck", "error": "repository has no commits yet"}
        base_commit = head.stdout.strip()
        base_ref = self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        base = base_ref if base_ref and base_ref != "HEAD" else base_commit[:12]

        branch = f"friday/self-improve-{int(time.time())}-{secrets.token_hex(3)}"
        tmp = Path(tempfile.mkdtemp(prefix="friday-proposal-"))
        worktree = tmp / "wt"
        added = self._git("worktree", "add", "-q", "-b", branch, str(worktree), base_commit)
        if added.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            return {"ok": "false", "stage": "branch", "error": added.stderr}

        try:
            return self._apply_and_test(proposal, branch, base, worktree)
        finally:
            self._git("worktree", "remove", "--force", str(worktree))
            shutil.rmtree(tmp, ignore_errors=True)
            self._git("worktree", "prune")

    def _apply_and_test(self, proposal: Proposal, branch: str, base: str, worktree: Path) -> dict[str, str]:
        def fail(stage: str, error: str) -> dict[str, str]:
            # Nothing useful was committed; don't leave an empty branch behind.
            self._git("worktree", "remove", "--force", str(worktree))
            self._git("branch", "-D", branch)
            return {"ok": "false", "stage": stage, "error": error}

        # Patch goes in via stdin: nothing is written into anyone's checkout.
        applied = self._git("apply", "--index", "--whitespace=nowarn", "-", cwd=worktree, stdin=proposal.diff)
        if applied.returncode != 0:
            return fail("apply", applied.stderr or "git apply failed")

        message = f"{proposal.title.strip() or 'untitled'}\n\n{proposal.rationale.strip()}\n\nProposed by Friday."
        committed = self._git(
            "-c", "user.name=Friday", "-c", "user.email=friday@localhost",
            "commit", "-q", "--no-verify", "-F", "-",
            cwd=worktree, stdin=message,
        )
        if committed.returncode != 0:
            return fail("commit", committed.stderr or committed.stdout or "git commit failed")

        try:
            tests = subprocess.run(
                [sys.executable, "-m", "pytest", "-q"],
                cwd=str(worktree),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
            )
            test_rc = tests.returncode
            test_out = (tests.stdout + tests.stderr)[-4000:]
        except subprocess.TimeoutExpired:
            test_rc, test_out = 1, "pytest timed out after 600s"
        except OSError:
            test_rc, test_out = 1, "pytest not available"
        return {
            "ok": "true" if test_rc == 0 else "false",
            "stage": "tested",
            "branch": branch,
            "base": base,
            "title": proposal.title,
            "rationale": proposal.rationale,
            "test_output": test_out,
            "next_step": (
                f"Review with: git diff {base}..{branch}\n"
                f"Approve with: git checkout {base} && git merge {branch}\n"
                f"Reject with:  git branch -D {branch}"
            ),
        }
