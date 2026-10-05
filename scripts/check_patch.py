#!/usr/bin/env python3
"""Refuse any patch the PR job must not push. All or nothing: one failure refuses the whole patch.

Runs in job `pr` before a write token is minted. The patch is model-written, so it is
checked as hostile input:

- every path matches an allowlist glob and is not under .github/ or .autofix/. The patch
  is made with --no-renames, so a rename is a delete plus an add and both paths are checked;
  a rename or copy section is refused outright;
- no binary hunks, no symlinks, no mode changes, no submodules;
- no added line in a rendered file (.md, .markdown, .svg, .html, .htm) carries an
  external reference: one that names a scheme (http:, data:, anything) or is
  protocol-relative (//host/...). Viewing a rich diff would otherwise fetch it;
- at most MAX_LINES changed lines and MAX_FILES files;
- git reads the patch exactly as this checker does: for every file, the same path with the
  same added and deleted counts, from `git apply --numstat`. Hunks are also counted against
  their headers as git counts them. Without this, a section smuggled after a hunk's declared
  length, or a line attributed to the wrong file, slips past the checks above;
- it applies cleanly to the base tree (when a repo is given).

Usage: check_patch.py <patch.diff> <allow json> [<repo dir>]
Prints only the refusal reason (never patch content) and exits 1 on refusal.
"""

import html
import json
import os
import re
import subprocess
import sys

MAX_LINES = 400
MAX_FILES = 20
RENDERED_SUFFIXES = (".md", ".markdown", ".svg", ".html", ".htm")
FORBIDDEN_ROOTS = (".github", ".autofix")
FORBIDDEN_FILES = (".gitmodules",)
REGULAR_MODE = "100644"


class Refused(Exception):
    """The patch is refused. The message is fixed text plus, at most, a path."""


# ---------------------------------------------------------------- allowlist globs

def glob_to_regex(glob):
    """`**` spans directories (`**/` may match none), `*` and `?` stay within one segment."""
    out, i = [], 0
    while i < len(glob):
        c = glob[i]
        if glob.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def check_path(path, allow_res):
    if not path or path.startswith("/") or "\\" in path or not path.isprintable():
        raise Refused(f"unsafe path: {path!r}")
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise Refused(f"unsafe path: {path!r}")
    # Case-folded, so a checkout on a case-insensitive filesystem cannot be steered either.
    if parts[0].casefold() in FORBIDDEN_ROOTS or any(p.casefold() == ".git" for p in parts):
        raise Refused(f"forbidden path: {path}")
    if path.casefold() in FORBIDDEN_FILES:
        raise Refused(f"forbidden path: {path}")
    if not any(r.match(path) for r in allow_res):
        raise Refused(f"path outside the allowlist: {path}")


# ---------------------------------------------------------------- diff parsing

def _strip_prefix(raw, prefix):
    if raw.startswith('"'):
        raise Refused("quoted path in patch")  # git quotes paths with unusual bytes; refuse them all
    raw = raw.rstrip("\t")
    if raw == "/dev/null":
        return None
    if not raw.startswith(prefix):
        raise Refused("malformed patch header")
    return raw[len(prefix):]


def header_path(rest):
    """The path from `diff --git a/P b/P` when both sides are the same unquoted path, else None.

    A section with no ---/+++ lines (a mode-only change, a binary file) names its path only
    here. When the sides differ, the rename/copy lines name both paths instead.
    """
    if rest.startswith('"'):
        raise Refused("quoted path in patch")
    n = (len(rest) - 5) // 2
    if len(rest) == 2 * n + 5 and rest.startswith("a/") and rest[2 + n:5 + n] == " b/" and rest[2:2 + n] == rest[5 + n:]:
        return rest[2:2 + n]
    return None


HUNK_RE = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


