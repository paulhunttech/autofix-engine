#!/usr/bin/env python3
"""Build the draft PR's title and body so that nothing in them renders or fetches.

The agent's summary and the problem id are untrusted. Each goes inside a code fence
longer than any backtick run within it, so images, links and @mentions render as
inert text and nothing in the body causes an outbound fetch when the PR is viewed.
The title is plain text: control and format characters removed, whitespace
collapsed, capped at TITLE_MAX.

When another open PR on the target touches a patched path, a notice heads the body. Only
integers the engine has validated are rendered from that listing: a confirmed overlap as a
same-repository reference (`#N`, so its author sees the back-reference), an unverified one as
inert text (`PR N`). A defect anywhere in the overlap path degrades to "could not check", never
to a failed job: the notice annotates the PR, and must never cost it.

Usage: pr_body.py <result.json> <out title file> <out body file> <patch.diff> <open_prs.json>
Reads INPUT_PROBLEM_ID, INPUT_WINDOW_START and INPUT_WINDOW_END from the environment.
"""

import json
import os
import re
import sys
import unicodedata

from check_patch import parse

TITLE_MAX = 100
SUMMARY_MAX = 4000
DEFAULT_TITLE = "Autofix: proposed fix"
# The `pr` job's list step passes this as `--limit`; tests/test_workflow.py pins the two together.
# A listing this long may have been cut, so the check is reported incomplete.
LISTING_LIMIT = 200
OVERLAP_HEADING = "## ⚠️ Other open PRs touch these files"
COULD_NOT_CHECK = "Could not check for other open PRs touching these files."
INCOMPLETE = "The check for other open PRs was incomplete, so one not named here may also touch these files."
CI_NOTE = (
    "If this repository gates CI on autofix PRs (host-setup gate 2), its checks fail at the autofix "
    "gate step until a person has read the diff and released CI. A red check whose failing step is "
    "that gate means the gate held, not that the build failed. Once CI has been released, a red "
    "check is a real failure."
)
FOOTER = (
    "---\n"
    "This pull request was written by a machine and has not been reviewed. "
    "It is a draft: read the diff before trusting the summary, and only a human merges it."
)


def _strip_controls(text, keep=""):
    return "".join(
        ch for ch in text
        if ch in keep or unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp")
    )


def plain_title(raw):
    # Collapse whitespace first, so a newline between words becomes a space rather than vanishing.
    title = re.sub(r"\s+", " ", raw or "")
    title = _strip_controls(title).strip()
    title = re.sub(r" {2,}", " ", title)
    if len(title) > TITLE_MAX:
        title = title[: TITLE_MAX - 1].rstrip() + "…"
    return title or DEFAULT_TITLE


def fence(text):
    """Wrap text in a backtick fence that nothing inside it can close."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}text\n{text}\n{ticks}"


def clean_block(raw, cap):
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _strip_controls(text, keep="\n\t")
    if len(text) > cap:
        text = text[:cap] + "\n…[truncated]"
    return text


def patch_paths(patch_text):
    """Every path any section of the patch names, as the checker parsed it."""
    return {p for section in parse(patch_text) for p in section["paths"]}


def read_listing(path):
    """The open-PR listing, or None when it is missing, empty, not JSON, or not a list."""
    try:
        with open(path, encoding="utf-8") as f:
            listing = json.load(f)
    except (OSError, ValueError):
        return None
    return listing if isinstance(listing, list) else None


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _readable(entry):
    return (
        isinstance(entry, dict)
        and _is_int(entry.get("number"))
        and _is_int(entry.get("changedFiles"))
        and isinstance(entry.get("files"), list)
        and all(isinstance(f, dict) and isinstance(f.get("path"), str) for f in entry["files"])
    )


def _overlap_notice(paths, listing):
    if listing is None:
        return "\n\n".join([OVERLAP_HEADING, COULD_NOT_CHECK])
    overlaps, unverified = set(), set()
    incomplete = len(listing) >= LISTING_LIMIT
    for entry in listing:
        if not _readable(entry):
            incomplete = True
            continue
        files = entry["files"]
        if paths & {f["path"] for f in files}:
            overlaps.add(entry["number"])
        elif entry["changedFiles"] > len(files):
            unverified.add(entry["number"])
    if not (overlaps or unverified or incomplete):
        return ""
    lines = [OVERLAP_HEADING]
    if overlaps:
        lines.append("These open PRs change a file this PR changes. Check that this one is not a duplicate: "
                     + ", ".join(f"#{n}" for n in sorted(overlaps)))
    if unverified:
        lines.append("These open PRs change more files than could be listed, so they may too: "
                     + ", ".join(f"PR {n}" for n in sorted(unverified)))
    if incomplete:
        lines.append(INCOMPLETE)
    return "\n\n".join(lines)


def overlap_notice(paths, listing):
    """The notice for the head of the body, or "" when nothing overlaps and the check was complete."""
    try:
        return _overlap_notice(paths, listing)
    except Exception:  # noqa: BLE001 — the notice must never cost the PR; the text says it failed
        return "\n\n".join([OVERLAP_HEADING, COULD_NOT_CHECK])


def build(result, problem_id, window_start, window_end, notice=""):
    title = plain_title(result.get("title", ""))
    summary = clean_block(result.get("summary", ""), SUMMARY_MAX)
    body = "\n\n".join(([notice] if notice else []) + [
        "## Summary from the agent",
        fence(summary),
        "## Problem",
        fence(clean_block(problem_id, 1000)),
        # The window is validated to two ISO-8601 timestamps in job resolve, so it is safe as text.
        f"Window: {window_start} to {window_end} (UTC)",
        CI_NOTE,
        FOOTER,
    ])
    return title, body + "\n"


def main(argv):
    if len(argv) != 6:
        print("usage: pr_body.py <result.json> <out title> <out body> <patch.diff> <open_prs.json>", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        result = json.load(f)
    try:
        with open(argv[4], encoding="utf-8", errors="surrogateescape") as f:
            paths = patch_paths(f.read())
    except Exception:  # noqa: BLE001 — same rule as overlap_notice: degrade, never fail the PR
        paths = None
    notice = overlap_notice(paths, read_listing(argv[5])) if paths is not None else \
        "\n\n".join([OVERLAP_HEADING, COULD_NOT_CHECK])
    title, body = build(
        result,
        os.environ["INPUT_PROBLEM_ID"],
        os.environ["INPUT_WINDOW_START"],
        os.environ["INPUT_WINDOW_END"],
        notice,
    )
    with open(argv[2], "w", encoding="utf-8") as f:
        f.write(title)
    with open(argv[3], "w", encoding="utf-8") as f:
        f.write(body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
