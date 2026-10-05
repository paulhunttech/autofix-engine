#!/usr/bin/env python3
"""Build the draft PR's title and body so that nothing in them renders or fetches.

The agent's summary and the problem id are untrusted. Each goes inside a code fence
longer than any backtick run within it, so images, links and @mentions render as
inert text and nothing in the body causes an outbound fetch when the PR is viewed.
The title is plain text: control and format characters removed, whitespace
collapsed, capped at TITLE_MAX.

Usage: pr_body.py <result.json> <out title file> <out body file>
Reads INPUT_PROBLEM_ID, INPUT_WINDOW_START and INPUT_WINDOW_END from the environment.
"""

import json
import os
import re
import sys
import unicodedata

TITLE_MAX = 100
SUMMARY_MAX = 4000
DEFAULT_TITLE = "Autofix: proposed fix"
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


def build(result, problem_id, window_start, window_end):
    title = plain_title(result.get("title", ""))
    summary = clean_block(result.get("summary", ""), SUMMARY_MAX)
    body = "\n\n".join([
        "## Summary from the agent",
        fence(summary),
        "## Problem",
        fence(clean_block(problem_id, 1000)),
        # The window is validated to two ISO-8601 timestamps in job resolve, so it is safe as text.
        f"Window: {window_start} to {window_end} (UTC)",
        FOOTER,
    ])
    return title, body + "\n"


def main(argv):
    if len(argv) != 4:
        print("usage: pr_body.py <result.json> <out title> <out body>", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        result = json.load(f)
    title, body = build(
        result,
        os.environ["INPUT_PROBLEM_ID"],
        os.environ["INPUT_WINDOW_START"],
        os.environ["INPUT_WINDOW_END"],
    )
    with open(argv[2], "w", encoding="utf-8") as f:
        f.write(title)
    with open(argv[3], "w", encoding="utf-8") as f:
        f.write(body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
