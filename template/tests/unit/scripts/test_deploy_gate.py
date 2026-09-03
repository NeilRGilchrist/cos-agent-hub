"""Unit tests for `scripts/deploy-gate.py` — specifically the D1 `--status-only`
hook fix.

`--status-only` is the read-only reporting surface wired to the Stop hook: it
must run the full set of checks and print a summary, but it must NOT rewrite the
tracked `specs/INDEX.md` (which would dirty the working tree on every turn). We
drive the gate through `subprocess` against a self-contained temporary repo so
we can assert on the exit code and on whether the index file is written.

All fixture FR markdown here is synthetic (see `tests/fixtures/README.md`).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# template/tests/unit/scripts/ -> parents[3] == template/
_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
# deploy-gate imports index-specs and spec_footprint, so all three travel.
_COPY_SCRIPTS = ("deploy-gate.py", "index-specs.py", "spec_footprint.py")


def _fr_markdown(fr_id: str, *, status: str = "ready", depends_on: str = "[]") -> str:
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
ac_state: {{}}
created: 2026-01-01
updated: 2026-01-01
---

# {fr_id}: Synthetic

## Acceptance criteria

- **AC-1:** does the first thing
- **AC-2:** does the second thing
"""


def _build_repo(tmp_path: Path, fr_files: dict[str, str]) -> Path:
    """Copy the team scripts into `<tmp>/scripts/` and write FRs under
    `<tmp>/specs/`. The scripts derive their paths from `__file__`, so running
    out of the temp tree keeps the test away from the template's live specs.
    No `tests/` or `src/` dirs are created, so coverage/@implements scans are
    inert no-ops here."""
    (tmp_path / "scripts").mkdir()
    for name in _COPY_SCRIPTS:
        shutil.copy(_SCRIPTS_DIR / name, tmp_path / "scripts" / name)
    specs = tmp_path / "specs"
    specs.mkdir()
    for filename, content in fr_files.items():
        (specs / filename).write_text(content, encoding="utf-8")
    return tmp_path


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(tmp_path / "scripts" / "deploy-gate.py"), *args],
        capture_output=True,
        text=True,
    )


def test_status_only_valid_tree_exits_zero_and_writes_nothing(tmp_path):
    repo = _build_repo(
        tmp_path,
        {"FR-0100-example.md": _fr_markdown("FR-0100", status="ready")},
    )
    proc = _run(repo, "--status-only")
    assert proc.returncode == 0, proc.stderr
    assert "Deploy gate PASSED" in proc.stdout
    assert not (repo / "specs" / "INDEX.md").exists()


def test_plain_run_writes_index(tmp_path):
    """Anchor: without --status-only the gate DOES write INDEX.md, proving the
    'writes nothing' assertion above is a real distinction."""
    repo = _build_repo(
        tmp_path,
        {"FR-0100-example.md": _fr_markdown("FR-0100", status="ready")},
    )
    proc = _run(repo)
    assert proc.returncode == 0, proc.stderr
    index = repo / "specs" / "INDEX.md"
    assert index.exists()
    assert "FR-0100" in index.read_text(encoding="utf-8")


def test_status_only_still_validates_and_fails_on_bad_graph(tmp_path):
    """--status-only is read-only but still runs the checks: an invalid graph
    exits non-zero and writes no index."""
    repo = _build_repo(
        tmp_path,
        {"FR-0100-example.md": _fr_markdown("FR-0100", depends_on="[FR-9999]")},
    )
    proc = _run(repo, "--status-only")
    assert proc.returncode != 0
    assert not (repo / "specs" / "INDEX.md").exists()
