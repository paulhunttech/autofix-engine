#!/usr/bin/env python3
"""Validate the dispatch inputs and resolve the target from the host's config.

Runs in job `resolve`, which holds no secrets. Every input is untrusted data: it
arrives through the environment, is checked against a fixed shape, and a failure
stops the run before any later job touches a secret.

Usage: resolve.py <path to autofix-targets.json>
Reads INPUT_* from the environment and writes outputs to $GITHUB_OUTPUT.
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

TARGET_RE = re.compile(r"^[a-z0-9-]{1,40}$")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,7})?Z$")
REPO_RE = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")
GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

PROBLEM_ID_MAX = 1000
FINDING_MAX = 400  # the caller's FINDING_MAX_CHARS
BRIEF_MAX = 1500  # the caller's BRIEF_MAX_CHARS
WINDOW_MAX = timedelta(hours=24)
ALLOW_MAX_ENTRIES = 50
ALLOW_MAX_CHARS = 200
BRANCH_PREFIX = "autofix/"


class InputError(ValueError):
    """An input or config value failed validation. The message names the field, never its value."""


def _parse_timestamp(name, value):
    if not TIMESTAMP_RE.match(value):
        raise InputError(f"{name}: not an ISO-8601 UTC timestamp ending in Z")
    # fromisoformat accepts at most 6 fractional digits; trim a 7th (KQL emits 7).
    trimmed = re.sub(r"(\.\d{6})\d", r"\1", value[:-1])
    try:
        return datetime.fromisoformat(trimmed).replace(tzinfo=timezone.utc)
    except ValueError:
        raise InputError(f"{name}: not a valid date and time") from None


def validate_inputs(raw):
    """Check every dispatch input. Returns the validated values; raises InputError."""
    target = raw.get("target", "")
    if not TARGET_RE.match(target):
        raise InputError("target: must match ^[a-z0-9-]{1,40}$")

    problem_id = raw.get("problem_id", "")
    if not 1 <= len(problem_id) <= PROBLEM_ID_MAX:
        raise InputError(f"problem_id: must be 1-{PROBLEM_ID_MAX} characters")
    if not problem_id.isprintable():
        raise InputError("problem_id: must be printable, with no newlines or control characters")

    start = _parse_timestamp("window_start", raw.get("window_start", ""))
    end = _parse_timestamp("window_end", raw.get("window_end", ""))
    if not start < end:
        raise InputError("window: window_start must be before window_end")
    if end - start > WINDOW_MAX:
        raise InputError("window: must be at most 24 hours")

    finding = raw.get("finding", "")
    if len(finding) > FINDING_MAX:
        raise InputError(f"finding: must be at most {FINDING_MAX} characters")
    brief = raw.get("brief", "")
    if len(brief) > BRIEF_MAX:
        raise InputError(f"brief: must be at most {BRIEF_MAX} characters")

    dry_run = raw.get("dry_run", "true")
    if dry_run not in ("true", "false"):
        raise InputError("dry_run: must be true or false")

    return {
        "target": target,
        "problem_id": problem_id,
        "window_start": raw["window_start"],
        "window_end": raw["window_end"],
        "finding": finding,
        "brief": brief,
        "dry_run": dry_run,
    }


def _check_glob(glob):
    if not isinstance(glob, str) or not 1 <= len(glob) <= ALLOW_MAX_CHARS:
        raise InputError(f"allow: each entry must be a string of 1-{ALLOW_MAX_CHARS} characters")
    if glob.startswith("/") or "\\" in glob or not glob.isprintable():
        raise InputError("allow: entries are repo-relative, with no backslashes or control characters")
    if any(part in ("", ".", "..") for part in glob.split("/")):
        raise InputError("allow: entries may not contain empty, '.' or '..' segments")


def load_target(config, key):
    """Look up one target in the parsed targets config. Raises InputError on anything malformed."""
    targets = config.get("targets") if isinstance(config, dict) else None
    if not isinstance(targets, dict):
        raise InputError("config: must be an object with a 'targets' object")
    entry = targets.get(key)
    if entry is None:
        raise InputError("target: no such key in the host's targets config")
    if not isinstance(entry, dict):
        raise InputError("config: the target entry must be an object")

    repo = entry.get("repo")
    if not isinstance(repo, str) or not REPO_RE.match(repo):
        raise InputError("config: repo must be owner/name")
    allow = entry.get("allow")
    if not isinstance(allow, list) or not 1 <= len(allow) <= ALLOW_MAX_ENTRIES:
        raise InputError(f"config: allow must be a list of 1-{ALLOW_MAX_ENTRIES} globs")
    for glob in allow:
        _check_glob(glob)
    workspace = entry.get("workspace")
    if not isinstance(workspace, str) or not GUID_RE.match(workspace):
        raise InputError("config: workspace must be a Log Analytics workspace id (a GUID)")

    owner, name = repo.split("/", 1)
    return {"repo": repo, "owner": owner, "name": name, "allow": allow, "workspace": workspace}


def branch_for(problem_id):
    return BRANCH_PREFIX + hashlib.sha256(problem_id.encode("utf-8")).hexdigest()[:8]


def main(argv):
    if len(argv) != 2:
        print("usage: resolve.py <autofix-targets.json>", file=sys.stderr)
        return 2
    names = ("target", "problem_id", "window_start", "window_end", "finding", "brief", "dry_run")
    raw = {n: os.environ.get("INPUT_" + n.upper(), "") for n in names}
    try:
        inputs = validate_inputs(raw)
        try:
            with open(argv[1], encoding="utf-8") as f:
                config = json.load(f)
        except (OSError, json.JSONDecodeError):
            raise InputError("config: the host's targets file is missing or not valid JSON") from None
        target = load_target(config, inputs["target"])
    except InputError as e:
        print(f"::error::{e}")
        return 1

    outputs = {
        "repo": target["repo"],
        "owner": target["owner"],
        "name": target["name"],
        "allow": json.dumps(target["allow"], separators=(",", ":")),
        "workspace": target["workspace"],
        "branch": branch_for(inputs["problem_id"]),
        "dry_run": inputs["dry_run"],
    }
    # Every value above is validated to a single line, so the simple key=value form is safe.
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
        for k, v in outputs.items():
            f.write(f"{k}={v}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
