#!/usr/bin/env python3
"""
compile.py — Compile an architecture pack + flow.yaml into an integration viz spec.

Joins two sources:
  1. The architecture pack: yaml-frontmatter markdown files (FRs, ADRs) indexed by stable ID.
  2. flow.yaml: the topology spine (systems, steps, edges, payloads, traces).

Emits:
  - spec.json  — the renderer's input (pure data, renderer-agnostic)
  - gaps.md    — coverage lint: unreferenced FRs, unanchored steps, missing rationale

Hard errors (exit 1 unless --force): unknown FR/step/payload/system references,
duplicate IDs. Soft warnings land in gaps.md and spec.json["gaps"].

Usage:
  python compile.py --pack ./pack --flow ./pack/flow.yaml --out ./spec.json [--gaps ./gaps.md] [--force]

Dependencies: pyyaml
"""

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("compile.py requires pyyaml (pip install pyyaml)")

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.M)


# ---------------------------------------------------------------- pack parsing

def parse_md(path: Path):
    """Return (frontmatter_dict, body_str) for a markdown file, or (None, None) if no frontmatter."""
    text = path.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None, None
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"{path}: bad frontmatter: {e}")
    return meta, text[m.end():]


def extract_section(body: str, names):
    """Text under the first heading whose title matches any of `names` (case-insensitive),
    up to the next heading of the same or higher level."""
    wanted = {n.lower() for n in names}
    headings = list(HEADING_RE.finditer(body))
    for i, h in enumerate(headings):
        if h.group(2).strip().lower() in wanted:
            level = len(h.group(1))
            start = h.end()
            end = len(body)
            for nxt in headings[i + 1:]:
                if len(nxt.group(1)) <= level:
                    end = nxt.start()
                    break
            return body[start:end].strip()
    return ""


def extract_bullets(text: str):
    """Pull top-level markdown bullets (supports -, *, and - [ ] task syntax)."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^\s*[-*]\s+(?:\[[ xX]\]\s+)?(.*\S)\s*$", line)
        if m:
            out.append(m.group(1))
    return out


# AC prefix detection, aligned with the spec-graph deploy-gate (scripts/deploy-gate.py
# AC_HEADING_PATTERN): accept `AC-1:`, `**AC-1:**`, and `**AC-1**:` bold variants so the
# stable AC id survives whichever form a conformant FR uses. Also captures the AC text.
AC_RE = re.compile(r"^\**\s*(AC-\d+)\**\s*:\s*\**\s*(.*?)\s*$")
ADR_MENTION_RE = re.compile(r"\bADR-[A-Za-z0-9][A-Za-z0-9_-]*")
FR_AC_REF_RE = re.compile(r"\b(FR-[A-Za-z0-9_-]+)\s*:\s*(AC-\d+)")


def first_para(text: str):
    """First non-empty paragraph, markdown bold stripped — an ADR Decision's thesis line."""
    for chunk in re.split(r"\n\s*\n", text.strip()):
        chunk = chunk.strip()
        if chunk:
            return chunk.replace("**", "")[:500]
    return ""


def parse_acs(text: str):
    """Bullets under Acceptance criteria. Honors stable `AC-n:` ids when present."""
    out = []
    for b in extract_bullets(text):
        m = AC_RE.match(b)
        out.append({"id": m.group(1), "text": m.group(2)} if m else {"id": None, "text": b})
    return out


# Q / Default prefix detection. Bold-tolerant for the same reason AC_RE above is:
# the FR template writes `- **Q:**` and `  - **Default:**`, so a bare `^Q\s*:` never
# matches a conformant FR and every open question is silently dropped. Accepts
# `Q:`, `**Q:**` and `**Q**:` (likewise Default), mirroring AC_RE's shape.
OQ_Q_RE = re.compile(r"^\**\s*Q\**\s*:\s*\**\s*(.*?)\s*$", re.I)
OQ_DEFAULT_RE = re.compile(r"^\**\s*Default\**\s*:\s*\**\s*(.*?)\s*$", re.I)


def parse_open_questions(text: str):
    """Pairs `- **Q:** ...` bullets with their nested `- **Default:** ...` bullet."""
    out, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^(\s*)[-*]\s+(.*\S)\s*$", line)
        if not m:
            continue
        body = m.group(2)
        qm = OQ_Q_RE.match(body)
        dm = OQ_DEFAULT_RE.match(body)
        if qm:
            cur = {"q": qm.group(1), "default": ""}
            out.append(cur)
        elif dm and cur is not None:
            cur["default"] = dm.group(1)
    return out


