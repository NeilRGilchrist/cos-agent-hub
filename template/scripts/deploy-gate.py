#!/usr/bin/env python3
"""
deploy-gate.py — Pre-merge gate for agent-driven PRs

Verifies the spec graph and traceability invariants that the agent workflow
depends on. Designed to run in CI (and locally before declaring work done).

Checks performed:

1. Spec graph validates (delegates to index-specs.py parsing)
2. Every FR with status in-review or merged has at least one test annotated
   with @covers FR-XXXX:AC-Y for every AC in the FR
3. Every @covers annotation in tests/ references a real FR and AC
4. No orphan @covers (referencing deleted FRs)
5. @implements FR-XXXX tags in src/ are cross-referenced against the spec
   graph; FRs in dev or later status without any @implements tag produce
   warnings (not failures)

Exits 0 on pass, 1 on gate failure, 2 on script error.

Usage:
  python3 scripts/deploy-gate.py              # Full check
  python3 scripts/deploy-gate.py --stage dev  # Lighter check for Developer stage
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

# Import shared parsing from the sibling index-specs module to avoid
# duplicating frontmatter/YAML parsing logic (DRY).
import importlib.util as _ilu

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

def _import_from(filename: str, module_name: str):  # type: ignore[no-untyped-def]
    spec = _ilu.spec_from_file_location(module_name, _SCRIPTS_DIR / filename)
    mod = _ilu.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod

_index_specs = _import_from("index-specs.py", "index_specs")
collect_frs = _index_specs.collect_frs
validate_graph = _index_specs.validate_graph
render_index = _index_specs.render_index
FR = _index_specs.FR
INDEX_PATH = _index_specs.INDEX_PATH

from spec_footprint import _git_ls_files  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parent.parent
SPECS_DIR = REPO_ROOT / "specs"
TESTS_DIR = REPO_ROOT / "tests"
SRC_DIR = REPO_ROOT / "src"

# AC detection: matches **AC-1:**, AC-1:, **AC-1**:, and list-prefixed variants
AC_HEADING_PATTERN = re.compile(
    r"(?:^[\s*-]*)"           # optional leading whitespace, bullet, or bold marker
    r"(?:\*\*\s*)?"           # optional opening bold
    r"AC-(\d+)"              # the AC number (captured)
    r"(?:\s*\*\*)?"           # optional closing bold
    r"\s*:",                  # colon (possibly with space before)
    re.MULTILINE,
)
# Loose detector for "AC-" mentions that don't match the structured pattern
AC_LOOSE_PATTERN = re.compile(r"\bAC-\d+\b")

COVERS_PATTERN = re.compile(r"@covers\s+(FR-\d{4}):(AC-\d+)")
IMPLEMENTS_PATTERN = re.compile(r"@implements\s+(FR-\d{4})")

AC_NUM_PATTERN = re.compile(r"AC-(\d+)")

FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# Statuses where @implements is expected (dev-stage and beyond)
IMPLEMENTS_EXPECTED_STATUSES = {
    "in-progress", "in-review", "merged",
}

SOURCE_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt"}


@dataclass
class FRSummary:
    id: str
    status: str
    acs: set[int]
    path: Path


def _load_fr_summaries(frs: list[FR]) -> dict[str, FRSummary]:
    """Convert imported FR objects to FRSummary with AC extraction from body."""
    summaries: dict[str, FRSummary] = {}
    for fr in frs:
        text = fr.path.read_text(encoding="utf-8")
        match = FRONTMATTER_PATTERN.match(text)
        body = text[match.end():] if match else text
        ac_nums = {int(m.group(1)) for m in AC_HEADING_PATTERN.finditer(body)}

        # Warn if body contains AC- references not matched by the structured pattern
        all_ac_mentions = {m.group(0) for m in AC_LOOSE_PATTERN.finditer(body)}
        detected_strs = {f"AC-{n}" for n in ac_nums}
        undetected = all_ac_mentions - detected_strs
        if undetected:
            print(
                f"WARN: {fr.path.name} contains AC references in unexpected format: "
                f"{', '.join(sorted(undetected))} — consider using '**AC-N:**' format",
                file=sys.stderr,
            )

        summaries[fr.id] = FRSummary(id=fr.id, status=fr.status, acs=ac_nums, path=fr.path)
    return summaries


def collect_covers() -> dict[str, set[str]]:
    """Return mapping of FR-id -> set of AC-N strings covered by tests."""
    covers: dict[str, set[str]] = defaultdict(set)
    if not TESTS_DIR.exists():
        return covers
    for path in TESTS_DIR.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in SOURCE_EXTENSIONS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in COVERS_PATTERN.finditer(text):
            covers[m.group(1)].add(m.group(2))
    return covers


def collect_implements() -> dict[str, list[str]]:
    """Return mapping of FR-id -> list of src/ file paths with @implements."""
    implements: dict[str, list[str]] = defaultdict(list)
    if not SRC_DIR.exists():
        return implements
    for path in SRC_DIR.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in SOURCE_EXTENSIONS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in IMPLEMENTS_PATTERN.finditer(text):
            rel = str(path.relative_to(REPO_ROOT))
            implements[m.group(1)].append(rel)
    return implements


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent deploy gate")
    parser.add_argument(
        "--stage",
        choices=["dev", "review", "full"],
        default="full",
        help="Which stage's checks to run. dev = lightest; full = all.",
    )
    args = parser.parse_args()

    failures: list[str] = []
    warnings: list[str] = []

    # 1. Spec graph validates (using shared parsing — no subprocess)
    parsed_frs, parse_errors = collect_frs()
    graph_errors = validate_graph(parsed_frs)
    all_errors = parse_errors + graph_errors
    if all_errors:
        failures.append("Spec index validation failed:")
        for err in all_errors:
            failures.append(f"  {err}")
        print("\n".join(failures), file=sys.stderr)
        return 1

    # Write INDEX.md as the old subprocess call did
    repo_files = _git_ls_files()
    INDEX_PATH.write_text(render_index(parsed_frs, repo_files), encoding="utf-8")

    frs = _load_fr_summaries(parsed_frs)
    covers = collect_covers()

    # 2. @covers must reference real FRs and ACs (any stage)
    for fr_id, ac_set in covers.items():
        if fr_id not in frs:
            failures.append(
                f"@covers references unknown FR {fr_id} (FR may have been deleted)"
            )
            continue
        for ac in ac_set:
            num_match = AC_NUM_PATTERN.match(ac)
            if not num_match:
                failures.append(f"@covers has malformed AC '{ac}' for {fr_id}")
                continue
            ac_num = int(num_match.group(1))
            if ac_num not in frs[fr_id].acs:
                failures.append(
                    f"@covers references {fr_id}:{ac} but that AC is not in the FR"
                )

    # 3. Coverage check (only for review/full stages)
    if args.stage in {"review", "full"}:
        for fr in frs.values():
            if fr.status not in {"in-review", "merged"}:
                continue
            covered = covers.get(fr.id, set())
            covered_nums = {int(AC_NUM_PATTERN.match(a).group(1)) for a in covered if AC_NUM_PATTERN.match(a)}
            missing = sorted(fr.acs - covered_nums)
            if missing:
                failures.append(
                    f"{fr.id} ({fr.status}): no @covers test for AC-{', AC-'.join(str(n) for n in missing)}"
                )

    # 4. @implements cross-reference (warnings only)
    implements = collect_implements()
    for fr in frs.values():
        if fr.status not in IMPLEMENTS_EXPECTED_STATUSES:
            continue
        if fr.id not in implements:
            warnings.append(
                f"{fr.id} ({fr.status}): no @implements tag found in src/"
            )
    for fr_id in implements:
        if fr_id not in frs:
            warnings.append(
                f"@implements references unknown FR {fr_id} in: "
                + ", ".join(implements[fr_id])
            )

    if warnings:
        print("Deploy gate WARNINGS:", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)

    if failures:
        print("Deploy gate FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print(
        f"Deploy gate PASSED (stage={args.stage}): {len(frs)} FRs, "
        f"{sum(len(v) for v in covers.values())} @covers annotations, "
        f"{sum(len(v) for v in implements.values())} @implements annotations"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
