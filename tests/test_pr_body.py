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