# Spec-graph scaffolding dirs that live under specs/ but are not pack documents:
# the FR template stub (a bogus FR-XXXX id) and escalation notes. Skip them so
# pointing --pack at a conformant specs/ tree doesn't ingest non-requirements.
PACK_SKIP_DIRS = {"_template", "_escalations"}


def load_pack(pack_dir: Path):
    """Index every frontmattered markdown file in the pack by its `id`."""
    docs, errors = {}, []
    for path in sorted(pack_dir.rglob("*.md")):
        if any(part in PACK_SKIP_DIRS for part in path.relative_to(pack_dir).parts):
            continue
        try:
            meta, body = parse_md(path)
        except ValueError as e:
            errors.append(str(e))
            continue
        if not meta or "id" not in meta:
            continue  # not a pack document; skip silently
        doc_id = str(meta["id"])
        if doc_id in docs:
            errors.append(f"duplicate id {doc_id}: {path} and {docs[doc_id]['path']}")
            continue
        docs[doc_id] = {"meta": meta, "body": body, "path": str(path)}
    return docs, errors


def doc_type(doc):
    """Classify a pack doc: explicit `type` frontmatter wins, else infer from ID prefix."""
    t = str(doc["meta"].get("type", "")).lower()
    if t:
        return t
    did = str(doc["meta"].get("id", ""))
    if did.upper().startswith("FR"):
        return "functional-requirement"
    if did.upper().startswith("ADR"):
        return "adr"
    return "unknown"


def adr_entry(doc, superseded_by):
    meta, body = doc["meta"], doc["body"]
    did = str(meta["id"])
    return {
        "id": did,
        "title": meta.get("title", did),
        "status": meta.get("status", ""),
        "date": str(meta.get("date", "")),
        "summary": first_para(extract_section(body, ["Decision", "Summary"])),
        "supersedes": str(meta["supersedes"]) if meta.get("supersedes") else None,
        "superseded_by": superseded_by.get(did),
        "ac_refs": [],
    }


def build_adr_links(docs, warnings):
    """Reverse join: ADR frontmatter `fr_refs` (plus FR-XXXX:AC-n body mentions,
    which also give AC-level anchoring) -> fr_id -> [adr entries]."""
    superseded_by = {}
    for did, doc in docs.items():
        if doc_type(doc) == "adr" and doc["meta"].get("supersedes"):
            superseded_by[str(doc["meta"]["supersedes"])] = did

    links = {}
    for did, doc in docs.items():
        if doc_type(doc) != "adr":
            continue
        meta, body = doc["meta"], doc["body"]
        ac_map = {}
        for fr_id, ac_id in FR_AC_REF_RE.findall(body):
            ac_map.setdefault(fr_id, set()).add(ac_id)
        targets = {str(f) for f in (meta.get("fr_refs") or [])} | set(ac_map)
        for fr_id in targets:
            if fr_id not in docs:
                warnings.append(f"{did}: fr_ref {fr_id} not found in pack")
            e = adr_entry(doc, superseded_by)
            e["ac_refs"] = sorted(ac_map.get(fr_id, []))
            links.setdefault(fr_id, []).append(e)
    return links, superseded_by


