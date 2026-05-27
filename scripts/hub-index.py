#!/usr/bin/env python3
"""
hub-index.py — Build a denormalized cross-project FR index.

Reads hub/projects.yaml, walks each (non-private) project's specs/ directory,
parses every FR's frontmatter, and writes hub/FR-INDEX.json.

The Chief of Staff slash command, /patterns, and the proactive-overlap check
in /architect query this file rather than walking each project. Re-running is
cheap; run on every relevant slash-command invocation.

By default, .claude/worktrees/ directories are skipped to prevent unbounded
index growth from ephemeral worktrees. Pass --include-worktrees to restore
the old behavior and index worktree specs as well.

Usage:
  python3 scripts/hub-index.py                       # rebuild (main branches only)
  python3 scripts/hub-index.py --include-worktrees    # include .claude/worktrees/*
  python3 scripts/hub-index.py --check                # validate without writing
  python3 scripts/hub-index.py --incremental           # only re-parse changed files
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(2)


HUB_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_YAML = HUB_ROOT / "hub" / "projects.yaml"
INDEX_JSON = HUB_ROOT / "hub" / "FR-INDEX.json"

FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def resolve_project_path(entry: dict) -> Path:
    raw = entry["path"]
    p = Path(raw)
    if not p.is_absolute():
        p = (HUB_ROOT / p).resolve()
    return p


def parse_fr_file(path: Path, project_name: str) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = FRONTMATTER_PATTERN.match(text)
    if not m:
        return None
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return None
    fr_id = str(meta.get("id", ""))
    if not fr_id.startswith("FR-"):
        return None
    return {
        "project": project_name,
        "fr_id": fr_id,
        "title": str(meta.get("title", "")),
        "status": str(meta.get("status", "")),
        "owner": str(meta.get("owner", "")),
        "tags": list(meta.get("tags") or []),
        "depends_on": list(meta.get("depends_on") or []),
        "derived_from": meta.get("derived_from"),
        "pattern": meta.get("pattern"),
        "created": str(meta.get("created", "")),
        "updated": str(meta.get("updated", "")),
        "rel_path": str(path.relative_to(HUB_ROOT)) if path.is_relative_to(HUB_ROOT) else str(path),
    }


def git_tracked_specs(repo_path: Path) -> set[str] | None:
    """Return the set of `specs/FR-*.md` paths tracked in HEAD, or None if not a git repo."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "ls-tree", "-r", "--name-only", "HEAD", "--", "specs"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return {line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()}


def iter_worktrees(project_path: Path) -> list[tuple[str, Path]]:
    """List (branch_label, worktree_path) under <project>/.claude/worktrees/*."""
    wt_root = project_path / ".claude" / "worktrees"
    if not wt_root.exists():
        return []
    out: list[tuple[str, Path]] = []
    for wt_dir in sorted(p for p in wt_root.iterdir() if p.is_dir()):
        out.append((f"claude/{wt_dir.name}", wt_dir))
    return out


def index_specs_dir(
    specs_dir: Path,
    project_name: str,
    branch: str,
    tracked: set[str] | None,
    worktree_path: Path | None,
    ahead_tracked: set[str] | None,
) -> list[dict]:
    """Walk specs/FR-*.md and annotate each record with branch + commit status."""
    records: list[dict] = []
    for fr_path in sorted(specs_dir.glob("FR-*.md")):
        record = parse_fr_file(fr_path, project_name=project_name)
        if record is None:
            continue
        rel_to_repo = f"specs/{fr_path.name}"
        if branch == "main":
            committed = (rel_to_repo in tracked) if tracked is not None else None
            ahead_of_main = False
        else:
            committed = (
                (ahead_tracked is not None and rel_to_repo in ahead_tracked)
            )
            ahead_of_main = bool(committed)
        record["branch"] = branch
        record["committed"] = committed
        record["ahead_of_main"] = ahead_of_main
        record["worktree_path"] = str(worktree_path) if worktree_path is not None else None
        records.append(record)
    return records


