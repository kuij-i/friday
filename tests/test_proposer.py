import shutil
import subprocess
import textwrap

import pytest

from friday.selfimprove.proposer import Proposal, SelfImprover

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _out(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _init_repo(repo):
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "checkout", "-q", "-b", "main")
    (repo / "hello.py").write_text("def greet():\n    return 'hi'\n")
    (repo / "test_hello.py").write_text(
        "from hello import greet\n\n\ndef test_greet():\n    assert greet() == 'hi'\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")


def test_stage_proposal_applies_and_tests(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)

    # A valid unified diff that keeps the test passing.
    diff = textwrap.dedent(
        """\
        diff --git a/hello.py b/hello.py
        index 0000000..1111111 100644
        --- a/hello.py
        +++ b/hello.py
        @@ -1,2 +1,2 @@
         def greet():
        -    return 'hi'
        +    return 'hi'  # tweaked
        """
    )
    report = SelfImprover(repo).stage_proposal(Proposal("tweak", "because", diff))
    assert report["ok"] == "true", report
    branch = report["branch"]
    assert branch.startswith("friday/self-improve-")
    # The change is committed on the branch...
    shown = subprocess.run(["git", "show", f"{branch}:hello.py"], cwd=repo,
                           capture_output=True, text=True, check=True).stdout
    assert "tweaked" in shown
    # ...while the live checkout is untouched: same branch, clean tree, no worktree left.
    assert _out(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert _out(repo, "status", "--porcelain") == ""
    assert "tweaked" not in (repo / "hello.py").read_text()
    assert len(_out(repo, "worktree", "list").splitlines()) == 1
    assert _out(repo, "diff", "--name-only", f"main..{branch}") == "hello.py"


def test_failed_apply_leaves_no_branch(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    report = SelfImprover(repo).stage_proposal(Proposal("bad", "", "not a real diff"))
    assert report["ok"] == "false"
    assert "self-improve" not in _out(repo, "branch", "--list")
    assert _out(repo, "status", "--porcelain") == ""


def test_bad_diff_reports_error(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    report = SelfImprover(repo).stage_proposal(Proposal("bad", "", "not a real diff"))
    assert report["ok"] == "false"
    assert report["stage"] in {"apply", "precheck"}


def test_not_a_git_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    report = SelfImprover(plain).stage_proposal(Proposal("x", "", "whatever"))
    assert report["ok"] == "false" and report["stage"] == "precheck"
