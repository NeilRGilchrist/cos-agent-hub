#!/usr/bin/env python3
"""
render-docs.py — Render client-facing docs as projections of the spec graph.

Client docs are a *read model*. They are never hand-edited: a doc spec under
`docs/_spec/<doc>.yaml` names the FRs and ADRs that belong to a document (IDs
only — no prose), and this script emits `docs/client/<doc>.md` from the spec
graph. It also writes a render stamp (`docs/_render.json`) and a gap report
(`docs/_gaps.md`).

The four generated sections (all read from the graph):
  1. What you've signed off      — ACs in state `ratified`
  2. Changes since your last review — ACs in state `client-review`
  3. Open questions              — every Open Question + Default, per FR
  4. Decisions and why           — the referenced ADRs

Sections 1 and 2 depend on per-AC ratification state (`ac_state:` frontmatter,
Lockstep N-4). Until that lands they render an explanatory note rather than
failing, so the renderer is useful today.

This is a warn-only lint: gaps are reported to `docs/_gaps.md`, never fatal.
Exits 0 unless a doc spec is itself malformed.

Cross-platform (Windows/macOS/Linux). Requires Python 3.11+ and pyyaml.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(2)


REPO_ROOT = Path(__file__).resolve().parent.parent
SPECS_DIR = REPO_ROOT / "specs"
DECISIONS_DIR = SPECS_DIR / "decisions"
DOCS_DIR = REPO_ROOT / "docs"
SPEC_DIR = DOCS_DIR / "_spec"
CLIENT_DIR = DOCS_DIR / "client"
RENDER_STAMP = DOCS_DIR / "_render.json"
GAPS_PATH = DOCS_DIR / "_gaps.md"

FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
FR_FILE_PATTERN = re.compile(r"^FR-\d{4}-.+\.md$")
ADR_FILE_PATTERN = re.compile(r"^ADR-\d{4}-.+\.md$")
AC_LINE_PATTERN = re.compile(r"^-\s+\*\*AC-(\d+):\*\*\s*(.*)$")
Q_LINE_PATTERN = re.compile(r"^-\s+\*\*Q:\*\*\s*(.*)$")
DEFAULT_LINE_PATTERN = re.compile(r"^\s*-\s+\*\*Default:\*\*\s*(.*)$")

# Statuses that make an FR "active" — an active FR referenced by no doc is a gap.
ACTIVE_STATUSES = {"ready", "in-progress", "in-review", "merged"}
# Statuses that make a referenced FR/ADR a broken reference for a client doc.
BROKEN_FR_STATUSES = {"draft", "deprecated", "blocked"}
BROKEN_ADR_STATUSES = {"proposed", "superseded", "rejected", "deprecated"}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
@dataclass
class FRDoc:
    id: str
    title: str
    status: str
    updated: str
    acs: list[tuple[int, str]] = field(default_factory=list)
    ac_state: dict = field(default_factory=dict)
    open_questions: list[tuple[str, str]] = field(default_factory=list)
    content_hash: str = ""


@dataclass
class ADRDoc:
    id: str
    title: str
    status: str
    summary: str = ""
    content_hash: str = ""


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_frontmatter(text: str) -> dict | None:
    m = FRONTMATTER_PATTERN.match(text)
    if not m:
        return None
    try:
        return yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return None


def _section_lines(text: str, heading: str) -> list[str]:
    """Return the lines of a `## <heading>` section (excluding the heading),
    stopping at the next `## ` heading or EOF."""
    lines = text.splitlines()
    out: list[str] = []
    in_section = False
    want = heading.strip().lower()
    for line in lines:
        if line.startswith("## "):
            if in_section:
                break
            in_section = line[3:].strip().lower() == want
            continue
        if in_section:
            out.append(line)
    return out


def _parse_acs(text: str) -> list[tuple[int, str]]:
    acs: list[tuple[int, str]] = []
    for line in _section_lines(text, "Acceptance criteria"):
        m = AC_LINE_PATTERN.match(line)
        if m:
            acs.append((int(m.group(1)), m.group(2).strip()))
    return acs


def _parse_open_questions(text: str) -> list[tuple[str, str]]:
    qs: list[tuple[str, str]] = []
    pending_q: str | None = None
    for line in _section_lines(text, "Open questions"):
        mq = Q_LINE_PATTERN.match(line)
        if mq:
            pending_q = mq.group(1).strip()
            continue
        md = DEFAULT_LINE_PATTERN.match(line)
        if md and pending_q is not None:
            qs.append((pending_q, md.group(1).strip()))
            pending_q = None
    if pending_q is not None:
        qs.append((pending_q, ""))
    return qs


def _adr_summary(text: str) -> str:
    """Best-effort one-block summary: the Decision section, else the first
    non-empty paragraph of Context."""
    for heading in ("Decision", "Context"):
        body = "\n".join(_section_lines(text, heading)).strip()
        if body:
            # First paragraph only, to keep the client doc bounded.
            para = body.split("\n\n", 1)[0].strip()
            if para:
                return para
    return ""


def load_frs() -> tuple[dict[str, FRDoc], list[str]]:
    frs: dict[str, FRDoc] = {}
    errors: list[str] = []
    if not SPECS_DIR.exists():
        return frs, errors
    for path in sorted(SPECS_DIR.glob("FR-*.md")):
        if not FR_FILE_PATTERN.match(path.name):
            continue
        text = path.read_text(encoding="utf-8")
        meta = _parse_frontmatter(text)
        if meta is None:
            errors.append(f"{path.name}: missing or invalid frontmatter")
            continue
        fr_id = str(meta.get("id", "")).strip()
        if not fr_id:
            errors.append(f"{path.name}: no id in frontmatter")
            continue
        frs[fr_id] = FRDoc(
            id=fr_id,
            title=str(meta.get("title", "")).strip(),
            status=str(meta.get("status", "")).strip(),
            updated=str(meta.get("updated", "")).strip(),
            acs=_parse_acs(text),
            ac_state=meta.get("ac_state") or {},
            open_questions=_parse_open_questions(text),
            content_hash=_content_hash(text),
        )
    return frs, errors


def load_adrs() -> dict[str, ADRDoc]:
    adrs: dict[str, ADRDoc] = {}
    if not DECISIONS_DIR.exists():
        return adrs
    for path in sorted(DECISIONS_DIR.glob("ADR-*.md")):
        if not ADR_FILE_PATTERN.match(path.name):
            continue
        text = path.read_text(encoding="utf-8")
        meta = _parse_frontmatter(text) or {}
        adr_id = str(meta.get("id", "")).strip() or path.name.split("-")[0]
        adrs[adr_id] = ADRDoc(
            id=adr_id,
            title=str(meta.get("title", "")).strip(),
            status=str(meta.get("status", "")).strip(),
            summary=_adr_summary(text),
            content_hash=_content_hash(text),
        )
    return adrs


@dataclass
class DocSpec:
    doc: str
    title: str
    frs: list[str]
    adrs: list[str]
    path: Path


def load_doc_specs(only: str | None) -> tuple[list[DocSpec], list[str]]:
    specs: list[DocSpec] = []
    errors: list[str] = []
    if not SPEC_DIR.exists():
        # Nothing to render is not an error — a project may not use doc specs.
        return specs, errors
    for path in sorted(SPEC_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            errors.append(f"{path.name}: invalid YAML: {e}")
            continue
        doc = str(data.get("doc") or path.stem).strip()
        if only and doc != only:
            continue
        frs = [str(x).strip() for x in (data.get("frs") or [])]
        adrs = [str(x).strip() for x in (data.get("adrs") or [])]
        title = str(data.get("title") or doc).strip()
        specs.append(DocSpec(doc=doc, title=title, frs=frs, adrs=adrs, path=path))
    if only and not specs:
        errors.append(f"no doc spec named '{only}' under {SPEC_DIR}")
    return specs, errors


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _acs_in_state(spec: DocSpec, frs: dict[str, FRDoc], state: str) -> list[str]:
    out: list[str] = []
    for fr_id in spec.frs:
        fr = frs.get(fr_id)
        if not fr:
            continue
        for n, text in fr.acs:
            entry = fr.ac_state.get(n) or fr.ac_state.get(str(n)) or {}
            if entry.get("state") == state:
                out.append(f"- **{fr.id} AC-{n}** — {text}")
    return out


def _any_ac_state(spec: DocSpec, frs: dict[str, FRDoc]) -> bool:
    return any((frs.get(f) and frs[f].ac_state) for f in spec.frs)


def render_doc(
    spec: DocSpec, frs: dict[str, FRDoc], adrs: dict[str, ADRDoc]
) -> tuple[str, dict[str, str]]:
    """Return (markdown, {source_id: content_hash}). The markdown is
    deterministic (no timestamps) so an unchanged graph re-renders byte-for-byte."""
    lines: list[str] = []
    sources: dict[str, str] = {}

    lines.append(
        f"<!-- Generated by scripts/render-docs.py from "
        f"docs/_spec/{spec.path.name} — do not edit by hand. -->"
    )
    lines.append("")
    lines.append(f"# {spec.title}")
    lines.append("")

    # 1. Signed off
    lines.append("## What you've signed off")
    lines.append("")
    ratified = _acs_in_state(spec, frs, "ratified")
    if ratified:
        lines.extend(ratified)
    elif not _any_ac_state(spec, frs):
        lines.append(
            "_Acceptance-criteria sign-off tracking is not enabled for this "
            "project yet. Once it is, the requirements you have approved will "
            "be listed here._"
        )
    else:
        lines.append("_Nothing has been signed off yet._")
    lines.append("")

    # 2. Changes since last review
    lines.append("## Changes since your last review")
    lines.append("")
    review = _acs_in_state(spec, frs, "client-review")
    if review:
        lines.extend(review)
    elif not _any_ac_state(spec, frs):
        lines.append(
            "_No changes are awaiting your review. Items needing your sign-off "
            "will appear here once review tracking is enabled._"
        )
    else:
        lines.append("_Nothing is currently awaiting your review._")
    lines.append("")

    # 3. Open questions
    lines.append("## Open questions")
    lines.append("")
    any_q = False
    for fr_id in spec.frs:
        fr = frs.get(fr_id)
        if fr:
            sources[fr.id] = fr.content_hash
        if not fr or not fr.open_questions:
            continue
        any_q = True
        heading = f"{fr.id}: {fr.title}" if fr.title else fr.id
        lines.append(f"### {heading}")
        lines.append("")
        for q, default in fr.open_questions:
            lines.append(f"- **{q}**")
            if default:
                lines.append(f"  - _If we don't hear otherwise:_ {default}")
        lines.append("")
    if not any_q:
        lines.append("_No open questions at this time._")
        lines.append("")

    # 4. Decisions and why
    lines.append("## Decisions and why")
    lines.append("")
    any_adr = False
    for adr_id in spec.adrs:
        adr = adrs.get(adr_id)
        if not adr:
            continue
        sources[adr.id] = adr.content_hash
        any_adr = True
        title = f"{adr.id}: {adr.title}" if adr.title else adr.id
        lines.append(f"### {title}")
        if adr.status:
            lines.append(f"_Status: {adr.status}_")
        lines.append("")
        if adr.summary:
            lines.append(adr.summary)
            lines.append("")
    if not any_adr:
        lines.append("_No decisions have been recorded for this document._")
        lines.append("")

    text = "\n".join(lines).rstrip() + "\n"
    return text, sources


# ---------------------------------------------------------------------------
# Gaps
# ---------------------------------------------------------------------------
def compute_gaps(
    specs: list[DocSpec],
    frs: dict[str, FRDoc],
    adrs: dict[str, ADRDoc],
    prior_stamp: dict,
) -> dict[str, list[str]]:
    broken: list[str] = []
    uncovered: list[str] = []
    stale: list[str] = []

    referenced_frs: set[str] = set()
    for spec in specs:
        for fr_id in spec.frs:
            referenced_frs.add(fr_id)
            fr = frs.get(fr_id)
            if fr is None:
                broken.append(f"doc `{spec.doc}`: FR `{fr_id}` is referenced but no spec file exists.")
            elif fr.status in BROKEN_FR_STATUSES:
                broken.append(f"doc `{spec.doc}`: FR `{fr_id}` is referenced but its status is `{fr.status}`.")
        for adr_id in spec.adrs:
            adr = adrs.get(adr_id)
            if adr is None:
                broken.append(f"doc `{spec.doc}`: ADR `{adr_id}` is referenced but no decision file exists.")
            elif adr.status and adr.status.lower() in BROKEN_ADR_STATUSES:
                broken.append(f"doc `{spec.doc}`: ADR `{adr_id}` is referenced but its status is `{adr.status}`.")

    for fr_id, fr in sorted(frs.items()):
        if fr.status in ACTIVE_STATUSES and fr_id not in referenced_frs:
            uncovered.append(f"FR `{fr_id}` (`{fr.status}`) is referenced by no doc spec.")

    # Staleness: compare current source hashes to the prior render stamp.
    for spec in specs:
        prior = (prior_stamp.get(spec.doc) or {}).get("sources") or {}
        for src_id in spec.frs + spec.adrs:
            src = frs.get(src_id) or adrs.get(src_id)
            if not src:
                continue
            prior_hash = prior.get(src_id)
            if prior_hash and prior_hash != src.content_hash:
                stale.append(f"doc `{spec.doc}`: source `{src_id}` changed since the last render.")

    return {"broken": broken, "uncovered": uncovered, "stale": stale}


def _render_gaps_md(gaps: dict[str, list[str]]) -> str:
    def block(title: str, items: list[str]) -> list[str]:
        out = [f"## {title}", ""]
        out.extend([f"- {it}" for it in items] if items else ["- (none)"])
        out.append("")
        return out

    lines = [
        "# Documentation gaps",
        "",
        "_Generated by `scripts/render-docs.py`. Warn-only — this report never "
        "fails a build. Regenerate with `python scripts/render-docs.py`._",
        "",
    ]
    lines += block("Broken references (missing / draft / deprecated)", gaps["broken"])
    lines += block("Active FRs covered by no doc", gaps["uncovered"])
    lines += block("Stale sections (source changed since last render)", gaps["stale"])
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render client docs from the spec graph.")
    parser.add_argument("doc", nargs="?", help="render only this doc id (default: all)")
    args = parser.parse_args(argv)

    specs, spec_errors = load_doc_specs(args.doc)
    for err in spec_errors:
        print(f"ERROR: {err}", file=sys.stderr)
    if not specs:
        if spec_errors:
            return 1
        print(f"No doc specs under {SPEC_DIR.relative_to(REPO_ROOT)}; nothing to render.")
        return 0

    frs, fr_errors = load_frs()
    for err in fr_errors:
        print(f"WARN: {err}", file=sys.stderr)
    adrs = load_adrs()

    prior_stamp: dict = {}
    if RENDER_STAMP.exists():
        try:
            prior_stamp = json.loads(RENDER_STAMP.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            prior_stamp = {}

    CLIENT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    stamp: dict = dict(prior_stamp)  # preserve stamps for docs not rendered this run
    for spec in specs:
        markdown, sources = render_doc(spec, frs, adrs)
        out_path = CLIENT_DIR / f"{spec.doc}.md"
        out_path.write_text(markdown, encoding="utf-8")
        stamp[spec.doc] = {"rendered_at": now, "sources": sources}
        print(f"Rendered {out_path.relative_to(REPO_ROOT)} ({len(sources)} source(s))")

    RENDER_STAMP.write_text(json.dumps(stamp, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Gaps are computed across ALL doc specs, not just the ones rendered, so the
    # "uncovered active FR" check stays accurate when rendering a single doc.
    all_specs, _ = load_doc_specs(None)
    gaps = compute_gaps(all_specs, frs, adrs, prior_stamp)
    GAPS_PATH.write_text(_render_gaps_md(gaps), encoding="utf-8")
    total_gaps = sum(len(v) for v in gaps.values())
    print(f"Wrote {GAPS_PATH.relative_to(REPO_ROOT)} ({total_gaps} gap(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