def collect_projects() -> tuple[list[dict], list[str]]:
    if not PROJECTS_YAML.exists():
        return [], [f"hub/projects.yaml not found at {PROJECTS_YAML}"]
    try:
        data = yaml.safe_load(PROJECTS_YAML.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        return [], [f"hub/projects.yaml is malformed: {e}"]
    return list(data.get("projects") or []), []


def _load_existing_index() -> dict | None:
    """Load the existing FR-INDEX.json, or return None if missing/corrupt."""
    if not INDEX_JSON.exists():
        return None
    try:
        return json.loads(INDEX_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _file_mtime_iso(path: Path) -> str:
    """Return the file modification time as an ISO string (UTC)."""
    ts = path.stat().st_mtime
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).isoformat(timespec="seconds")


def main() -> int:
    parser = argparse.ArgumentParser(description="Cross-project FR indexer")
    parser.add_argument("--check", action="store_true", help="validate without writing")
    parser.add_argument(
        "--include-worktrees",
        action="store_true",
        help="also index specs from .claude/worktrees/* (skipped by default)",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="only re-parse files modified since the last index run",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="force full rebuild (overrides --incremental)",
    )
    args = parser.parse_args()

    incremental = args.incremental and not args.force
    existing_index = _load_existing_index() if incremental else None
    if incremental and existing_index is None:
        incremental = False

    cutoff_iso: str | None = None
    if incremental and existing_index is not None:
        cutoff_iso = existing_index.get("generated_at")

    projects, errors = collect_projects()
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1

    out_projects: list[dict] = []
    out_frs: list[dict] = []
    skipped_unchanged = 0

    for entry in projects:
        name = entry.get("name", "?")
        project_path = resolve_project_path(entry)
        if not project_path.exists():
            print(f"WARN: project '{name}' path does not exist: {project_path}", file=sys.stderr)
            out_projects.append({**entry, "exists": False, "fr_count": 0})
            continue
        specs_dir = project_path / "specs"
        if not specs_dir.exists():
            print(f"WARN: project '{name}' has no specs/ directory", file=sys.stderr)
            out_projects.append({**entry, "exists": True, "fr_count": 0})
            continue

        is_private = bool(entry.get("private", False))
        if is_private:
            out_projects.append({**entry, "exists": True, "fr_count": 0, "indexed": False})
            continue

        main_tracked = git_tracked_specs(project_path)

        if incremental and cutoff_iso:
            existing_frs_by_key = {}
            if existing_index is not None:
                for fr_rec in existing_index.get("frs", []):
                    if fr_rec.get("project") == name and fr_rec.get("branch") == "main":
                        existing_frs_by_key[fr_rec["fr_id"]] = fr_rec

            project_records: list[dict] = []
            for fr_path in sorted(specs_dir.glob("FR-*.md")):
                mtime = _file_mtime_iso(fr_path)
                fr_id_match = re.match(r"(FR-\d{4})", fr_path.stem)
                fr_id_key = fr_id_match.group(1) if fr_id_match else None
                if mtime <= cutoff_iso and fr_id_key and fr_id_key in existing_frs_by_key:
                    project_records.append(existing_frs_by_key[fr_id_key])
                    skipped_unchanged += 1
                else:
                    record = parse_fr_file(fr_path, project_name=name)
                    if record is None:
                        continue
                    rel_to_repo = f"specs/{fr_path.name}"
                    committed = (rel_to_repo in main_tracked) if main_tracked is not None else None
                    record["branch"] = "main"
                    record["committed"] = committed
                    record["ahead_of_main"] = False
                    record["worktree_path"] = None
                    project_records.append(record)
        else:
            project_records = index_specs_dir(
                specs_dir=specs_dir,
                project_name=name,
                branch="main",
                tracked=main_tracked,
                worktree_path=None,
                ahead_tracked=None,
            )

        worktree_count = 0
        in_flight_count = 0
        if args.include_worktrees:
            worktrees = iter_worktrees(project_path)
            worktree_count = len(worktrees)
            for branch_label, wt_path in worktrees:
                wt_specs = wt_path / "specs"
                if not wt_specs.exists():
                    continue
                ahead_tracked = git_tracked_specs(wt_path)
                wt_records = index_specs_dir(
                    specs_dir=wt_specs,
                    project_name=name,
                    branch=branch_label,
                    tracked=None,
                    worktree_path=wt_path,
                    ahead_tracked=ahead_tracked,
                )
                project_records.extend(wt_records)
                in_flight_count += sum(1 for r in wt_records if not r["committed"])

        out_frs.extend(project_records)
        out_projects.append({
            **entry,
            "exists": True,
            "fr_count": len(project_records),
            "indexed": True,
            "worktree_count": worktree_count,
            "in_flight_fr_count": in_flight_count,
        })

    output = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "projects": out_projects,
        "frs": out_frs,
    }

    if args.check:
        print(f"Hub index check OK: {len(out_projects)} projects, {len(out_frs)} FRs")
        return 0

    INDEX_JSON.parent.mkdir(parents=True, exist_ok=True)
    INDEX_JSON.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    parts = [
        f"Wrote {INDEX_JSON.relative_to(HUB_ROOT)}: "
        f"{len(out_projects)} projects, {len(out_frs)} FRs"
    ]
    if skipped_unchanged:
        parts.append(f" ({skipped_unchanged} unchanged, reused from cache)")
    print("".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
