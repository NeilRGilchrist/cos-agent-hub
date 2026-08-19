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
import unicodedata
from pathlib import Path
from typing import TextIO

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(2)


# Documented ASCII equivalents for the typographic characters that appear in FR
# titles (— em dash, → arrow) plus the common relatives, so a stream that cannot
# represent them stays legible rather than dropping characters. Anything not
# listed is folded via Unicode decomposition; nothing becomes '?' or '�'.
_ASCII_FOLD = {
    "—": "-",  # — em dash
    "–": "-",  # – en dash
    "‒": "-",  # ‒ figure dash
    "―": "-",  # ― horizontal bar
    "→": "->",  # → rightwards arrow
    "←": "<-",  # ← leftwards arrow
    "↔": "<->",  # ↔ left-right arrow
    "‘": "'",  # ' left single quote
    "’": "'",  # ' right single quote
    "“": '"',  # " left double quote
    "”": '"',  # " right double quote
    "…": "...",  # … ellipsis
}


def _ascii_fold(text: str) -> str:
    """Fold `text` to ASCII deterministically, without replacement characters.

    Known typographic characters map to their documented equivalents; any
    residual non-ASCII is decomposed (NFKD) and stripped of combining marks so
    accented letters keep their base form. The result always encodes as ASCII.
    """
    for uni, repl in _ASCII_FOLD.items():
        text = text.replace(uni, repl)
    normalized = unicodedata.normalize("NFKD", text)
    return normalized.encode("ascii", "ignore").decode("ascii")


def safe_print(*args: object, sep: str = " ", end: str = "\n", file: TextIO | None = None) -> None:
    """Print like the builtin, but fall back to ASCII folding when the target
    stream's encoding cannot represent a character (e.g. cp1252 + em dash).

    On a UTF-8-capable stream the write path is unchanged, so output stays
    byte-identical to the builtin `print`.
    """
    stream = sys.stdout if file is None else file
    text = sep.join(str(a) for a in args) + end
    try:
        stream.write(text)
    except UnicodeEncodeError:
        stream.write(_ascii_fold(text))


def _configure_stdio() -> None:
    """Best-effort: reconfigure stdout/stderr to UTF-8 so titles print with full
    fidelity on any modern terminal. Where reconfiguration is unavailable, the
    `safe_print` folding path keeps output crash-free and legible.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


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
        safe_print(f"FR {fr_id} not found in {SPECS_DIR}", file=sys.stderr)
        return 1
    meta, path = result
    safe_print(f"=== {fr_id} ===")
    safe_print(f"Title:      {meta.get('title', '?')}")
    safe_print(f"Status:     {meta.get('status', '?')}")
    safe_print(f"Owner:      {meta.get('owner', '?')}")
    safe_print(f"Path:       {path}")
    deps = meta.get("depends_on") or []
    safe_print(f"Depends on: {', '.join(deps) if deps else '—'}")
    safe_print(f"Updated:    {meta.get('updated', '?')}")
    safe_print()

    escalations = recent_escalations(fr_id)
    if escalations:
        safe_print("Recent escalations:")
        for e in escalations:
            safe_print(f"  {e}")
        safe_print()

    safe_print(f"Read the full FR before starting work: {path}")
    return 0


def show_all() -> int:
    rows = list_open_frs()
    if not rows:
        safe_print("No open FRs.")
        return 0
    safe_print(f"{'ID':<10} {'Status':<14} {'Owner':<12} Title")
    safe_print("-" * 80)
    for meta, _ in rows:
        safe_print(
            f"{str(meta.get('id', '?')):<10} "
            f"{str(meta.get('status', '?')):<14} "
            f"{str(meta.get('owner', '?')):<12} "
            f"{str(meta.get('title', '?'))}"
        )
    return 0


def main() -> int:
    _configure_stdio()
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
