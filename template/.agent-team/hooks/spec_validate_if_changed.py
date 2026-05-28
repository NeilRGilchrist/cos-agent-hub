#!/usr/bin/env python3
"""
PostToolUse hook: validate spec graph after edits to specs/ files.

Cheap pass-through if the edited file isn't under specs/. Otherwise runs
index-specs.py in validate-only mode and surfaces any errors back to Claude
via stderr (exit 0 is still used; we want the agent to *see* validation
errors and react to them, not have the tool execution treated as failed
after the fact — PostToolUse can't undo writes anyway).

This keeps the spec graph from drifting silently when an FR is edited in a
way that breaks frontmatter, parent links, or AC ID continuity.
"""
import json
import os
import subprocess
import sys


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path") or tool_input.get("path") or ""

    if not file_path:
        return 0

    # Normalize and check if this edit touched specs/.
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    try:
        rel = os.path.relpath(file_path, project_dir)
    except ValueError:
        return 0

    if not rel.startswith("specs" + os.sep) and rel != "specs":
        return 0

    indexer = os.path.join(project_dir, "scripts", "index-specs.py")
    if not os.path.exists(indexer):
        return 0

    result = subprocess.run(
        ["python", indexer, "--validate"],
        capture_output=True,
        text=True,
        cwd=project_dir,
    )

    if result.returncode != 0:
        # Surface the validation failure to Claude. Exit 0 because the tool
        # already executed; this is informational pressure for the next turn.
        print(
            "Spec graph validation failed after edit:\n"
            + (result.stdout or "")
            + (result.stderr or ""),
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
