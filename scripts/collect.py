#!/usr/bin/env python3
"""Turn the agent's session into patch.diff and result.json, in job `agent`.

After claude exits: stage everything, diff against the base SHA excluding .autofix/,
and parse the structured result. An empty diff is no_fix, and so is a no_fix result
that left changes behind (they are discarded). If the literal Anthropic key appears
in the patch or the result, the run is refused and neither file is kept.

Usage: collect.py <work dir> <out dir> <base sha>
Reads ANTHROPIC_API_KEY from the environment for the scan only. Writes counts, never
model text, to the job log and summary.
"""

import json
import os
import subprocess
import sys

RESULT_FIELDS = ("outcome", "title", "summary")
OUTCOMES = ("patch", "no_fix")


def parse_result(raw_text):
    """Extract {outcome, title, summary} from claude's --output-format json output.

    Returns (result, problem) where problem is a fixed, non-model string when the agent
    returned nothing usable.
    """
    try:
        doc = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return None, "the agent's output was not JSON"
    if not isinstance(doc, dict):
        return None, "the agent's output was not an object"
    if doc.get("is_error"):
        subtype = doc.get("subtype")
        subtype = subtype if isinstance(subtype, str) and subtype.replace("_", "").isalnum() else "unknown"
        return None, f"the agent session ended in error ({subtype[:40]})"
    structured = doc.get("structured_output")
    if not isinstance(structured, dict):
        return None, "the agent returned no structured result"
    if set(structured) != set(RESULT_FIELDS):
        return None, "the agent's result had the wrong fields"
    if structured["outcome"] not in OUTCOMES:
        return None, "the agent's outcome was not patch or no_fix"
    if not all(isinstance(structured[k], str) for k in ("title", "summary")):
        return None, "the agent's title or summary was not text"
    return {k: structured[k] for k in RESULT_FIELDS}, None


def diff_stats(patch):
    files = lines = 0
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            files += 1
        elif (line.startswith("+") and not line.startswith("+++ ")) or (
            line.startswith("-") and not line.startswith("--- ")
        ):
            lines += 1
    return files, lines


def decide(result, problem, patch):
    """Combine the parsed result and the diff into the final result.json content and patch."""
    if result is None:
        return {"outcome": "no_fix", "title": "", "summary": problem}, ""
    if result["outcome"] == "no_fix":
        return result, ""
    if not patch.strip():
        return {"outcome": "no_fix", "title": result["title"], "summary": result["summary"]}, ""
    return result, patch


def contains_key(key, *texts):
    return bool(key) and any(key in t for t in texts)


# The tree was edited by the model. Its .git came from a fresh checkout and is a protected
# path, but git runs here without the key in its environment and with every config-driven
# execution hook off, in case that ever stops holding.
GIT_HARDENING = ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"]


def _git_env():
    return {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}


def make_patch(work, base):
    """--no-renames, so a rename is a delete plus an add and every path is reported by git."""
    env = _git_env()
    subprocess.run(["git", *GIT_HARDENING, "-C", work, "add", "-A"], check=True, capture_output=True, env=env)
    out = subprocess.run(
        ["git", *GIT_HARDENING, "-C", work, "diff", "--cached", "--binary", "--no-renames",
         "--no-ext-diff", "--no-textconv", base, "--", ".", ":!.autofix"],
        check=True, capture_output=True, env=env,
    )
    return out.stdout.decode("utf-8", errors="surrogateescape")


def main(argv):
    if len(argv) != 4:
        print("usage: collect.py <work dir> <out dir> <base sha>", file=sys.stderr)
        return 2
    work, out, base = argv[1:]
    try:
        with open(os.path.join(out, "result.raw"), encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        raw = ""
    result, problem = parse_result(raw)
    patch = make_patch(work, base)
    final, patch = decide(result, problem, patch)

    if contains_key(os.environ.get("ANTHROPIC_API_KEY", ""), patch, json.dumps(final), raw):
        print("::error::refused: the Anthropic key appears in the agent's output")
        return 1

    with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False)
    with open(os.path.join(out, "patch.diff"), "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(patch)

    files, lines = diff_stats(patch)
    counts = f"outcome={final['outcome']} files_changed={files} lines_changed={lines}"
    with open(os.path.join(out, "outcome"), "w", encoding="utf-8") as f:
        f.write(final["outcome"])
    if result is None:
        # The session failed (bad key, no credit, max turns, crash). That is not the model judging
        # the problem unfixable, so it fails the job. `problem` is fixed text, never model output.
        line = f"agent session failed: {problem} (claude exit {_rc(out)})"
        print(f"::error::{line}")
        _summary(f"**agent**: {line}")
        return 1
    print(counts)
    _summary(f"**agent**: {counts}")
    return 0


def _rc(out):
    try:
        with open(os.path.join(out, "claude.rc"), encoding="utf-8") as f:
            rc = f.read().strip()
    except OSError:
        return "unknown"
    return rc if rc.isdigit() else "unknown"


def _summary(line):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
