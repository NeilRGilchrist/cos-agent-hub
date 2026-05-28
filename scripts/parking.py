#!/usr/bin/env python3
"""
parking.py — Manage parked ideas (parking-lot/IDEA-*.md).

Pure CRUD over IDEA records. The slash commands (/park, /promote, /cos) handle
interaction; this script handles storage.

Usage:
  python scripts/parking.py add "<title>" \\
      [--description "<paragraph>"] \\
      [--tags tag1,tag2] \\
      [--size XS|S|M|L] \\
      [--value "<one-sentence value hypothesis>"] \\
      [--context "<originating context>"]

  python scripts/parking.py list [--status STATUS] [--tag TAG]
  python scripts/parking.py show IDEA-NNNN
  python scripts/parking.py promote IDEA-NNNN --to-fr <project>/<fr-id>
  python scripts/parking.py promote IDEA-NNNN --to-project <name>
  python scripts/parking.py merge IDEA-NNNN --into PATTERN-NNNN
  python scripts/parking.py archive IDEA-NNNN --reason "..."
  python scripts/parking.py reflect [--days 90]
  python scripts/parking.py reindex
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
PARKING_DIR = HUB_ROOT / "parking-lot"
INDEX_PATH = PARKING_DIR / "INDEX.md"

ID_PATTERN = re.compile(r"^IDEA-(\d{4})$")
FILE_PATTERN = re.compile(r"^IDEA-(\d{4})-.+\.md$")
FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

VALID_STATUSES = {"parked", "promoted", "merged-into-pattern", "archived"}
VALID_SIZES = {"XS", "S", "M", "L"}


@dataclass
class Idea:
    path: Path
    meta: dict
    body: str

    @property
    def id(self) -> str:
        return str(self.meta.get("id", ""))

    @property
    def title(self) -> str:
        return str(self.meta.get("title", ""))

    @property
    def status(self) -> str:
        return str(self.meta.get("status", ""))

    @property
    def tags(self) -> list[str]:
        return list(self.meta.get("tags") or [])

    @property
    def size(self) -> str:
        return str(self.meta.get("size", "M"))


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:48] or "idea"


def today() -> str:
    return dt.date.today().isoformat()


def load_idea(idea_id: str) -> Idea:
    if not ID_PATTERN.match(idea_id):
        raise SystemExit(f"ERROR: '{idea_id}' is not a valid IDEA id (expected IDEA-NNNN)")
    for path in PARKING_DIR.glob("IDEA-*.md"):
        text = path.read_text(encoding="utf-8")
        m = FRONTMATTER_PATTERN.match(text)
        if not m:
            continue
        meta = yaml.safe_load(m.group(1)) or {}
        if str(meta.get("id", "")) == idea_id:
            return Idea(path=path, meta=meta, body=text[m.end():])
    raise SystemExit(f"ERROR: {idea_id} not found in {PARKING_DIR}")


def write_idea(idea: Idea) -> None:
    idea.meta["updated"] = today()
    fm = yaml.safe_dump(idea.meta, sort_keys=False, allow_unicode=True).rstrip()
    text = f"---\n{fm}\n---\n{idea.body}"
    idea.path.write_text(text, encoding="utf-8")


def all_ideas() -> list[Idea]:
    out: list[Idea] = []
    for path in sorted(PARKING_DIR.glob("IDEA-*.md")):
        text = path.read_text(encoding="utf-8")
        m = FRONTMATTER_PATTERN.match(text)
        if not m:
            continue
        meta = yaml.safe_load(m.group(1)) or {}
        out.append(Idea(path=path, meta=meta, body=text[m.end():]))
    return out


def next_id() -> str:
    used: set[int] = set()
    for path in PARKING_DIR.glob("IDEA-*.md"):
        m = FILE_PATTERN.match(path.name)
        if m:
            used.add(int(m.group(1)))
    n = max(used) + 1 if used else 1
    return f"IDEA-{n:04d}"


# ---------- Commands ----------

def cmd_add(args: argparse.Namespace) -> int:
    title = args.title.strip()
    if not title:
        print("ERROR: title is required", file=sys.stderr)
        return 1

    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    size = (args.size or "M").upper()
    if size not in VALID_SIZES:
        print(f"ERROR: --size must be one of {sorted(VALID_SIZES)}", file=sys.stderr)
        return 1

    idea_id = next_id()
    slug = slugify(title)
    path = PARKING_DIR / f"{idea_id}-{slug}.md"

    meta = {
        "id": idea_id,
        "title": title,
        "status": "parked",
        "tags": tags,
        "size": size,
        "created": today(),
        "updated": today(),
        "last_reviewed": today(),
        "promoted_to": None,
        "pattern": None,
        "archive_reason": None,
    }

    body_parts = [
        f"\n# {idea_id}: {title}\n",
        "## Description\n",
        f"{args.description or '(no description provided)'}\n",
        "## Originating context\n",
        f"{args.context or '(not captured)'}\n",
        "## Value hypothesis\n",
        f"{args.value or '(not articulated yet — fill in before /promote)'}\n",
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
    ideas = all_ideas()
    if args.status:
        ideas = [i for i in ideas if i.status == args.status]
    if args.tag:
        ideas = [i for i in ideas if args.tag in i.tags]
    if not ideas:
        print("(no ideas match)")
        return 0
    print(f"{'ID':<10} {'Status':<22} {'Size':<5} Title")
    print("-" * 80)
    for i in sorted(ideas, key=lambda x: x.id):
        print(f"{i.id:<10} {i.status:<22} {i.size:<5} {i.title}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    idea = load_idea(args.idea_id)
    print(idea.path.read_text(encoding="utf-8"))
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    idea = load_idea(args.idea_id)
    if idea.status != "parked":
        print(f"WARN: {idea.id} is currently '{idea.status}', not 'parked' -- proceeding anyway", file=sys.stderr)

    if args.to_fr:
        idea.meta["promoted_to"] = args.to_fr
        idea.meta["status"] = "promoted"
    elif args.to_project:
        idea.meta["promoted_to"] = f"{args.to_project}/(new project)"
        idea.meta["status"] = "promoted"
    else:
        print("ERROR: --to-fr or --to-project is required", file=sys.stderr)
        return 1

    idea.meta["last_reviewed"] = today()
    write_idea(idea)
    print(f"Marked {idea.id} as promoted -> {idea.meta['promoted_to']}")
    reindex()
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    idea = load_idea(args.idea_id)
    if not args.into.startswith("PATTERN-"):
        print("ERROR: --into must be PATTERN-NNNN", file=sys.stderr)
        return 1
    idea.meta["pattern"] = args.into
    idea.meta["status"] = "merged-into-pattern"
    idea.meta["last_reviewed"] = today()
    write_idea(idea)
    print(f"Marked {idea.id} as merged into {args.into}")
    reindex()
    return 0


def cmd_archive(args: argparse.Namespace) -> int:
    idea = load_idea(args.idea_id)
    idea.meta["status"] = "archived"
    idea.meta["archive_reason"] = args.reason
    idea.meta["last_reviewed"] = today()
    write_idea(idea)
    print(f"Archived {idea.id}: {args.reason}")
    reindex()
    return 0


def cmd_reflect(args: argparse.Namespace) -> int:
    threshold = dt.date.today() - dt.timedelta(days=args.days)
    stale: list[Idea] = []
    for idea in all_ideas():
        if idea.status != "parked":
            continue
        try:
            last = dt.date.fromisoformat(str(idea.meta.get("last_reviewed", "")))
        except ValueError:
            stale.append(idea)
            continue
        if last < threshold:
            stale.append(idea)

    if not stale:
        print(f"No parked ideas are older than {args.days} days. Nothing to reflect on.")
        return 0

    print(f"Parked ideas not reviewed in {args.days}+ days ({len(stale)}):")
    for idea in stale:
        print(f"  {idea.id}  last_reviewed={idea.meta.get('last_reviewed', '?')}  {idea.title}")
    print("\nFor each: re-validate, archive (with reason), or promote. Then run:")
    print("  python scripts/parking.py archive IDEA-NNNN --reason '...'  # or")
    print("  /promote IDEA-NNNN")
    return 0


def reindex() -> None:
    ideas = sorted(all_ideas(), key=lambda i: i.id)
    lines = [
        "# Parking Lot Index",
        "",
        "_Auto-generated by `scripts/parking.py reindex`. Do not edit by hand._",
        "",
        "| ID | Title | Status | Tags | Size | Created | Last reviewed |",
        "|----|-------|--------|------|------|---------|---------------|",
    ]
    for i in ideas:
        tags = ", ".join(i.tags) if i.tags else "—"
        link = f"[{i.id}]({i.path.name})"
        lines.append(
            f"| {link} | {i.title} | `{i.status}` | {tags} | {i.size} "
            f"| {i.meta.get('created', '?')} | {i.meta.get('last_reviewed', '?')} |"
        )
    if not ideas:
        lines.append("")
        lines.append("_Empty. Add ideas with `/park <idea>`._")
    INDEX_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_reindex(_: argparse.Namespace) -> int:
    reindex()
    print(f"Wrote {INDEX_PATH.relative_to(HUB_ROOT)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Parking-lot CRUD")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add")
    p_add.add_argument("title")
    p_add.add_argument("--description")
    p_add.add_argument("--tags", help="comma-separated")
    p_add.add_argument("--size", default="M")
    p_add.add_argument("--value", help="one-sentence value hypothesis")
    p_add.add_argument("--context", help="originating context")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list")
    p_list.add_argument("--status", choices=sorted(VALID_STATUSES))
    p_list.add_argument("--tag")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show")
    p_show.add_argument("idea_id")
    p_show.set_defaults(func=cmd_show)

    p_promote = sub.add_parser("promote")
    p_promote.add_argument("idea_id")
    p_promote.add_argument("--to-fr", help="<project>/<fr-id>")
    p_promote.add_argument("--to-project", help="<project-name>")
    p_promote.set_defaults(func=cmd_promote)

    p_merge = sub.add_parser("merge")
    p_merge.add_argument("idea_id")
    p_merge.add_argument("--into", required=True, help="PATTERN-NNNN")
    p_merge.set_defaults(func=cmd_merge)

    p_archive = sub.add_parser("archive")
    p_archive.add_argument("idea_id")
    p_archive.add_argument("--reason", required=True)
    p_archive.set_defaults(func=cmd_archive)

    p_reflect = sub.add_parser("reflect")
    p_reflect.add_argument("--days", type=int, default=90)
    p_reflect.set_defaults(func=cmd_reflect)

    p_reindex = sub.add_parser("reindex")
    p_reindex.set_defaults(func=cmd_reindex)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
