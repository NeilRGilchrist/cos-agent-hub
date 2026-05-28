#!/usr/bin/env python3
"""
PreToolUse hook: PHI regex check.

Reads the tool invocation JSON from stdin. If the tool is Write/Edit/MultiEdit and
the proposed content matches obvious PHI patterns (Canadian SIN, Ontario Health
Card Number, or US SSN), exit 2 to block the write. Exit 0 otherwise.

This is a coarse first line of defense. It catches the obvious mistakes —
literal SINs/HCNs pasted into code, tests, or specs. It does NOT catch subtle
PHI leaks (real names that don't pattern-match, plausible-but-real addresses,
etc.). Discipline + role files + AGENTS.md cover those.

False positives are possible (e.g., a 9-digit identifier that happens to look
like a SIN). When that happens, the human supervisor decides whether to add an
exemption to .agent-team/hooks/phi_exemptions.txt or rework the input.
"""
import json
import re
import sys

PHI_PATTERNS = [
    # Canadian SIN: 9 digits, often grouped 3-3-3 with space or hyphen.
    (r"\b\d{3}[-\s]\d{3}[-\s]\d{3}\b", "Possible Canadian SIN (XXX-XXX-XXX)"),
    # Ontario Health Card Number: 10 digits, often with 2-letter version code.
    (r"\b\d{4}[-\s]\d{3}[-\s]\d{3}(?:[-\s][A-Z]{2})?\b", "Possible Ontario HCN (XXXX-XXX-XXX [VV])"),
    # US SSN: 3-2-4 grouping.
    (r"\b\d{3}-\d{2}-\d{4}\b", "Possible US SSN (XXX-XX-XXXX)"),
]


def extract_content(tool_input: dict, tool_name: str) -> str:
    """Pull the content payload out of the tool input, format-dependent."""
    if tool_name == "Write":
        return tool_input.get("content", "") or ""
    if tool_name == "Edit":
        return (tool_input.get("old_string", "") or "") + "\n" + (tool_input.get("new_string", "") or "")
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits", []) or []
        chunks = []
        for e in edits:
            chunks.append(e.get("old_string", "") or "")
            chunks.append(e.get("new_string", "") or "")
        return "\n".join(chunks)
    return ""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}

    if tool_name not in ("Write", "Edit", "MultiEdit"):
        return 0

    content = extract_content(tool_input, tool_name)
    if not content:
        return 0

    hits = []
    for pattern, label in PHI_PATTERNS:
        if re.search(pattern, content):
            hits.append(label)

    if hits:
        print(
            "BLOCKED by PHI hygiene hook. Detected pattern(s): "
            + "; ".join(hits)
            + ". Real PHI must never be written to this repo. "
            + "If this is a false positive (e.g. synthetic data that happens to match the pattern), "
            + "stop and surface to the human supervisor — do not work around the hook.",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
