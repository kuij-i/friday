import shutil
import subprocess
import textwrap

import pytest

from friday.selfimprove.proposer import Proposal, SelfImprover

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


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
    assert report["branch"].startswith("friday/self-improve-")
    assert "tweaked" in (repo / "hello.py").read_text()


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