def parse(patch):
    """Split a `git diff` into per-file sections: paths, metadata, added lines, counts.

    Hunk lines are counted against the `@@ -a,b +c,d @@` header, as git apply counts them,
    so a line after a hunk's declared length is read as a header, not as content.
    """
    files, cur = [], None
    old_left = new_left = 0
    for line in patch.split("\n"):
        if old_left or new_left:
            tag = line[:1]
            if tag == " " and old_left and new_left:
                old_left, new_left = old_left - 1, new_left - 1
            elif tag == "-" and old_left:
                old_left -= 1
                cur["minus"] += 1
            elif tag == "+" and new_left:
                new_left -= 1
                cur["plus"] += 1
                cur["added"].append(line[1:])
            elif tag == "\\":
                pass
            else:
                raise Refused("hunk is shorter than its header declares")
            continue
        if line.startswith("\\") and cur is not None and cur["hunks"]:
            continue  # "\ No newline at end of file" after a hunk's last line
        if line.startswith("diff --git "):
            cur = {"paths": set(), "added": [], "plus": 0, "minus": 0, "hunks": 0, "meta": []}
            p = header_path(line[len("diff --git "):])
            if p is not None:
                cur["paths"].add(p)
            files.append(cur)
            continue
        if cur is None:
            if line.strip():
                raise Refused("patch has content before the first file header")
            continue
        if line.startswith("@@"):
            m = HUNK_RE.match(line)
            if not m:
                raise Refused("malformed hunk header")
            old_left = int(m.group(1)) if m.group(1) is not None else 1
            new_left = int(m.group(2)) if m.group(2) is not None else 1
            cur["hunks"] += 1
            continue
        if line.startswith("--- "):
            p = _strip_prefix(line[4:], "a/")
            if p is not None:
                cur["paths"].add(p)
        elif line.startswith("+++ "):
            p = _strip_prefix(line[4:], "b/")
            if p is not None:
                cur["paths"].add(p)
        elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            # collect.py diffs with --no-renames, so a rename arrives as a delete plus an add and git
            # reports both paths. A rename section here was not made by the engine.
            raise Refused("rename or copy section in patch")
        elif line:
            cur["meta"].append(line)
    return files


def check_meta(section):
    for m in section["meta"]:
        if m.startswith(("GIT binary patch", "Binary files ", "literal ", "delta ")):
            raise Refused("binary hunk")
        if m.startswith(("old mode ", "new mode ")):
            raise Refused("mode change")
        if m.startswith("new file mode ") and m.split()[-1] != REGULAR_MODE:
            raise Refused("new file is not a regular non-executable file (symlink, executable or submodule)")
        if m.startswith("deleted file mode ") and m.split()[-1] in ("120000", "160000"):
            raise Refused("deletes a symlink or submodule")
        if m.startswith("index ") and m.split()[-1] in ("120000", "160000"):
            raise Refused("changes a symlink or submodule")
        if m.startswith(("similarity index ", "dissimilarity index ", "index ", "new file mode ", "deleted file mode ")):
            continue
        raise Refused("unrecognised patch metadata")


# ---------------------------------------------------------------- external references in rendered files

SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
REF_PATTERNS = (
    re.compile(r"\]\(\s*<?\s*([^)\s>]*)"),  # markdown [text](target) and ![alt](target)
    re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?\s*(\S+)"),  # markdown reference definition
    re.compile(r"<\s*([A-Za-z][A-Za-z0-9+.\-]*:[^>\s]*)\s*>"),  # autolink <scheme:...>
    re.compile(r"""(?i)\b(?:src|href|xlink:href|srcset|action|formaction|poster|data|background|cite|longdesc)\s*=\s*["']?\s*([^"'\s>]+)"""),
    re.compile(r"""(?i)url\(\s*["']?\s*([^"')\s]+)"""),  # CSS url() in style blocks or attributes
    re.compile(r"""(?i)@import\s+["']\s*([^"']+)"""),
)
BARE_URL_RE = re.compile(r"(?i)(?:\b[a-z][a-z0-9+.\-]*:)?//[^\s/]")


def is_external(target):
    t = target.strip().strip("<>\"'")
    # Browsers treat a backslash like a slash, so /\host and \\host are protocol-relative too.
    if t.startswith(("//", "/\\", "\\\\", "\\/")):
        return True
    return bool(SCHEME_RE.match(t))


