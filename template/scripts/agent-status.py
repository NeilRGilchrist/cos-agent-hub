#!/usr/bin/env python3
"""
agent-status.py — Print the current state of an FR for an agent starting work.

Run this at the start of every agent session targeting a specific FR.
It prevents two parallel Developers from picking up the same FR and gives
each agent the orientation it needs without hunting through the repo.

Usage:
  python scripts/agent-status.py --fr FR-0013
  python scripts/agent-status.py --all          # Show all open FRs
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(2)


REPO_ROOT = Path(__file__).resolve().parent.parent
SPECS_DIR = REPO_ROOT / "specs"
ESCALATION_LOG = REPO_ROOT / ".agent-team" / "escalation-log.md"

FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def load_fr(fr_id: str) -> tuple[dict, str] | None:
    for path in SPECS_DIR.glob("FR-*.md"):
        text = path.read_text(encoding="utf-8")
        m = FRONTMATTER_PATTERN.match(text)
        if not m:
            continue
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            continue
        if str(meta.get("id", "")) == fr_id:
            return meta, str(path.relative_to(REPO_ROOT))
    return None


def list_open_frs() -> list[tuple[dict, str]]:
    out: list[tuple[dict, str]] = []
    for path in sorted(SPECS_DIR.glob("FR-*.md")):
        text = path.read_text(encoding="utf-8")
        m = FRONTMATTER_PATTERN.match(text)
        if not m:
            continue
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            continue
        if meta.get("status") in {"merged", "deprecated"}:
            continue
        out.append((meta, str(path.relative_to(REPO_ROOT))))
    return out


def recent_escalations(fr_id: str, limit: int = 5) -> list[str]:
    if not ESCALATION_LOG.exists():
        return []
    lines = ESCALATION_LOG.read_text(encoding="utf-8").splitlines()
    matched = [ln for ln in lines if fr_id in ln and ln.strip().startswith("|")]
    return matched[-limit:]


def show_fr(fr_id: str) -> int:
    result = load_fr(fr_id)
    if not result:
        print(f"FR {fr_id} not found in {SPECS_DIR}", file=sys.stderr)
        return 1
    meta, path = result
    print(f"=== {fr_id} ===")
    print(f"Title:      {meta.get('title', '?')}")
    print(f"Status:     {meta.get('status', '?')}")
    print(f"Owner:      {meta.get('owner', '?')}")
    print(f"Path:       {path}")
    deps = meta.get("depends_on") or []
    print(f"Depends on: {', '.join(deps) if deps else '—'}")
    print(f"Updated:    {meta.get('updated', '?')}")
    print()

    escalations = recent_escalations(fr_id)
    if escalations:
        print("Recent escalations:")
        for e in escalations:
            print(f"  {e}")
        print()

    print(f"Read the full FR before starting work: {path}")
    return 0


def show_all() -> int:
    rows = list_open_frs()
    if not rows:
        print("No open FRs.")
        return 0
    print(f"{'ID':<10} {'Status':<14} {'Owner':<12} Title")
    print("-" * 80)
    for meta, _ in rows:
        print(
            f"{str(meta.get('id', '?')):<10} "
            f"{str(meta.get('status', '?')):<14} "
            f"{str(meta.get('owner', '?')):<12} "
            f"{str(meta.get('title', '?'))}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent FR status helper")
    parser.add_argument("--fr", help="FR id (e.g., FR-0013)")
    parser.add_argument("--all", action="store_true", help="List all open FRs")
    args = parser.parse_args()

    if args.all:
        return show_all()
    if args.fr:
        return show_fr(args.fr)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
