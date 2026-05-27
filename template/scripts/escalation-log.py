#!/usr/bin/env python3
"""
escalation-log.py — Append a row to .agent-team/escalation-log.md

Called by the /escalate command to ensure consistent column formatting.

Usage:
  python3 scripts/escalation-log.py append --fr FR-XXXX --role architect \
      --trigger "3 dev-reviewer cycles" --resolution "(pending)"

  python3 scripts/escalation-log.py resolve --fr FR-XXXX --resolution "clarified AC-2"
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = REPO_ROOT / ".agent-team" / "escalation-log.md"

HEADER_LINES = [
    "# Escalation Log",
    "",
    "Append a row whenever an agent escalates to a human. Review weekly.",
    "",
    "| Date | FR | Role | Trigger | Resolution |",
    "|------|----|----|---------|------------|",
]

TABLE_ROW_PATTERN = re.compile(
    r"^\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|$"
)


def ensure_log_exists() -> None:
    if not LOG_PATH.exists():
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(HEADER_LINES) + "\n", encoding="utf-8")


def append_row(date: str, fr: str, role: str, trigger: str, resolution: str) -> None:
    ensure_log_exists()
    row = f"| {date} | {fr} | {role} | {trigger} | {resolution} |"
    text = LOG_PATH.read_text(encoding="utf-8").rstrip("\n")
    text += "\n" + row + "\n"
    LOG_PATH.write_text(text, encoding="utf-8")


def resolve_row(fr: str, resolution: str) -> bool:
    """Update the most recent (pending) row for the given FR with a resolution."""
    ensure_log_exists()
    lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
    updated = False
    for i in range(len(lines) - 1, -1, -1):
        m = TABLE_ROW_PATTERN.match(lines[i])
        if not m:
            continue
        row_fr = m.group(2).strip()
        row_resolution = m.group(4).strip()
        if row_fr == fr and row_resolution == "(pending)":
            date, _, role, trigger, _ = m.groups()
            lines[i] = f"| {date} | {row_fr} | {role} | {trigger} | {resolution} |"
            updated = True
            break
    if updated:
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description="Escalation log helper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_append = sub.add_parser("append", help="Append a new escalation row")
    p_append.add_argument("--fr", required=True, help="FR-XXXX or 'n/a'")
    p_append.add_argument("--role", required=True, help="Architect | Developer | Reviewer")
    p_append.add_argument("--trigger", required=True, help="Escalation trigger")
    p_append.add_argument("--resolution", default="(pending)", help="Resolution (default: pending)")

    p_resolve = sub.add_parser("resolve", help="Resolve the most recent pending escalation for an FR")
    p_resolve.add_argument("--fr", required=True, help="FR-XXXX")
    p_resolve.add_argument("--resolution", required=True, help="Resolution text")

    args = parser.parse_args()

    if args.cmd == "append":
        date = dt.date.today().isoformat()
        append_row(date, args.fr, args.role, args.trigger, args.resolution)
        print(f"Appended escalation for {args.fr} to {LOG_PATH.relative_to(REPO_ROOT)}")
        return 0

    if args.cmd == "resolve":
        if resolve_row(args.fr, args.resolution):
            print(f"Resolved escalation for {args.fr}")
            return 0
        else:
            print(f"No pending escalation found for {args.fr}", file=sys.stderr)
            return 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
