#!/usr/bin/env python3
"""
patterns.py — Manage hub-level patterns (patterns/PATTERN-*.md).

Pure CRUD over PATTERN records. Pattern *synthesis* (looking at parked ideas
and active FRs to propose new patterns) happens inside the /patterns slash
command using Claude's judgment; this script handles storage of the result.

Usage:
  python scripts/patterns.py propose "<name>" \\
      --description "<paragraph>" \\
      --value "<compounding-value hypothesis>" \\
      [--tags tag1,tag2] \\
      [--instances proj/FR-XXXX,proj/FR-YYYY] \\
      [--ideas IDEA-NNNN,IDEA-MMMM]

  python scripts/patterns.py list [--status STATUS]
  python scripts/patterns.py show PATTERN-NNNN
  python scripts/patterns.py accept PATTERN-NNNN
  python scripts/patterns.py reject PATTERN-NNNN --reason "..."
  python scripts/patterns.py mark-built PATTERN-NNNN --as <project>
  python scripts/patterns.py reindex
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(2)


HUB_ROOT = Path(__file__).resolve().parent.parent
PATTERNS_DIR = HUB_ROOT / "patterns"
INDEX_PATH = PATTERNS_DIR / "INDEX.md"
HUB_INDEX_PATH = HUB_ROOT / "hub" / "PATTERN-INDEX.md"

ID_PATTERN = re.compile(r"^PATTERN-(\d{4})$")
FILE_PATTERN = re.compile(r"^PATTERN-(\d{4})-.+\.md$")
FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

VALID_STATUSES = {"proposed", "accepted", "built", "rejected"}


@dataclass
class Pattern:
    path: Path
    meta: dict
    body: str

    @property
    def id(self) -> str:
        return str(self.meta.get("id", ""))

    @property
    def name(self) -> str:
        return str(self.meta.get("name", ""))

    @property
    def status(self) -> str:
        return str(self.meta.get("status", ""))

    @property
    def tags(self) -> list[str]:
        return list(self.meta.get("tags") or [])

    @property
    def instances(self) -> list[str]:
        return list(self.meta.get("instances") or [])

    @property
    def ideas(self) -> list[str]:
        return list(self.meta.get("ideas") or [])


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:48] or "pattern"


def today() -> str:
    return dt.date.today().isoformat()


def all_patterns() -> list[Pattern]:
    out: list[Pattern] = []
    for path in sorted(PATTERNS_DIR.glob("PATTERN-*.md")):
        text = path.read_text(encoding="utf-8")
        m = FRONTMATTER_PATTERN.match(text)
        if not m:
            continue
        meta = yaml.safe_load(m.group(1)) or {}
        out.append(Pattern(path=path, meta=meta, body=text[m.end():]))
    return out


def load_pattern(pattern_id: str) -> Pattern:
    if not ID_PATTERN.match(pattern_id):
        raise SystemExit(f"ERROR: '{pattern_id}' is not a valid PATTERN id (PATTERN-NNNN)")
    for p in all_patterns():
        if p.id == pattern_id:
            return p
    raise SystemExit(f"ERROR: {pattern_id} not found in {PATTERNS_DIR}")


def write_pattern(p: Pattern) -> None:
    p.meta["updated"] = today()
    fm = yaml.safe_dump(p.meta, sort_keys=False, allow_unicode=True).rstrip()
    text = f"---\n{fm}\n---\n{p.body}"
    p.path.write_text(text, encoding="utf-8")


def next_id() -> str:
    used: set[int] = set()
    for path in PATTERNS_DIR.glob("PATTERN-*.md"):
        m = FILE_PATTERN.match(path.name)
        if m:
            used.add(int(m.group(1)))
    n = max(used) + 1 if used else 1
    return f"PATTERN-{n:04d}"


# ---------- Commands ----------

def cmd_propose(args: argparse.Namespace) -> int:
    name = args.name.strip()
    if not name:
        print("ERROR: name is required", file=sys.stderr)
        return 1

    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    instances = [s.strip() for s in (args.instances or "").split(",") if s.strip()]
    ideas = [s.strip() for s in (args.ideas or "").split(",") if s.strip()]

    pattern_id = next_id()
    slug = slugify(name)
    path = PATTERNS_DIR / f"{pattern_id}-{slug}.md"

    meta = {
        "id": pattern_id,
        "name": name,
        "status": "proposed",
        "tags": tags,
        "created": today(),
        "updated": today(),
        "instances": instances,
        "ideas": ideas,
        "rejection_reason": None,
    }

    body_parts = [
        f"\n# {pattern_id}: {name}\n",
        "## Description\n",
        f"{args.description or '(fill in)'}\n",
        "## Compounding-value hypothesis\n",
        f"{args.value or '(fill in — required before /patterns accept will succeed)'}\n",
        "## Constituent signals\n",
    ]
    if instances or ideas:
        for inst in instances:
            body_parts.append(f"- {inst} — (fill in: how this is an instance)")
        for idea in ideas:
            body_parts.append(f"- {idea} — (fill in: how this fits)")
        body_parts.append("")
    else:
        body_parts.append("(none yet)\n")

    body_parts += [
        "## Proposed shape\n",
        "(fill in)\n",
        "## Alternatives considered\n",
        "- **Don't build it.** Cost: (fill in)\n",
        "## Notes\n",
        "(none)\n",
    ]

    fm = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).rstrip()
    text = f"---\n{fm}\n---\n" + "\n".join(body_parts)
    path.write_text(text, encoding="utf-8")
    print(f"Created {path.relative_to(HUB_ROOT)}")
    reindex()
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    patterns = all_patterns()
    if args.status:
        patterns = [p for p in patterns if p.status == args.status]
    if not patterns:
        print("(no patterns match)")
        return 0
    print(f"{'ID':<14} {'Status':<10} {'Tags':<24} Name")
    print("-" * 80)
    for p in sorted(patterns, key=lambda x: x.id):
        tags = ",".join(p.tags) if p.tags else "—"
        print(f"{p.id:<14} {p.status:<10} {tags:<24} {p.name}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    p = load_pattern(args.pattern_id)
    print(p.path.read_text(encoding="utf-8"))
    return 0


def cmd_accept(args: argparse.Namespace) -> int:
    p = load_pattern(args.pattern_id)
    if p.status not in {"proposed", "rejected"}:
        print(f"ERROR: {p.id} is '{p.status}', cannot accept", file=sys.stderr)
        return 1
    p.meta["status"] = "accepted"
    p.meta["rejection_reason"] = None
    write_pattern(p)
    print(f"{p.id} accepted")
    reindex()
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    p = load_pattern(args.pattern_id)
    p.meta["status"] = "rejected"
    p.meta["rejection_reason"] = args.reason
    write_pattern(p)
    print(f"{p.id} rejected: {args.reason}")
    reindex()
    return 0


def cmd_mark_built(args: argparse.Namespace) -> int:
    p = load_pattern(args.pattern_id)
    p.meta["status"] = "built"
    p.meta["built_as"] = args.as_project
    write_pattern(p)
    print(f"{p.id} marked built as {args.as_project}")
    reindex()
    return 0


def reindex() -> None:
    patterns = sorted(all_patterns(), key=lambda x: x.id)
    lines = [
        "# Pattern Index",
        "",
        "_Auto-generated by `scripts/patterns.py reindex`. Do not edit by hand._",
        "",
        "| ID | Name | Status | Tags | Instances | Ideas | Updated |",
        "|----|------|--------|------|-----------|-------|---------|",
    ]
    for p in patterns:
        tags = ", ".join(p.tags) if p.tags else "—"
        instances = ", ".join(p.instances) if p.instances else "—"
        ideas = ", ".join(p.ideas) if p.ideas else "—"
        link = f"[{p.id}]({p.path.name})"
        lines.append(
            f"| {link} | {p.name} | `{p.status}` | {tags} | {instances} | {ideas} "
            f"| {p.meta.get('updated', '?')} |"
        )
    if not patterns:
        lines.append("")
        lines.append("_Empty. Run `/patterns` to surface candidates._")
    out = "\n".join(lines) + "\n"
    INDEX_PATH.write_text(out, encoding="utf-8")
    # Mirror to hub/PATTERN-INDEX.md so the hub directory has a single overview.
    HUB_INDEX_PATH.write_text(out, encoding="utf-8")


def cmd_reindex(_: argparse.Namespace) -> int:
    reindex()
    print(f"Wrote {INDEX_PATH.relative_to(HUB_ROOT)} and {HUB_INDEX_PATH.relative_to(HUB_ROOT)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Pattern catalog CRUD")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_propose = sub.add_parser("propose")
    p_propose.add_argument("name")
    p_propose.add_argument("--description")
    p_propose.add_argument("--value", help="compounding-value hypothesis")
    p_propose.add_argument("--tags", help="comma-separated")
    p_propose.add_argument("--instances", help="comma-separated <project>/FR-NNNN")
    p_propose.add_argument("--ideas", help="comma-separated IDEA-NNNN")
    p_propose.set_defaults(func=cmd_propose)

    p_list = sub.add_parser("list")
    p_list.add_argument("--status", choices=sorted(VALID_STATUSES))
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show")
    p_show.add_argument("pattern_id")
    p_show.set_defaults(func=cmd_show)

    p_accept = sub.add_parser("accept")
    p_accept.add_argument("pattern_id")
    p_accept.set_defaults(func=cmd_accept)

    p_reject = sub.add_parser("reject")
    p_reject.add_argument("pattern_id")
    p_reject.add_argument("--reason", required=True)
    p_reject.set_defaults(func=cmd_reject)

    p_built = sub.add_parser("mark-built")
    p_built.add_argument("pattern_id")
    p_built.add_argument("--as", dest="as_project", required=True)
    p_built.set_defaults(func=cmd_mark_built)

    p_reindex = sub.add_parser("reindex")
    p_reindex.set_defaults(func=cmd_reindex)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