def fr_to_spec(doc, docs, adr_links, superseded_by, warnings):
    """Project an FR pack doc into the spec's embedded FR shape.

    Aligned with the FR template: Why / What / Acceptance criteria (stable AC-n ids)
    / Out of scope / Open questions (Q + Default) / Notes, plus frontmatter
    status/owner/depends_on/owns. `## Context` is honored as a legacy alias for Why.

    ADR linkage is ADR-driven (the ADR's `fr_refs` frontmatter and its
    FR-XXXX:AC-n body anchors). ADR mentions inside the FR body and an optional
    legacy `adrs:` frontmatter list are merged in as supplementary links.
    """
    meta, body = doc["meta"], doc["body"]
    fr_id = str(meta["id"])

    adrs = [dict(e) for e in adr_links.get(fr_id, [])]
    seen = {a["id"] for a in adrs}
    for adr_id in [str(a) for a in (meta.get("adrs") or [])] + ADR_MENTION_RE.findall(body):
        # Markdown links to ADR files (e.g. `[ADR-0001](decisions/ADR-0001-slug.md)`) make the
        # greedy mention regex capture the whole filename slug. If the raw mention isn't a pack id,
        # trim trailing `-segment`s to recover the canonical id before treating it as unknown.
        if adr_id not in docs:
            parts = adr_id.split("-")
            while len(parts) > 2 and "-".join(parts) not in docs:
                parts.pop()
            trimmed = "-".join(parts)
            if trimmed in docs:
                adr_id = trimmed
        if adr_id in seen:
            continue
        seen.add(adr_id)
        adr_doc = docs.get(adr_id)
        if adr_doc:
            adrs.append(adr_entry(adr_doc, superseded_by))
        else:
            warnings.append(f"{fr_id}: mentions unknown ADR {adr_id}")
            adrs.append({"id": adr_id, "title": adr_id, "status": "unresolved", "date": "",
                         "summary": "", "supersedes": None, "superseded_by": None, "ac_refs": []})
    for a in adrs:
        if a.get("superseded_by"):
            warnings.append(f"{fr_id} links {a['id']}, which is superseded by {a['superseded_by']}")

    return {
        "id": fr_id,
        "title": meta.get("title", fr_id),
        "status": meta.get("status", ""),
        "owner": meta.get("owner", ""),
        "depends_on": [str(d) for d in (meta.get("depends_on") or [])],
        "owns": [str(g) for g in (meta.get("owns") or [])],
        "why": extract_section(body, ["Why", "Context"]),
        "what": extract_section(body, ["What"]),
        "acceptance_criteria": parse_acs(extract_section(body, ["Acceptance Criteria", "ACs"])),
        "out_of_scope": extract_bullets(extract_section(body, ["Out of scope"])),
        "open_questions": parse_open_questions(extract_section(body, ["Open questions"])),
        "notes": extract_section(body, ["Notes"]),
        "adrs": adrs,
    }


# ------------------------------------------------------------------- compile

def compile_spec(pack_dir: Path, flow_path: Path, scope: str = "tag"):
    flow = yaml.safe_load(flow_path.read_text(encoding="utf-8"))
    docs, hard_errors = load_pack(pack_dir)
    warnings = []

    flow_id = str(flow.get("id", "integration"))
    integration_tag = f"integration:{flow_id}"

    systems = flow.get("systems", []) or []
    system_ids = {s["id"] for s in systems}

    payloads = flow.get("payloads", []) or []
    payload_ids = {p["id"] for p in payloads}

    adr_links, superseded_by = build_adr_links(docs, warnings)

    # --- steps: join fr_refs against the pack
    steps, referenced_frs, steps_without_frs = [], set(), []
    step_ids, seqs_seen = set(), {}
    for raw in flow.get("steps", []) or []:
        sid = str(raw["id"])
        if sid in step_ids:
            hard_errors.append(f"duplicate step id {sid}")
        step_ids.add(sid)

        seq = raw.get("seq", 0)
        if seq in seqs_seen:
            warnings.append(f"steps {seqs_seen[seq]} and {sid} share seq {seq}")
        seqs_seen[seq] = sid

        if raw.get("system") not in system_ids:
            hard_errors.append(f"step {sid}: unknown system {raw.get('system')!r}")

        fr_refs = [str(r) for r in (raw.get("fr_refs") or [])]
        frs = []
        for fr_id in fr_refs:
            doc = docs.get(fr_id)
            if not doc:
                hard_errors.append(f"step {sid}: unknown FR {fr_id}")
                continue
            referenced_frs.add(fr_id)
            frs.append(fr_to_spec(doc, docs, adr_links, superseded_by, warnings))
        if not fr_refs and raw.get("path", "happy") == "happy":
            steps_without_frs.append(sid)

        contract = dict(raw.get("contract") or {})
        pref = contract.get("payload_ref")
        if pref and pref not in payload_ids:
            hard_errors.append(f"step {sid}: unknown payload_ref {pref}")

        steps.append({
            "id": sid,
            "seq": seq,
            "system": raw.get("system"),
            "title": raw.get("title", sid),
            "mode": raw.get("mode", "sync"),
            "cadence": raw.get("cadence", "event"),
            "phi": bool(raw.get("phi", False)),
            "path": raw.get("path", "happy"),
            "why": {"summary": raw.get("why", ""), "frs": frs},
            "contract": contract,
            "behavior": dict(raw.get("behavior") or {}),
            "provenance": list(raw.get("provenance") or []),  # allowed empty by design
        })

    # --- edges
    edges = []
    for raw in flow.get("edges", []) or []:
        src, dst = str(raw["from"]), str(raw["to"])
        for endpoint in (src, dst):
            if endpoint not in step_ids:
                hard_errors.append(f"edge {src}->{dst}: unknown step {endpoint}")
        pref = raw.get("payload")
        if pref and pref not in payload_ids:
            hard_errors.append(f"edge {src}->{dst}: unknown payload {pref}")
        edges.append({
            "from": src, "to": dst,
            "payload_ref": pref,
            "kind": raw.get("kind", "normal"),
            "label": raw.get("label", ""),
        })

    # --- payload field lint
    fields_missing_rationale = []
    for p in payloads:
        for f in p.get("fields", []) or []:
            if not f.get("rationale"):
                fields_missing_rationale.append(f"{p['id']}.{f.get('name', '?')}")
            fr_ref = f.get("fr_ref")
            if fr_ref:
                if fr_ref in docs:
                    referenced_frs.add(str(fr_ref))
                else:
                    warnings.append(f"payload field {p['id']}.{f.get('name')}: unknown fr_ref {fr_ref}")

    # --- coverage: FRs tagged to this integration that nothing references
    def in_scope(doc):
        if scope == "pack":
            return True  # whole pack belongs to this integration
        return integration_tag in (doc["meta"].get("tags") or [])

    unreferenced_frs = sorted(
        did for did, doc in docs.items()
        if doc_type(doc) == "functional-requirement"
        and in_scope(doc) and did not in referenced_frs
    )

    spec = {
        "meta": {
            "id": flow_id,
            "title": flow.get("title", flow_id),
            "purpose": flow.get("purpose", ""),
            "trigger": flow.get("trigger", {}),
            "success_criterion": flow.get("success_criterion", ""),
            "version": str(flow.get("version", "0.1")),
            "compiled_at": datetime.datetime.now(datetime.timezone.utc)
                           .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_pack": str(pack_dir),
        },
        "systems": systems,
        "steps": sorted(steps, key=lambda s: s["seq"]),
        "edges": edges,
        "payloads": payloads,
        "traces": flow.get("trace_samples", []) or [],
        "gaps": {
            "unreferenced_frs": unreferenced_frs,
            "steps_without_frs": steps_without_frs,
            "fields_missing_rationale": fields_missing_rationale,
            "warnings": warnings,
        },
    }
    return spec, hard_errors


