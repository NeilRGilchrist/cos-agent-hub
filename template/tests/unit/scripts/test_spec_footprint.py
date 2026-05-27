"""Unit tests for `scripts/spec_footprint.py`.

Tests the pure-function helpers for FR `owns:`/`reads:` footprint metadata:
parse_footprint, expand_globs, and find_overlaps. Each function has both
happy-path and adversarial tests.
"""

from __future__ import annotations

import pytest

from spec_footprint import (
    Footprint,
    FootprintInvalid,
    FRRecord,
    Overlap,
    expand_globs,
    find_overlaps,
    parse_footprint,
)


# ---------------------------------------------------------------------------
# parse_footprint
# ---------------------------------------------------------------------------


def test_parse_footprint_happy_path_lifts_owns_and_reads():
    """Well-formed frontmatter yields a frozen Footprint."""
    fp = parse_footprint(
        {
            "id": "FR-9999",
            "owns": ["src/foo/**", "src/bar.py"],
            "reads": ["src/baz.py"],
        }
    )
    assert isinstance(fp, Footprint)
    assert fp.owns == ("src/foo/**", "src/bar.py")
    assert fp.reads == ("src/baz.py",)
    # Frozen — assignment must raise.
    with pytest.raises(Exception):
        fp.owns = ("mutated",)  # type: ignore[misc]


def test_parse_footprint_missing_fields_treated_as_empty():
    """`owns:`/`reads:` are optional; missing → empty tuples."""
    fp = parse_footprint({"id": "FR-9999"})
    assert fp.owns == ()
    assert fp.reads == ()


def test_parse_footprint_rejects_string_instead_of_list():
    """a bare string under `owns:` must raise FootprintInvalid."""
    with pytest.raises(FootprintInvalid) as exc:
        parse_footprint({"id": "FR-9999", "owns": "src/foo.py"})
    msg = str(exc.value)
    assert "FR-9999" in msg
    assert "owns" in msg


def test_parse_footprint_rejects_absolute_path():
    """Absolute globs are rejected; globs must be repo-relative."""
    with pytest.raises(FootprintInvalid) as exc:
        parse_footprint({"id": "FR-9999", "owns": ["/abs/path"]})
    msg = str(exc.value)
    assert "FR-9999" in msg
    assert "/abs/path" in msg
    assert "absolute" in msg.lower() or "repo-relative" in msg.lower()


def test_parse_footprint_rejects_dotdot_traversal():
    """`..` segment (parent traversal) is rejected."""
    with pytest.raises(FootprintInvalid) as exc:
        parse_footprint({"id": "FR-9999", "owns": ["../escape"]})
    msg = str(exc.value)
    assert "FR-9999" in msg
    assert ".." in msg


def test_parse_footprint_rejects_non_string_entry():
    """a non-string list entry must raise; the spec says
    'a present field MUST be a YAML list of strings'."""
    with pytest.raises(FootprintInvalid):
        parse_footprint({"id": "FR-9999", "owns": [123]})


def test_parse_footprint_rejects_dotdot_in_middle_segment():
    """`..` anywhere in the path, not just leading, must be rejected.
    A naive implementation that only checks `startswith('..')` would let
    `src/../escape` through; the spec rejects '`..` segments' generally."""
    with pytest.raises(FootprintInvalid):
        parse_footprint({"id": "FR-9999", "owns": ["src/../escape"]})


# ---------------------------------------------------------------------------
# expand_globs
# ---------------------------------------------------------------------------


def test_expand_globs_double_star_matches_multi_segment():
    """`**` matches across path separators."""
    files = [
        "src/foo/bar.py",
        "src/foo/sub/baz.py",
        "src/foo/sub/deeper/qux.py",
        "src/other/x.py",
    ]
    matched = expand_globs(["src/foo/**"], files)
    assert matched == frozenset(
        {
            "src/foo/bar.py",
            "src/foo/sub/baz.py",
            "src/foo/sub/deeper/qux.py",
        }
    )


def test_expand_globs_single_star_does_not_cross_path_separator():
    """`*` matches within a single segment ONLY.

    `src/*/x.py` matches `src/a/x.py` but NOT `src/a/b/x.py` — a naive
    `fnmatch.fnmatch` implementation collapses these, which is wrong."""
    files = [
        "src/a/x.py",
        "src/a/b/x.py",  # Two segments after src/ — must NOT match `src/*/x.py`.
        "src/other.py",
    ]
    matched = expand_globs(["src/*/x.py"], files)
    assert matched == frozenset({"src/a/x.py"})


def test_expand_globs_question_mark_matches_one_non_separator_char():
    """`?` matches exactly one non-separator character."""
    files = ["src/ab.py", "src/a.py", "src/abc.py", "src/a/b.py"]
    matched = expand_globs(["src/a?.py"], files)
    assert matched == frozenset({"src/ab.py"})


def test_expand_globs_exact_filename_match():
    """a non-glob string matches an exact path."""
    files = ["src/extraction/config.py", "src/extraction/configs/tenant_a.yaml"]
    matched = expand_globs(["src/extraction/config.py"], files)
    assert matched == frozenset({"src/extraction/config.py"})


def test_expand_globs_empty_glob_list_returns_empty():
    """zero globs = zero matches (not 'match everything')."""
    files = ["src/a.py", "src/b.py"]
    assert expand_globs([], files) == frozenset()


