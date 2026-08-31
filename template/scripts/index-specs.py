#!/usr/bin/env python3
"""
index-specs.py — Build a flat index of all FRs in specs/

Reads YAML frontmatter from every specs/FR-*.md file and writes a Markdown
table to specs/INDEX.md showing id, title, status, owner, and dependencies.

Also performs basic validation:
- No duplicate FR IDs
- All depends_on references resolve to existing FRs
- Required frontmatter fields are present

Exits 0 on success, non-zero on validation failure (so it can run in CI).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

# spec_footprint is a sibling module providing pure functions for FR footprint
# metadata (owns:/reads: globs), overlap reporting, and CODEOWNERS emission.
# This script orchestrates I/O (file reads, git ls-files, file writes).
from spec_footprint import (
    FRRecord,
    Footprint,
    FootprintInvalid,
    find_overlaps,
    parse_footprint,
    _git_ls_files,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
SPECS_DIR = REPO_ROOT / "specs"
INDEX_PATH = SPECS_DIR / "INDEX.md"
CODEOWNERS_PATH = REPO_ROOT / "CODEOWNERS"
CODEOWNERS_CONFIG_PATH = REPO_ROOT / ".agent-team" / "codeowners-config.yaml"

REQUIRED_FIELDS = {"id", "title", "status", "owner", "created", "updated"}
VALID_STATUSES = {
    "draft",
    "ready",
    "in-progress",
    "in-review",
    "merged",
    "deprecated",
    "blocked",
}

FR_FILE_PATTERN = re.compile(r"^FR-\d{4}-.+\.md$")
FR_ID_PATTERN = re.compile(r"^FR-\d{4}$")
FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
AC_LINE_PATTERN = re.compile(r"^-\s+\*\*AC-(\d+):\*\*\s*(.*)$")

# Per-AC ratification lifecycle (Lockstep N-4). A ratified AC whose text later
# changes is drift — reported as needing re-review (warn-only for now).
AC_STATES = {"proposed", "refined", "client-review", "ratified", "superseded"}


@dataclass
class FR:
    path: Path
    id: str
    title: str
    status: str
    owner: str
    depends_on: list[str]
    tags: list[str]
    derived_from: str | None
    pattern: str | None
    created: str
    updated: str
    owns: tuple[str, ...]
    reads: tuple[str, ...]
    acs: dict[int, str] = field(default_factory=dict)
    ac_state: dict = field(default_factory=dict)


def _section_body(text: str, heading: str) -> str:
    """Return the body of a `## <heading>` section, up to the next `## `."""
    out: list[str] = []
    in_section = False
    want = heading.strip().lower()
    for line in text.splitlines():
        if line.startswith("## "):
            if in_section:
                break
            in_section = line[3:].strip().lower() == want
            continue
        if in_section:
            out.append(line)
    return "\n".join(out)


def parse_acs(text: str) -> dict[int, str]:
    """Return {AC number: raw text} from the Acceptance criteria section. An AC
    spans its bullet line through any continuation lines up to the next AC
    bullet, so multi-line ACs hash on their full text."""
    acs: dict[int, str] = {}
    current: int | None = None
    buf: list[str] = []
    for line in _section_body(text, "Acceptance criteria").splitlines():
        m = AC_LINE_PATTERN.match(line)
        if m:
            if current is not None:
                acs[current] = "\n".join(buf).strip()
            current = int(m.group(1))
            buf = [m.group(2)]
        elif current is not None:
            buf.append(line)
    if current is not None:
        acs[current] = "\n".join(buf).strip()
    return acs


def normalize_ac_text(text: str) -> str:
    """Whitespace-insensitive normalization, so reflowing an AC does not read as
    a change. This is the contract a recorded `hash` is computed against."""
    return " ".join(text.split())


def ac_hash(text: str) -> str:
    return hashlib.sha256(normalize_ac_text(text).encode("utf-8")).hexdigest()


def parse_fr(path: Path) -> tuple[FR | None, list[str]]:
    """Parse a single FR file. Returns (fr, errors)."""
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_PATTERN.match(text)
    if not match:
        return None, [f"{path.name}: missing YAML frontmatter"]

    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as e:
        return None, [f"{path.name}: invalid YAML frontmatter: {e}"]

    missing = REQUIRED_FIELDS - set(meta.keys())
    if missing:
        errors.append(f"{path.name}: missing required fields: {sorted(missing)}")

    fr_id = str(meta.get("id", ""))
    if not FR_ID_PATTERN.match(fr_id):
        errors.append(f"{path.name}: id '{fr_id}' must match FR-XXXX")

    status = str(meta.get("status", ""))
    if status not in VALID_STATUSES:
        errors.append(f"{path.name}: status '{status}' not in {sorted(VALID_STATUSES)}")

    depends_on = meta.get("depends_on") or []
    if not isinstance(depends_on, list):
        errors.append(f"{path.name}: depends_on must be a list")
        depends_on = []

    tags = meta.get("tags") or []
    if not isinstance(tags, list):
        errors.append(f"{path.name}: tags must be a list (or omitted)")
        tags = []

    derived_from = meta.get("derived_from")
    if derived_from is not None and not str(derived_from).startswith("IDEA-"):
        errors.append(
            f"{path.name}: derived_from must be 'IDEA-NNNN' or null (got '{derived_from}')"
        )

    pattern = meta.get("pattern")
    if pattern is not None and not str(pattern).startswith("PATTERN-"):
        errors.append(
            f"{path.name}: pattern must be 'PATTERN-NNNN' or null (got '{pattern}')"
        )

    try:
        footprint = parse_footprint(meta)
    except FootprintInvalid as e:
        errors.append(f"{path.name}: {e}")
        return None, errors

    if errors:
        return None, errors

    # AC bodies + optional per-AC ratification state (Lockstep N-4). Neither is
    # required and neither can fail the build — ac_state issues are warn-only.
    acs = parse_acs(text[match.end():])
    raw_ac_state = meta.get("ac_state")
    ac_state = raw_ac_state if isinstance(raw_ac_state, dict) else {}

    return (
        FR(
            path=path,
            id=fr_id,
            title=str(meta["title"]),
            status=status,
            owner=str(meta["owner"]),
            depends_on=[str(d) for d in depends_on],
            tags=[str(t) for t in tags],
            derived_from=str(derived_from) if derived_from else None,
            pattern=str(pattern) if pattern else None,
            created=str(meta["created"]),
            updated=str(meta["updated"]),
            owns=footprint.owns,
            reads=footprint.reads,
            acs=acs,
            ac_state=ac_state,
        ),
        [],
    )


def collect_frs() -> tuple[list[FR], list[str]]:
    if not SPECS_DIR.exists():
        return [], [f"specs/ directory not found at {SPECS_DIR}"]

    frs: list[FR] = []
    errors: list[str] = []

    for path in sorted(SPECS_DIR.glob("FR-*.md")):
        if not FR_FILE_PATTERN.match(path.name):
            errors.append(f"{path.name}: filename does not match FR-XXXX-<slug>.md")
            continue
        fr, file_errors = parse_fr(path)
        if file_errors:
            errors.extend(file_errors)
        if fr:
            frs.append(fr)

    return frs, errors


def validate_graph(frs: list[FR]) -> list[str]:
    errors: list[str] = []
    seen_ids: dict[str, Path] = {}
    for fr in frs:
        if fr.id in seen_ids:
            errors.append(
                f"duplicate FR id {fr.id} in {fr.path.name} and {seen_ids[fr.id].name}"
            )
        seen_ids[fr.id] = fr.path

    valid_ids = set(seen_ids.keys())
    for fr in frs:
        for dep in fr.depends_on:
            if dep not in valid_ids:
                errors.append(
                    f"{fr.path.name}: depends_on references unknown FR '{dep}'"
                )

    return errors


def _fmt_globs(globs: tuple[str, ...]) -> str:
    if not globs:
        return "—"
    # Backtick each glob; comma-separate. Markdown table cells have no native
    # list rendering, so this stays one line per FR.
    return ", ".join(f"`{g}`" for g in globs)


def _render_overlap_warnings(frs: list[FR], repo_files: list[str]) -> list[str]:
    records = [
        FRRecord(
            id=fr.id,
            status=fr.status,
            footprint=Footprint(owns=fr.owns, reads=fr.reads),
        )
        for fr in frs
    ]
    overlaps = find_overlaps(records, repo_files=repo_files)
    if not overlaps:
        return []
    lines = ["", "## Footprint warnings", ""]
    for o in overlaps:
        shared = sorted(o.shared_files)
        truncated = shared[:5]
        suffix = ""
        if len(shared) > 5:
            suffix = f", … (+{len(shared) - 5} more)"
        files_md = ", ".join(f"`{f}`" for f in truncated)
        lines.append(f"- **{o.fr_a} ↔ {o.fr_b}**: shared files: {files_md}{suffix}")
    return lines


def _ac_state_warnings(frs: list[FR]) -> list[str]:
    """Warn-only checks on per-AC ratification state (Lockstep N-4).

    Measurement before enforcement (CLAUDE.md rule 1): these never fail the
    build. An FR with no `ac_state` at all is 'unmanaged' and skipped silently —
    that is the migration default for FRs written before ratification tracking.
    Once an FR opts in (any `ac_state`), gaps in its coverage are reported."""
    warnings: list[str] = []
    for fr in sorted(frs, key=lambda f: f.id):
        if not fr.ac_state:
            continue  # unmanaged — no opt-in, no noise
        state_by_n: dict[int, dict] = {}
        for k, v in fr.ac_state.items():
            try:
                state_by_n[int(k)] = v if isinstance(v, dict) else {}
            except (TypeError, ValueError):
                warnings.append(f"{fr.id}: ac_state has a non-numeric key '{k}'")
        for n, entry in sorted(state_by_n.items()):
            if n not in fr.acs:
                warnings.append(
                    f"{fr.id}: ac_state references AC-{n}, which has no matching "
                    "acceptance criterion"
                )
                continue
            state = str(entry.get("state", ""))
            if state not in AC_STATES:
                warnings.append(f"{fr.id} AC-{n}: state '{state}' not in {sorted(AC_STATES)}")
            if state == "ratified":
                stored = entry.get("hash")
                current = ac_hash(fr.acs[n])
                if not stored:
                    warnings.append(
                        f"{fr.id} AC-{n}: ratified but has no recorded hash — "
                        f"record hash: {current}"
                    )
                elif str(stored) != current:
                    warnings.append(
                        f"{fr.id} AC-{n}: ratified text changed since sign-off - needs "
                        f"client-review (recorded {str(stored)[:12]}.., current {current[:12]}..)"
                    )
        unmanaged = sorted(n for n in fr.acs if n not in state_by_n)
        if unmanaged:
            listed = ", ".join(f"AC-{n}" for n in unmanaged)
            warnings.append(f"{fr.id}: no ac_state entry for {listed}")
    return warnings


def _render_ac_state_warnings(frs: list[FR]) -> list[str]:
    warnings = _ac_state_warnings(frs)
    if not warnings:
        return []
    return ["", "## AC ratification warnings", "", *[f"- {w}" for w in warnings]]


def _render_undeclared_section(frs: list[FR]) -> list[str]:
    # Only flag active-status FRs as missing footprint; merged/deprecated/draft
    # don't need to declare. AC-4 treats "footprint not declared" as a soft
    # nudge, not an error.
    active = {"ready", "in-progress", "in-review"}
    undeclared = sorted(
        [fr.id for fr in frs if fr.status in active and not fr.owns],
    )
    if not undeclared:
        return []
    return [
        "",
        "## FRs with undeclared footprint",
        "",
        "_These FRs have empty `owns:` and will not appear in `CODEOWNERS`._",
        "",
        *[f"- {fid}" for fid in undeclared],
    ]


def render_index(frs: list[FR], repo_files: list[str]) -> str:
    lines = [
        "# Spec Index",
        "",
        "_Auto-generated by `scripts/index-specs.py`. Do not edit by hand._",
        "",
        "| ID | Title | Status | Owner | Tags | Owns | Reads | Depends on | Derived from | Updated |",
        "|----|-------|--------|-------|------|------|-------|------------|--------------|---------|",
    ]
    for fr in sorted(frs, key=lambda f: f.id):
        deps = ", ".join(fr.depends_on) if fr.depends_on else "—"
        tags = ", ".join(fr.tags) if fr.tags else "—"
        derived = fr.derived_from if fr.derived_from else "—"
        link = f"[{fr.id}]({fr.path.name})"
        owns_md = _fmt_globs(fr.owns)
        reads_md = _fmt_globs(fr.reads)
        lines.append(
            f"| {link} | {fr.title} | `{fr.status}` | {fr.owner} | {tags} | {owns_md} | {reads_md} | {deps} | {derived} | {fr.updated} |"
        )
    lines.extend(_render_overlap_warnings(frs, repo_files))
    lines.extend(_render_undeclared_section(frs))
    lines.extend(_render_ac_state_warnings(frs))
    lines.append("")
    return "\n".join(lines)


def _load_codeowners_handle() -> str | None:
    """Read the configured GitHub handle, or return None to skip CODEOWNERS emission."""
    if not CODEOWNERS_CONFIG_PATH.exists():
        print(
            f"INFO: {CODEOWNERS_CONFIG_PATH.relative_to(REPO_ROOT).as_posix()} absent; "
            "skipping CODEOWNERS emission.",
            file=sys.stderr,
        )
        return None
    try:
        cfg = yaml.safe_load(CODEOWNERS_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        print(
            f"ERROR: malformed YAML in {CODEOWNERS_CONFIG_PATH.relative_to(REPO_ROOT).as_posix()}: {e}",
            file=sys.stderr,
        )
        return None
    handle = cfg.get("owner_handle")
    if not isinstance(handle, str) or not handle.startswith("@"):
        print(
            f"ERROR: {CODEOWNERS_CONFIG_PATH.relative_to(REPO_ROOT).as_posix()} "
            'must define `owner_handle: "@<github-handle>"`; skipping CODEOWNERS emission.',
            file=sys.stderr,
        )
        return None
    return handle


def render_codeowners(frs: list[FR], handle: str) -> str:
    lines = [
        "# Auto-generated by scripts/index-specs.py — do not edit by hand.",
        "# Source: owns: globs declared in specs/FR-*.md frontmatter.",
        "# Owner handle: read from .agent-team/codeowners-config.yaml (key: owner_handle).",
        "",
    ]
    # Per AC-5: per-FR, sorted by FR ID; within an FR, in declared order.
    # Docs-only FRs (empty `owns:`) are excluded silently per Open Q default.
    for fr in sorted(frs, key=lambda f: f.id):
        if not fr.owns:
            continue
        for glob in fr.owns:
            lines.append(f"{glob} {handle}")
    # Trailing newline so the file is POSIX-friendly and idempotent under
    # editors that auto-append.
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Index specs/ and validate the FR graph.")
    ap.add_argument(
        "--ac-hashes",
        nargs="?",
        const="*",
        metavar="FR-XXXX",
        help=(
            "Print the normalized hash of every acceptance criterion (the value to "
            "record under ac_state.<n>.hash when ratifying) and exit. Optionally "
            "limit to one FR."
        ),
    )
    args = ap.parse_args()

    if args.ac_hashes is not None:
        frs, _ = collect_frs()
        target = None if args.ac_hashes == "*" else args.ac_hashes
        for fr in sorted(frs, key=lambda f: f.id):
            if target and fr.id != target:
                continue
            for n in sorted(fr.acs):
                print(f"{fr.id} AC-{n}: {ac_hash(fr.acs[n])}")
        return 0

    frs, parse_errors = collect_frs()
    graph_errors = validate_graph(frs)
    all_errors = parse_errors + graph_errors

    if all_errors:
        print("Spec validation FAILED:", file=sys.stderr)
        for err in all_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    repo_files = _git_ls_files()

    INDEX_PATH.write_text(render_index(frs, repo_files), encoding="utf-8")
    print(f"Wrote {INDEX_PATH.relative_to(REPO_ROOT)} ({len(frs)} FRs)")

    # Warn-only: surface AC ratification drift on the console too (it is also
    # rendered into INDEX.md). Never changes the exit code — enforcement is a
    # later phase.
    ac_warnings = _ac_state_warnings(frs)
    if ac_warnings:
        print(f"AC ratification warnings ({len(ac_warnings)}):", file=sys.stderr)
        for w in ac_warnings:
            print(f"  - {w}", file=sys.stderr)

    handle = _load_codeowners_handle()
    if handle is not None:
        CODEOWNERS_PATH.write_text(render_codeowners(frs, handle), encoding="utf-8")
        print(f"Wrote {CODEOWNERS_PATH.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
