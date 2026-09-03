"""Unit tests for `scripts/index-specs.py`.

The module filename has a hyphen (`index-specs.py`) so it cannot be imported
with a plain `import index_specs`. We load it the same way `deploy-gate.py`
does — via `importlib.util.spec_from_file_location` — and drive its pure
helpers directly. The CLI surfaces (`--validate`, `--ac-hashes`) are exercised
through `subprocess` against a self-contained temporary `specs/` tree so we can
assert on the process exit code and prove that no tracked file is written.

Covers the Lockstep N-4 ratification engine and the D3 `--validate` hook fix.
All fixture FR markdown here is synthetic — generated, never derived from real
records (see `tests/fixtures/README.md`).
"""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

# template/tests/unit/scripts/ -> parents[3] == template/
_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
# Scripts that must travel together into a temp repo for the CLI tests.
_COPY_SCRIPTS = ("index-specs.py", "deploy-gate.py", "spec_footprint.py")


def _load_index_specs():
    """Load `index-specs.py` (hyphenated) as an importable module object."""
    spec = importlib.util.spec_from_file_location(
        "index_specs_under_test", _SCRIPTS_DIR / "index-specs.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


index_specs = _load_index_specs()


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _fr_markdown(
    fr_id: str,
    *,
    status: str = "ready",
    acs: list[str] | None = None,
    ac_state_yaml: str = "",
    depends_on: str = "[]",
    extra_body: str = "",
) -> str:
    """Build a synthetic FR markdown document.

    `acs` is a list of AC body strings rendered as `- **AC-N:** <text>` bullets.
    `ac_state_yaml` is spliced verbatim into frontmatter (already indented).
    """
    acs = acs or ["does the first thing", "does the second thing"]
    ac_lines = "\n".join(f"- **AC-{i}:** {text}" for i, text in enumerate(acs, start=1))
    ac_state_line = f"ac_state:\n{ac_state_yaml}" if ac_state_yaml else "ac_state: {}"
    return f"""---
id: {fr_id}
title: Synthetic {fr_id}
status: {status}
owner: architect
depends_on: {depends_on}
tags: []
owns: []
reads: []
derived_from: null
pattern: null
{ac_state_line}
created: 2026-01-01
updated: 2026-01-01
---

# {fr_id}: Synthetic

## Why

Synthetic FR for exercising the indexer.

## Acceptance criteria

{ac_lines}
{extra_body}
"""


def _build_repo(tmp_path: Path, fr_files: dict[str, str]) -> Path:
    """Materialise a self-contained repo: copies the team scripts into
    `<tmp>/scripts/` and writes each FR under `<tmp>/specs/`. The scripts derive
    every path from `__file__`, so running them out of the temp tree keeps the
    test hermetic and away from the template's live specs."""
    (tmp_path / "scripts").mkdir()
    for name in _COPY_SCRIPTS:
        shutil.copy(_SCRIPTS_DIR / name, tmp_path / "scripts" / name)
    specs = tmp_path / "specs"
    specs.mkdir()
    for filename, content in fr_files.items():
        (specs / filename).write_text(content, encoding="utf-8")
    return tmp_path


def _run(tmp_path: Path, script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(tmp_path / "scripts" / script), *args],
        capture_output=True,
        text=True,
    )


def _make_fr(fr_id: str, acs: dict[int, str], ac_state: dict):
    """Construct an in-memory `FR` for the pure `_ac_state_warnings` checks.
    `path` is unused by those checks, so a dummy Path is fine."""
    return index_specs.FR(
        path=Path(f"{fr_id}.md"),
        id=fr_id,
        title="t",
        status="ready",
        owner="architect",
        depends_on=[],
        tags=[],
        derived_from=None,
        pattern=None,
        created="2026-01-01",
        updated="2026-01-01",
        owns=(),
        reads=(),
        acs=acs,
        ac_state=ac_state,
    )


# ---------------------------------------------------------------------------
# normalize_ac_text
# ---------------------------------------------------------------------------


def test_normalize_collapses_runs_of_spaces():
    assert index_specs.normalize_ac_text("a   b    c") == "a b c"


def test_normalize_strips_leading_and_trailing_whitespace():
    assert index_specs.normalize_ac_text("   a b   ") == "a b"


def test_normalize_treats_newlines_as_whitespace():
    """A reflowed AC (newlines + indentation) normalises identically to its
    single-line form — the whole point of hashing normalised text."""
    reflowed = "the system\n  returns an error\n\twith a message"
    single = "the system returns an error with a message"
    assert index_specs.normalize_ac_text(reflowed) == single