def write_gaps_md(spec, hard_errors, path: Path):
    g = spec["gaps"]
    lines = [f"# Gap report — {spec['meta']['title']} ({spec['meta']['id']})",
             f"Compiled {spec['meta']['compiled_at']}", ""]

    def section(title, items, empty_note):
        lines.append(f"## {title}")
        if items:
            lines.extend(f"- {i}" for i in items)
        else:
            lines.append(f"_{empty_note}_")
        lines.append("")

    section("Hard errors", hard_errors, "None")
    section("FRs tagged to this integration but referenced by no step",
            g["unreferenced_frs"], "Full coverage — every tagged FR is anchored to a step")
    section("Happy-path steps citing no FR", g["steps_without_frs"],
            "Every happy-path step traces to at least one FR")
    section("Payload fields without a mapping rationale",
            g["fields_missing_rationale"], "Every field mapping carries its why")
    section("Warnings", g["warnings"], "None")
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pack", required=True, type=Path, help="architecture pack root directory")
    ap.add_argument("--flow", required=True, type=Path, help="flow.yaml topology file")
    ap.add_argument("--out", required=True, type=Path, help="output spec.json path")
    ap.add_argument("--gaps", type=Path, help="output gaps.md path (default: alongside spec.json)")
    ap.add_argument("--scope", choices=["tag", "pack"], default="tag",
                    help="which FRs count as belonging to this integration for coverage lint: "
                         "'tag' = FRs tagged integration:<flow-id> (default); "
                         "'pack' = every FR in the pack directory")
    ap.add_argument("--force", action="store_true", help="emit spec even with hard errors")
    args = ap.parse_args()

    spec, hard_errors = compile_spec(args.pack, args.flow, scope=args.scope)

    gaps_path = args.gaps or args.out.with_name("gaps.md")
    write_gaps_md(spec, hard_errors, gaps_path)

    if hard_errors and not args.force:
        print("HARD ERRORS — spec not written (use --force to override):", file=sys.stderr)
        for e in hard_errors:
            print(f"  - {e}", file=sys.stderr)
        print(f"Gap report written to {gaps_path}", file=sys.stderr)
        sys.exit(1)

    args.out.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    g = spec["gaps"]
    n_soft = sum(len(v) for v in g.values())
    print(f"spec.json written: {len(spec['steps'])} steps, {len(spec['edges'])} edges, "
          f"{len(spec['payloads'])} payloads, {len(spec['traces'])} trace(s)")
    print(f"gap report: {len(hard_errors)} hard error(s), {n_soft} soft finding(s) -> {gaps_path}")


if __name__ == "__main__":
    main()
