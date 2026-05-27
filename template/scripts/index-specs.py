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

import re
import sys
from dataclasses import dataclass
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

    handle = _load_codeowners_handle()
    if handle is not None:
        CODEOWNERS_PATH.write_text(render_codeowners(frs, handle), encoding="utf-8")
        print(f"Wrote {CODEOWNERS_PATH.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