def test_normalize_is_idempotent():
    once = index_specs.normalize_ac_text("a  b\n c")
    assert index_specs.normalize_ac_text(once) == once


# ---------------------------------------------------------------------------
# ac_hash
# ---------------------------------------------------------------------------


def test_ac_hash_equal_for_whitespace_variants():
    """Equal normalised text -> equal hash, regardless of spacing/newlines."""
    assert index_specs.ac_hash("a b c") == index_specs.ac_hash("a  b   c")
    assert index_specs.ac_hash("a b c") == index_specs.ac_hash("a\nb\n  c")


def test_ac_hash_changes_when_text_materially_changes():
    assert index_specs.ac_hash("returns error Y") != index_specs.ac_hash(
        "returns error Z"
    )


def test_ac_hash_is_sha256_of_normalized_text():
    text = "  the   system\nreturns  Y "
    expected = hashlib.sha256(
        index_specs.normalize_ac_text(text).encode("utf-8")
    ).hexdigest()
    assert index_specs.ac_hash(text) == expected
    assert len(index_specs.ac_hash(text)) == 64


# ---------------------------------------------------------------------------
# parse_acs
# ---------------------------------------------------------------------------


def test_parse_acs_extracts_numbered_single_line_acs():
    text = _fr_markdown("FR-0100", acs=["first criterion", "second criterion"])
    # parse_acs works on the body after frontmatter, but _section_body only
    # scans `## Acceptance criteria`, so handing it the whole doc is safe.
    acs = index_specs.parse_acs(text)
    assert acs == {1: "first criterion", 2: "second criterion"}


def test_parse_acs_captures_multiline_continuation():
    """A bullet plus its wrapped continuation lines is one AC's full text."""
    body = """## Acceptance criteria

- **AC-1:** first line of the criterion
  continued on a second line
  and a third
- **AC-2:** a single-line criterion

## Out of scope
"""
    acs = index_specs.parse_acs(body)
    assert set(acs) == {1, 2}
    assert acs[1].startswith("first line of the criterion")
    assert "continued on a second line" in acs[1]
    assert "and a third" in acs[1]
    assert acs[2] == "a single-line criterion"


def test_parse_acs_ignores_ac_bullets_outside_the_section():
    """`_section_body` stops at the next `## ` heading; an AC-looking bullet in
    another section must not leak into the parsed ACs."""
    body = """## Acceptance criteria

- **AC-1:** the only real criterion

## Notes

- **AC-9:** this lives under Notes and must be ignored
"""
    acs = index_specs.parse_acs(body)
    assert acs == {1: "the only real criterion"}


def test_parse_acs_empty_when_no_section():
    assert index_specs.parse_acs("# FR with no criteria section\n") == {}


# ---------------------------------------------------------------------------
# _ac_state_warnings
# ---------------------------------------------------------------------------


def test_ac_state_unmanaged_fr_is_silent():
    """No `ac_state` at all -> unmanaged -> no warnings (migration default)."""
    fr = _make_fr("FR-0100", acs={1: "a", 2: "b"}, ac_state={})
    assert index_specs._ac_state_warnings([fr]) == []


def test_ac_state_opted_in_warns_about_uncovered_acs():
    fr = _make_fr(
        "FR-0100",
        acs={1: "a", 2: "b", 3: "c"},
        ac_state={1: {"state": "proposed"}},
    )
    warnings = index_specs._ac_state_warnings([fr])
    assert len(warnings) == 1
    w = warnings[0]
    assert "no ac_state entry" in w
    assert "AC-2" in w and "AC-3" in w
    assert "AC-1" not in w


def test_ac_state_ratified_without_hash_warns():
    fr = _make_fr("FR-0100", acs={1: "a"}, ac_state={1: {"state": "ratified"}})
    warnings = index_specs._ac_state_warnings([fr])
    assert len(warnings) == 1
    assert "ratified but has no recorded hash" in warnings[0]


def test_ac_state_ratified_text_changed_warns_client_review():
    stale_hash = index_specs.ac_hash("the original ratified text")
    fr = _make_fr(
        "FR-0100",
        acs={1: "the text has since been edited"},
        ac_state={1: {"state": "ratified", "hash": stale_hash}},
    )
    warnings = index_specs._ac_state_warnings([fr])
    assert len(warnings) == 1
    assert "ratified text changed since sign-off" in warnings[0]
    assert "client-review" in warnings[0]


