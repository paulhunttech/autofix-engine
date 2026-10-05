"""Structural checks on engine.yml and the agent invocation."""

import os
import re

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return yaml.safe_load(f)


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


ENGINE = load(".github/workflows/engine.yml")
JOBS = ENGINE["jobs"]
RUN_AGENT = read("scripts/run_agent.sh")

# `${{` inside a `run:` is how an input becomes code. Exceptions must be listed here,
# with a reason; v1 has none.
RUN_EXPRESSION_ALLOWLIST = set()


def all_workflows():
    d = os.path.join(ROOT, ".github", "workflows")
    return sorted(os.path.join(".github", "workflows", f) for f in os.listdir(d) if f.endswith((".yml", ".yaml")))


@pytest.mark.parametrize("path", all_workflows() + ["examples/host/.github/workflows/autofix.yml"])
def test_no_expression_inside_any_run(path):
    """Invariant 4. Mutant killed: any `${{ inputs.* }}` (or other expression) interpolated into a script."""
    wf = load(path)
    for job_name, job in wf["jobs"].items():
        for i, step in enumerate(job.get("steps", [])):
            if "run" in step and "${{" in step["run"]:
                assert (job_name, i) in RUN_EXPRESSION_ALLOWLIST, f"{path}: {job_name} step {i} has ${{{{ in run:"


def test_jobs_are_the_five():
    assert list(JOBS) == ["resolve", "fetch", "evidence", "agent", "pr"]


EXPECTED_PERMISSIONS = {
    "resolve": {"contents": "read"},
    "fetch": {},
    "evidence": {"contents": "read", "id-token": "write"},
    "agent": {},
    "pr": {"contents": "read"},
}


@pytest.mark.parametrize("job", EXPECTED_PERMISSIONS)
def test_job_permissions(job):
    """Mutant killed: a widened or missing permissions block (a missing one inherits the caller's grant)."""
    assert JOBS[job].get("permissions") == EXPECTED_PERMISSIONS[job]


def secrets_in(job):
    return set(re.findall(r"secrets\.([A-Za-z0-9_]+)", yaml.safe_dump(job)))


EXPECTED_SECRETS = {
    "resolve": set(),
    "fetch": {"PR_APP_CLIENT_ID", "PR_APP_PRIVATE_KEY"},
    "evidence": set(),
    "agent": {"ANTHROPIC_API_KEY"},
    "pr": {"PR_APP_CLIENT_ID", "PR_APP_PRIVATE_KEY"},
}


@pytest.mark.parametrize("job", EXPECTED_SECRETS)
def test_job_secrets(job):
    """Invariant 3. Mutant killed: a write credential (or any extra secret) reaching the agent or evidence job."""
    assert secrets_in(JOBS[job]) == EXPECTED_SECRETS[job]


def test_no_secrets_inherit_or_token_in_agent():
    text = yaml.safe_dump(JOBS["agent"])
    assert "github.token" not in text and "GITHUB_TOKEN" not in text
    assert "id-token" not in text


def test_agent_invokes_claude_only_through_run_agent():
    """O7. Mutant killed: an inline claude call in engine.yml that drifts from the canary-tested one."""
    runs = [s.get("run", "") for s in JOBS["agent"]["steps"]]
    calls = [r for r in runs if re.search(r"(^|[\s;&|])claude(\s|$)", r)]
    assert calls == [], "engine.yml must not call claude directly"
    assert any("engine/scripts/run_agent.sh" in r for r in runs)
    for job_name, job in JOBS.items():
        if job_name != "agent":
            assert not any("run_agent.sh" in s.get("run", "") for s in job.get("steps", []))


def default_command():
    """The claude command line on run_agent.sh's default path (isolation array expanded)."""
    iso = re.search(r"^isolation=\((.*)\)$", RUN_AGENT, re.M).group(1)
    cmd = re.search(r"claude -p .*?\|\| rc=\$\?", RUN_AGENT, re.S).group(0)
    return cmd.replace('"${isolation[@]}"', iso)


@pytest.mark.parametrize("flag", ["--restricted", "--strict-mcp-config", "--permission-mode dontAsk", "--permission-prompts none"])
def test_run_agent_default_flags(flag):
    """R1/R2. Mutant killed: an isolation or permission flag dropped from the default path."""
    assert flag in default_command()


def test_run_agent_tools_have_no_bash_or_web():
    tools = re.search(r'--tools "([^"]+)"', default_command()).group(1).split(",")
    assert tools == ["Read", "Edit", "Write", "Glob", "Grep"]
    assert not {"Bash", "WebFetch", "WebSearch", "PowerShell"} & set(tools)


def test_run_agent_scrubs_subprocess_env_and_writes_output_to_files():
    cmd = default_command()
    assert "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1 claude" in RUN_AGENT
    assert '>"$out/result.raw"' in cmd and '2>"$out/claude.err"' in cmd


def test_canary_switch_cannot_be_set_from_engine():
    """O7. Mutant killed: engine.yml (or the host example) setting the flag-drop switch."""
    for path in [".github/workflows/engine.yml", "examples/host/.github/workflows/autofix.yml"]:
        assert "AUTOFIX_CANARY" not in read(path)


def test_canary_uses_the_engine_cli_version():
    pin = re.findall(r"@anthropic-ai/claude-code@(\S+)", read(".github/workflows/engine.yml"))
    ci = re.findall(r"@anthropic-ai/claude-code@(\S+)", read(".github/workflows/ci.yml"))
    assert len(set(pin)) == 1 and set(ci) == set(pin)
    assert re.fullmatch(r"\d+\.\d+\.\d+", pin[0])


@pytest.mark.parametrize("path,job", [(".github/workflows/engine.yml", "agent"), (".github/workflows/ci.yml", "canary")])
def test_bubblewrap_installed_before_the_agent_runs(path, job):
    """CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1 needs bubblewrap; without it claude exits before start-up
    (observed in the first CI canary run). Mutant killed: the install step dropped from either job."""
    runs = [s.get("run", "") for s in load(path)["jobs"][job]["steps"]]
    install = next(i for i, r in enumerate(runs) if "apt-get install" in r and "bubblewrap" in r)
    agent = next(i for i, r in enumerate(runs) if "run_agent.sh" in r or "canary.sh" in r)
    assert install < agent


def test_third_party_actions_pinned_by_sha():
    for path in all_workflows() + ["examples/host/.github/workflows/autofix.yml"]:
        for ref in re.findall(r"^\s*(?:-\s+)?uses:\s*(\S+)", read(path), re.M):
            if ref.startswith("./"):
                continue
            assert re.search(r"@[0-9a-f]{40}$", ref), f"{path}: {ref} is not pinned to a full SHA"


def test_artifacts_kept_one_day():
    for job in JOBS.values():
        for step in job.get("steps", []):
            if str(step.get("uses", "")).startswith("actions/upload-artifact@"):
                assert step["with"]["retention-days"] == 1


def test_pr_job_gated_on_patch_and_not_dry_run():
    cond = JOBS["pr"]["if"]
    assert "needs.agent.outputs.outcome == 'patch'" in cond
    assert "needs.resolve.outputs.dry_run == 'false'" in cond


def test_pr_job_never_forces_or_merges():
    run = " ".join(s.get("run", "") for s in JOBS["pr"]["steps"])
    assert "--force" not in run and " -f " not in run and "+HEAD" not in run
    assert "gh pr merge" not in run and "--draft" in run


def test_fetch_tars_without_credentials():
    checkout = next(s for s in JOBS["fetch"]["steps"] if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert checkout["with"]["persist-credentials"] is False
