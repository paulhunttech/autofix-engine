"""pr_body.py: model text renders inert, the fence cannot be closed, the caps hold."""

import re

from pr_body import DEFAULT_TITLE, SUMMARY_MAX, TITLE_MAX, build, fence, plain_title


def fenced_blocks(body):
    """Return the contents of each backtick fence, per CommonMark's closing rule."""
    blocks, lines, i = [], body.split("\n"), 0
    while i < len(lines):
        m = re.match(r"^ {0,3}(`{3,})", lines[i])
        if m:
            n, content, i = len(m.group(1)), [], i + 1
            while i < len(lines) and not re.match(r"^ {0,3}`{%d,}\s*$" % n, lines[i]):
                content.append(lines[i])
                i += 1
            blocks.append("\n".join(content))
        i += 1
    return blocks


def outside_fences(body):
    out, lines, i = [], body.split("\n"), 0
    while i < len(lines):
        m = re.match(r"^ {0,3}(`{3,})", lines[i])
        if m:
            n, i = len(m.group(1)), i + 1
            while i < len(lines) and not re.match(r"^ {0,3}`{%d,}\s*$" % n, lines[i]):
                i += 1
        else:
            out.append(lines[i])
        i += 1
    return "\n".join(out)


def test_image_link_mention_land_inside_the_fence():
    """Mutant killed: summary placed in the body unfenced."""
    summary = "See ![x](https://evil/p.png) and [y](https://evil) cc @someone"
    _, body = build({"title": "t", "summary": summary}, "pid", "s", "e")
    assert summary in fenced_blocks(body)
    rest = outside_fences(body)
    assert "evil" not in rest and "@someone" not in rest


def test_backtick_run_cannot_close_the_fence():
    """Mutant killed: a fixed ``` fence."""
    summary = "before\n```\n![x](https://evil/p.png)\n``````\nafter"
    _, body = build({"title": "t", "summary": summary}, "pid", "s", "e")
    assert "evil" not in outside_fences(body)
    assert summary in fenced_blocks(body)


def test_fence_longer_than_any_run():
    assert fence("a ```` b").startswith("`````text\n")
    assert fence("plain").startswith("```text\n")


def test_problem_id_is_fenced_too():
    _, body = build({"title": "t", "summary": "s"}, "![p](https://evil/q.png)", "s", "e")
    assert "evil" not in outside_fences(body)


def test_title_plain_and_capped():
    """Mutants killed: no control stripping, no cap, no whitespace collapse."""
    t = plain_title("Fix\x00 null‮\nref " + "x" * 200)
    assert "\x00" not in t and "‮" not in t and "\n" not in t
    assert len(t) <= TITLE_MAX
    assert t.startswith("Fix null ref")


def test_empty_title_defaults():
    assert plain_title("\x01 \t") == DEFAULT_TITLE


def test_summary_capped():
    _, body = build({"title": "t", "summary": "s" * (SUMMARY_MAX + 500)}, "pid", "s", "e")
    assert "s" * (SUMMARY_MAX + 1) not in body and "[truncated]" in body


def test_footer_present():
    _, body = build({"title": "t", "summary": "s"}, "pid", "2026-10-05T08:00:00Z", "2026-10-05T09:00:00Z")
    assert "written by a machine" in body and "only a human merges it" in body
    assert "2026-10-05T08:00:00Z" in body


# ---------------------------------------------------------------- overlapping open PRs and the CI note

import json

import pytest

import pr_body
from pr_body import (CI_NOTE, COULD_NOT_CHECK, INCOMPLETE, LISTING_LIMIT, OVERLAP_HEADING, main, overlap_notice,
                     patch_paths, read_listing)


def section(path):
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-old\n+new\n"


TWO_SECTIONS = section("src/a.cs") + section("src/b.cs")


def pr(number, *paths, changed=None):
    return {"number": number, "files": [{"path": p} for p in paths],
            "changedFiles": len(paths) if changed is None else changed}


def body_with(listing, patch=TWO_SECTIONS):
    notice = overlap_notice(patch_paths(patch), listing)
    return pr_body.build({"title": "t", "summary": "s"}, "pid", "s", "e", notice)[1]


def test_the_two_failure_texts_are_distinct():
    """R4: a validator crash caught by the wrapper must not satisfy an 'incomplete' assertion."""
    assert COULD_NOT_CHECK not in INCOMPLETE and INCOMPLETE not in COULD_NOT_CHECK


def test_no_overlap_no_notice():
    body = body_with([pr(7, "src/other.cs"), pr(8, "docs/x.md")])
    assert OVERLAP_HEADING not in body and "#7" not in body and "PR 7" not in body
    assert body.startswith("## Summary from the agent")