def test_ac_state_ratified_matching_hash_is_silent():
    text = "the ratified and unchanged text"
    fr = _make_fr(
        "FR-0100",
        acs={1: text},
        ac_state={1: {"state": "ratified", "hash": index_specs.ac_hash(text)}},
    )
    assert index_specs._ac_state_warnings([fr]) == []


def test_ac_state_non_numeric_key_warns():
    """A non-numeric ac_state key is reported and does not crash int()."""
    fr = _make_fr(
        "FR-0100",
        acs={1: "a"},
        ac_state={
            1: {"state": "ratified", "hash": index_specs.ac_hash("a")},
            "foo": {"state": "proposed"},
        },
    )
    warnings = index_specs._ac_state_warnings([fr])
    assert len(warnings) == 1
    assert "non-numeric key 'foo'" in warnings[0]


def test_ac_state_reference_to_missing_ac_warns():
    fr = _make_fr(
        "FR-0100",
        acs={1: "a"},
        ac_state={
            1: {"state": "ratified", "hash": index_specs.ac_hash("a")},
            5: {"state": "proposed"},
        },
    )
    warnings = index_specs._ac_state_warnings([fr])
    assert len(warnings) == 1
    assert "ac_state references AC-5" in warnings[0]
    assert "no matching" in warnings[0]


def test_ac_state_invalid_state_value_warns():
    fr = _make_fr("FR-0100", acs={1: "a"}, ac_state={1: {"state": "not-a-real-state"}})
    warnings = index_specs._ac_state_warnings([fr])
    assert len(warnings) == 1
    assert "state 'not-a-real-state' not in" in warnings[0]


# ---------------------------------------------------------------------------
# CLI: --validate (D3) and --ac-hashes
# ---------------------------------------------------------------------------


def test_validate_valid_graph_exits_zero_and_writes_nothing(tmp_path):
    repo = _build_repo(
        tmp_path,
        {"FR-0100-example.md": _fr_markdown("FR-0100", status="ready")},
    )
    proc = _run(repo, "index-specs.py", "--validate")
    assert proc.returncode == 0, proc.stderr
    assert "Wrote nothing" in proc.stdout
    assert not (repo / "specs" / "INDEX.md").exists()
    assert not (repo / "CODEOWNERS").exists()


def test_validate_invalid_graph_exits_nonzero(tmp_path):
    # depends_on points at an FR that does not exist -> graph validation fails.
    repo = _build_repo(
        tmp_path,
        {"FR-0100-example.md": _fr_markdown("FR-0100", depends_on="[FR-9999]")},
    )
    proc = _run(repo, "index-specs.py", "--validate")
    assert proc.returncode != 0
    assert "FAILED" in proc.stderr
    assert not (repo / "specs" / "INDEX.md").exists()


def test_plain_run_writes_index(tmp_path):
    """Anchor: without --validate the indexer DOES write INDEX.md, proving the
    'writes nothing' assertions above are a real distinction, not a broken
    harness."""
    repo = _build_repo(
        tmp_path,
        {"FR-0100-example.md": _fr_markdown("FR-0100", status="ready")},
    )
    proc = _run(repo, "index-specs.py")
    assert proc.returncode == 0, proc.stderr
    index = repo / "specs" / "INDEX.md"
    assert index.exists()
    assert "FR-0100" in index.read_text(encoding="utf-8")


def test_ac_hashes_prints_hashes_exits_zero_and_writes_nothing(tmp_path):
    repo = _build_repo(
        tmp_path,
        {
            "FR-0100-example.md": _fr_markdown(
                "FR-0100", acs=["alpha criterion", "beta criterion"]
            )
        },
    )
    proc = _run(repo, "index-specs.py", "--ac-hashes")
    assert proc.returncode == 0, proc.stderr
    assert not (repo / "specs" / "INDEX.md").exists()
    # Hashes printed match the in-process computation for the same AC text.
    expected1 = index_specs.ac_hash("alpha criterion")
    expected2 = index_specs.ac_hash("beta criterion")
    assert f"FR-0100 AC-1: {expected1}" in proc.stdout
    assert f"FR-0100 AC-2: {expected2}" in proc.stdout


def test_ac_hashes_can_target_a_single_fr(tmp_path):
    repo = _build_repo(
        tmp_path,
        {
            "FR-0100-a.md": _fr_markdown("FR-0100", acs=["one"]),
            "FR-0200-b.md": _fr_markdown("FR-0200", acs=["two"]),
        },
    )
    proc = _run(repo, "index-specs.py", "--ac-hashes", "FR-0200")
    assert proc.returncode == 0, proc.stderr
    assert "FR-0200 AC-1:" in proc.stdout
    assert "FR-0100" not in proc.stdout