def test_expand_globs_normalises_windows_paths():
    """caller may hand us Windows-style paths; the function
    must POSIX-normalise to keep glob matching deterministic across platforms."""
    files = ["src\\foo\\bar.py"]
    matched = expand_globs(["src/foo/**"], files)
    assert matched == frozenset({"src/foo/bar.py"})


# ---------------------------------------------------------------------------
# find_overlaps
# ---------------------------------------------------------------------------


def _record(fr_id: str, status: str, owns=(), reads=()):
    return FRRecord(
        id=fr_id,
        status=status,
        footprint=Footprint(owns=tuple(owns), reads=tuple(reads)),
    )


def test_find_overlaps_disjoint_ready_set_returns_no_overlaps():
    """A set of FRs with non-overlapping owns: globs reports no overlaps."""
    files = [
        "src/ingestion/adapters/transcript/granola.py",
        "src/extraction/config.py",
        "src/extraction/configs/acme.yaml",
        "src/extraction/agent/runner.py",
        "src/projection/cypher.py",
    ]
    frs = [
        _record("FR-0006", "ready", owns=["src/ingestion/adapters/transcript/**"]),
        _record(
            "FR-0008",
            "ready",
            owns=["src/extraction/config.py", "src/extraction/configs/**"],
        ),
        _record("FR-0009", "ready", owns=["src/extraction/agent/**"]),
        _record("FR-0010", "ready", owns=["src/projection/**"]),
    ]
    assert find_overlaps(frs, repo_files=files) == []


def test_find_overlaps_synthetic_collision_two_frs_same_glob():
    """two active FRs both claiming `src/shared/**` collide."""
    files = ["src/shared/util.py", "src/shared/other.py", "src/unrelated.py"]
    frs = [
        _record("FR-9001", "ready", owns=["src/shared/**"]),
        _record("FR-9002", "in-progress", owns=["src/shared/**"]),
    ]
    overlaps = find_overlaps(frs, repo_files=files)
    assert len(overlaps) == 1
    o = overlaps[0]
    assert o.fr_a == "FR-9001"
    assert o.fr_b == "FR-9002"
    assert o.shared_files == frozenset({"src/shared/util.py", "src/shared/other.py"})


def test_find_overlaps_excludes_merged_frs():
    """a merged FR cannot collide with anything by definition.

    Spec: 'Overlap detection is restricted to FRs in {ready, in-progress, in-review}'.
    A naive implementation that didn't filter status would flag this case."""
    files = ["src/shared/util.py"]
    frs = [
        _record("FR-9001", "ready", owns=["src/shared/**"]),
        _record("FR-9002", "merged", owns=["src/shared/**"]),
        _record("FR-9003", "deployed", owns=["src/shared/**"]),
        _record("FR-9004", "draft", owns=["src/shared/**"]),
    ]
    assert find_overlaps(frs, repo_files=files) == []


def test_find_overlaps_partial_glob_intersection_detected():
    """overlap is by *expanded files*, not by glob string equality.

    `src/extraction/**` and `src/extraction/agent/runner.py` are different globs but
    their expanded sets overlap on the runner file. A naive string-equality check
    would miss this; the spec demands set-intersection on expand_globs output."""
    files = ["src/extraction/agent/runner.py", "src/extraction/config.py"]
    frs = [
        _record("FR-9001", "ready", owns=["src/extraction/**"]),
        _record("FR-9002", "ready", owns=["src/extraction/agent/runner.py"]),
    ]
    overlaps = find_overlaps(frs, repo_files=files)
    assert len(overlaps) == 1
    assert overlaps[0].shared_files == frozenset({"src/extraction/agent/runner.py"})


def test_find_overlaps_deterministic_ordering():
    """output is sorted by (fr_a, fr_b) regardless of input order.
    A naive iteration order would produce non-deterministic INDEX.md diffs."""
    files = ["src/x/a.py"]
    frs = [
        _record("FR-9003", "ready", owns=["src/x/**"]),
        _record("FR-9001", "ready", owns=["src/x/**"]),
        _record("FR-9002", "ready", owns=["src/x/**"]),
    ]
    overlaps = find_overlaps(frs, repo_files=files)
    pairs = [(o.fr_a, o.fr_b) for o in overlaps]
    assert pairs == sorted(pairs)
    # Three FRs, all overlapping → C(3,2) = 3 pairs.
    assert pairs == [
        ("FR-9001", "FR-9002"),
        ("FR-9001", "FR-9003"),
        ("FR-9002", "FR-9003"),
    ]


def test_find_overlaps_empty_owns_does_not_collide():
    """an FR with empty `owns:` cannot collide with anything.
    Otherwise every empty-footprint pair would degenerately 'overlap' on ∅."""
    files = ["src/a.py"]
    frs = [
        _record("FR-9001", "ready", owns=[]),
        _record("FR-9002", "ready", owns=[]),
        _record("FR-9003", "ready", owns=["src/a.py"]),
    ]
    assert find_overlaps(frs, repo_files=files) == []


def test_overlap_dataclass_is_frozen_and_hashable():
    """`Overlap` is declared frozen — instances must be hashable
    (so the indexer can dedupe / set-intersect if needed)."""
    o = Overlap(fr_a="FR-9001", fr_b="FR-9002", shared_files=frozenset({"src/a.py"}))
    {o}  # smoke-test hashability
    with pytest.raises(Exception):
        o.fr_a = "FR-OTHER"  # type: ignore[misc]