def test_overlap_in_second_section_only_is_named_first():
    """Mutants killed: patch_paths reading only the first section; the notice placed after the summary;
    a non-overlapping PR named too."""
    body = body_with([pr(41, "src/b.cs"), pr(42, "src/other.cs")])
    assert body.startswith(OVERLAP_HEADING)
    assert body.index("#41") < body.index("## Summary from the agent")
    assert "#42" not in body and "PR 42" not in body
    assert INCOMPLETE not in body and COULD_NOT_CHECK not in body


def test_hostile_path_in_a_non_overlapping_entry_never_renders():
    body = body_with([pr(5, "![x](https://evil/p.png)", "@someone"), pr(6, "src/a.cs")])
    assert "evil" not in body and "@someone" not in body and "#6" in body


@pytest.mark.parametrize("entry", [
    {"number": 9, "files": None, "changedFiles": 3},
    {"number": 9, "files": [{"path": "src/z.cs"}]},
    "just a string",
    {"number": "9", "files": [], "changedFiles": 0},
    {"number": True, "files": [], "changedFiles": 0},
    {"number": 9, "files": [{"path": 3}], "changedFiles": 1},
    {"number": 9, "files": ["src/a.cs"], "changedFiles": 1},
])
def test_unreadable_entry_is_incomplete_not_a_crash(entry):
    """R1/R4. Mutant killed: any shape check dropped, so the wrapper's could-not-check text appears instead."""
    body = body_with([entry])
    assert INCOMPLETE in body and COULD_NOT_CHECK not in body


def test_one_bad_entry_does_not_hide_a_real_overlap():
    body = body_with([{"number": 9, "files": None, "changedFiles": 3}, pr(10, "src/a.cs")])
    assert "#10" in body and INCOMPLETE in body and COULD_NOT_CHECK not in body


def test_an_internal_failure_degrades_to_could_not_check(monkeypatch):
    def boom(_):
        raise RuntimeError("x")
    monkeypatch.setattr(pr_body, "_readable", boom)
    body = body_with([pr(1, "src/a.cs")])
    assert COULD_NOT_CHECK in body and "## Summary from the agent" in body


def test_truncated_file_list_is_unverified_and_inert():
    """A PR whose file list was cut at gh's cap, with no visible match, may still overlap: named inertly."""
    body = body_with([pr(77, "src/other.cs", changed=150)])
    assert "PR 77" in body and "#77" not in body
    assert INCOMPLETE not in body and COULD_NOT_CHECK not in body


def test_missing_listing_could_not_check():
    body = body_with(None)
    assert COULD_NOT_CHECK in body and INCOMPLETE not in body


def test_listing_at_the_limit_is_incomplete_one_below_is_not():
    """Boundary rows on both sides. Mutant killed: `>` for `>=`, or the limit check removed."""
    at = [pr(1000 + i, "src/other.cs") for i in range(LISTING_LIMIT)]
    assert INCOMPLETE in body_with(at)
    assert OVERLAP_HEADING not in body_with(at[:-1])


@pytest.mark.parametrize("content", [None, "", "not json", '{"number": 1}'])
def test_read_listing_returns_none_for_anything_but_a_list(tmp_path, content):
    path = tmp_path / "open_prs.json"
    if content is not None:
        path.write_text(content)
    assert read_listing(str(path)) is None


def test_read_listing_reads_a_list(tmp_path):
    path = tmp_path / "open_prs.json"
    path.write_text(json.dumps([pr(1, "a")]))
    assert read_listing(str(path)) == [pr(1, "a")]


def test_ci_note_present_outside_fences():
    body = body_with([])
    assert CI_NOTE in outside_fences(body)


def test_main_requires_five_args(capsys):
    assert main(["pr_body.py", "r.json", "t", "b"]) == 2


def test_main_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("INPUT_PROBLEM_ID", "pid")
    monkeypatch.setenv("INPUT_WINDOW_START", "2026-10-05T08:00:00Z")
    monkeypatch.setenv("INPUT_WINDOW_END", "2026-10-05T09:00:00Z")
    (tmp_path / "result.json").write_text(json.dumps({"title": "t", "summary": "s"}))
    (tmp_path / "patch.diff").write_text(TWO_SECTIONS)
    (tmp_path / "open.json").write_text(json.dumps([pr(31, "src/a.cs")]))
    args = [str(tmp_path / n) for n in ("result.json", "title.txt", "body.md", "patch.diff", "open.json")]
    assert main(["pr_body.py"] + args) == 0
    assert (tmp_path / "body.md").read_text().startswith(OVERLAP_HEADING)
    (tmp_path / "open.json").unlink()
    assert main(["pr_body.py"] + args) == 0
    assert COULD_NOT_CHECK in (tmp_path / "body.md").read_text()
