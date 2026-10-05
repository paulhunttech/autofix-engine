"""collect.py: result parsing, the empty-diff and no_fix rules, and the key scan."""

import json
import subprocess

import pytest

import collect
from collect import decide, parse_result


def raw(structured=None, **extra):
    doc = {"type": "result", "subtype": "success", "is_error": False, **extra}
    if structured is not None:
        doc["structured_output"] = structured
    return json.dumps(doc)


GOOD = {"outcome": "patch", "title": "Guard null lines", "summary": "s"}


def test_good_result_parsed():
    assert parse_result(raw(GOOD)) == (GOOD, None)


@pytest.mark.parametrize("text,problem", [
    ("not json", "not JSON"),
    (raw(None), "no structured result"),
    (raw({**GOOD, "extra": 1}), "wrong fields"),
    (raw({**GOOD, "outcome": "merge"}), "not patch or no_fix"),
    (raw({**GOOD, "title": 3}), "not text"),
    (json.dumps({"is_error": True, "subtype": "error_max_turns"}), "error_max_turns"),
    (json.dumps({"is_error": True, "subtype": "x; rm -rf"}), "unknown"),
])
def test_bad_results_become_fixed_problems(text, problem):
    result, msg = parse_result(text)
    assert result is None and problem in msg


def test_no_result_is_no_fix_and_discards_the_patch():
    final, patch = decide(None, "the agent returned no structured result", "diff --git a/x b/x\n")
    assert final["outcome"] == "no_fix" and patch == ""


def test_no_fix_discards_leftover_changes():
    final, patch = decide({**GOOD, "outcome": "no_fix"}, None, "diff --git a/x b/x\n")
    assert final["outcome"] == "no_fix" and patch == ""


def test_empty_diff_is_no_fix():
    final, patch = decide(GOOD, None, "  \n")
    assert final["outcome"] == "no_fix" and patch == ""


def test_patch_kept():
    final, patch = decide(GOOD, None, "diff --git a/x b/x\n")
    assert final == GOOD and patch


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def work(tmp_path):
    w = tmp_path / "work"
    (w / "src").mkdir(parents=True)
    (w / "src" / "a.py").write_text("a\n")
    git(w, "init", "-q")
    git(w, "add", "-A")
    git(w, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    (w / ".autofix").mkdir()
    (w / ".autofix" / "evidence.json").write_text("{}")
    return w


def base_of(w):
    return subprocess.run(["git", "-C", str(w), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def test_patch_excludes_autofix_and_includes_new_files(work, tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    (work / "src" / "a.py").write_text("b\n")
    (work / "src" / "new.py").write_text("n\n")
    (out / "result.raw").write_text(raw(GOOD))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    assert collect.main(["collect.py", str(work), str(out), base_of(work)]) == 0
    patch = (out / "patch.diff").read_text()
    assert "src/new.py" in patch and "src/a.py" in patch and ".autofix" not in patch
    assert json.loads((out / "result.json").read_text())["outcome"] == "patch"
    assert (out / "outcome").read_text() == "patch"


@pytest.mark.parametrize("raw_text", ["", "not json", json.dumps({"is_error": True, "subtype": "error_during_execution"})])
def test_failed_session_fails_the_job(work, tmp_path, monkeypatch, raw_text):
    """R3. Mutant killed: a crashed or errored session reported as a green no_fix."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "result.raw").write_text(raw_text)
    (out / "claude.rc").write_text("1\n")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    assert collect.main(["collect.py", str(work), str(out), base_of(work)]) == 1
    assert "agent session failed" in summary.read_text() and "claude exit 1" in summary.read_text()
    # The artifact still carries the fixed reason.
    assert json.loads((out / "result.json").read_text())["outcome"] == "no_fix"


def test_model_no_fix_stays_green(work, tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    (out / "result.raw").write_text(raw({"outcome": "no_fix", "title": "", "summary": "cause is upstream"}))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    assert collect.main(["collect.py", str(work), str(out), base_of(work)]) == 0


def test_git_runs_without_the_key(monkeypatch):
    """O1. Mutant killed: the git subprocesses inheriting ANTHROPIC_API_KEY."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    assert "ANTHROPIC_API_KEY" not in collect._git_env()


def test_git_argv_is_hardened_and_never_detects_renames(monkeypatch):
    """O7. Mutants killed: GIT_HARDENING emptied, --no-renames or --no-ext-diff/--no-textconv dropped."""
    calls = []

    class Done:
        stdout = b""

    def fake_run(argv, **kw):
        calls.append(argv)
        return Done()

    monkeypatch.setattr(collect.subprocess, "run", fake_run)
    collect.make_patch("/w", "base")
    assert len(calls) == 2
    for argv in calls:
        assert ["-c", "core.fsmonitor=false"] == argv[1:3] and ["-c", "core.hooksPath=/dev/null"] == argv[3:5]
    diff = calls[1]
    for flag in ("--cached", "--binary", "--no-renames", "--no-ext-diff", "--no-textconv"):
        assert flag in diff
    assert diff[-3:] == ["--", ".", ":!.autofix"]


@pytest.mark.parametrize("where", ["patch", "summary"])
def test_key_in_output_refuses_the_run(work, tmp_path, monkeypatch, where):
    """Mutant killed: the scan skipped, or run on only one of the outputs."""
    key = "sk-ant-api03-REALLOOKINGKEY"
    out = tmp_path / "out"
    out.mkdir()
    (work / "src" / "a.py").write_text(key + "\n" if where == "patch" else "b\n")
    summary = f"leaked {key}" if where == "summary" else "s"
    (out / "result.raw").write_text(raw({**GOOD, "summary": summary}))
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    assert collect.main(["collect.py", str(work), str(out), base_of(work)]) == 1
    assert not (out / "patch.diff").exists() and not (out / "result.json").exists()