def check_rendered(path, added):
    if not path.casefold().endswith(RENDERED_SUFFIXES):
        return
    for raw in added:
        line = html.unescape(raw)
        for pat in REF_PATTERNS:
            for m in pat.finditer(line):
                if is_external(m.group(1)):
                    raise Refused(f"external reference in rendered file: {path}")
        # A bare scheme://host or //host anywhere is autolinked or fetched; refuse it as well.
        if BARE_URL_RE.search(line):
            raise Refused(f"external reference in rendered file: {path}")


# ---------------------------------------------------------------- git's own reading of the patch

def git_view(patch, repo=None):
    """One (path, added, deleted) record per file, as `git apply --numstat -z` reports them.

    Each record is `added<TAB>deleted<TAB>path<NUL>`. Binary files report `-` for both counts,
    and a rename or copy uses an empty path followed by `old<NUL>new<NUL>`. Both are refused
    before this runs, so either form here means git and the checker disagree.
    """
    cmd = ["git"] + (["-C", repo] if repo is not None else []) + ["apply", "--numstat", "-z", "-"]
    r = subprocess.run(cmd, input=patch.encode("utf-8", "surrogateescape"), capture_output=True)
    if r.returncode != 0:
        raise Refused("git cannot parse this patch")
    records = []
    for token in r.stdout.decode("utf-8", "surrogateescape").split("\0"):
        if not token:
            continue
        fields = token.split("\t", 2)
        if len(fields) != 3 or not fields[0].isdigit() or not fields[1].isdigit() or not fields[2]:
            raise Refused("git reads this patch differently from the checker")
        records.append((fields[2], int(fields[0]), int(fields[1])))
    return records


# ---------------------------------------------------------------- the whole check

def check(patch, allow, repo=None):
    if not patch.strip():
        raise Refused("empty patch")
    allow_res = [glob_to_regex(g) for g in allow]
    files = parse(patch)
    if not files:
        raise Refused("no file sections in patch")
    if len(files) > MAX_FILES:
        raise Refused(f"more than {MAX_FILES} files changed")
    changed = sum(f["plus"] + f["minus"] for f in files)
    if changed > MAX_LINES:
        raise Refused(f"more than {MAX_LINES} lines changed")
    for f in files:
        check_meta(f)
        if not f["paths"]:
            raise Refused("file section with no recognisable path")
        if len(f["paths"]) != 1:
            raise Refused("file section names more than one path")
        if not f["hunks"] and not f["meta"]:
            raise Refused("file section with no hunk and no metadata")
        for p in f["paths"]:
            check_path(p, allow_res)
            check_rendered(p, f["added"])
    # The parser above is only trusted where git agrees with it, section by section: the same
    # path with the same added and deleted counts. git apply is what will act on the patch, so
    # every line it would write must be one the checks above attributed to the right file.
    parsed = sorted((next(iter(f["paths"])), f["plus"], f["minus"]) for f in files)
    if parsed != sorted(git_view(patch, repo)):
        raise Refused("git reads this patch differently from the checker")
    if repo is not None:
        r = subprocess.run(["git", "-C", repo, "apply", "--check", "-"], input=patch.encode("utf-8", "surrogateescape"),
                           capture_output=True)
        if r.returncode != 0:
            raise Refused("patch does not apply cleanly to the base")
    return {"files": len(files), "lines": changed}


def main(argv):
    if len(argv) not in (3, 4):
        print("usage: check_patch.py <patch.diff> <allow json> [<repo dir>]", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8", errors="surrogateescape") as f:
        patch = f.read()
    allow = json.loads(argv[2])
    try:
        stats = check(patch, allow, argv[3] if len(argv) == 4 else None)
    except Refused as e:
        # The reason can carry a model-chosen path; escape it so the runner cannot decode a new command line.
        reason = str(e).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error::patch refused: {reason}")
        _summary("**check_patch**: refused (see the job log for the reason)")
        return 1
    print(f"patch accepted: files={stats['files']} lines={stats['lines']}")
    _summary(f"**check_patch**: accepted, files={stats['files']} lines={stats['lines']}")
    return 0


def _summary(line):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
