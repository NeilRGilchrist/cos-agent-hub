#!/usr/bin/env python3
"""
dispatch.py — Optional orchestration layer for multi-agent FR dispatch.

This is the headless multi-agent dispatcher. It reads the spec graph,
spawns per-FR agent sessions in isolated git worktrees, manages lockfiles
for idempotency, and owns the GitHub PR lifecycle (push, create, review).

Projects can use this script for fully automated agent waves, or ignore
it entirely and run agents interactively via slash commands.

Reads specs/FR-*.md frontmatter and spawns headless agent sessions in
per-FR worktrees. Idempotent via per-(FR, role) lockfile.

Two roles are dispatchable today:

  * dev (default) — picks FRs with status=ready whose depends_on are all
    in TERMINAL_STATUSES. Worktree branches off `main`.
  * rev           — picks FRs with status in {in-progress, in-review} for
    which a `claude/dev-FR-XXXX` branch exists locally and no Developer
    is currently running. Worktree branches off the Dev branch so the
    Reviewer's tree contains the Developer's diff.

The headless session can run on either of two harnesses:

  * claude-code (default) — `claude -p "/developer FR-XXXX"` (or `/reviewer`)
  * cursor               — `cursor-agent -p --force --trust --approve-mcps
                              --output-format stream-json "<rendered prompt>"`

Pick a harness with `--harness {claude-code,cursor}` or the env var
`DISPATCH_HARNESS`. All other dispatch logic (worktrees, venv
provisioning, locks, prune/kill, escalation surfacing) is
harness-agnostic.

Designed to run from a git post-merge hook on `main`, but safe to invoke
manually.

Usage:
  python scripts/dispatch.py tick                            # Dev wave, dry-run
  python scripts/dispatch.py tick --apply                    # Dev wave, spawn
  python scripts/dispatch.py tick --role rev                 # Reviewer wave, dry-run
  python scripts/dispatch.py tick --role rev --apply         # Reviewer wave, spawn
  python scripts/dispatch.py tick --harness cursor --apply   # Dev wave on Cursor
  python scripts/dispatch.py status                          # liveness/idle/dead per lock
  python scripts/dispatch.py prune                           # remove dead locks
  python scripts/dispatch.py kill FR-XXXX --role {dev,rev}   # force-terminate + remove lock
  python scripts/dispatch.py summary FR-XXXX --role {dev,rev}  # parsed log summary
  python scripts/dispatch.py finalize FR-XXXX --role dev     # push + open PR (dry-run)
  python scripts/dispatch.py finalize FR-XXXX --role dev --apply  # actually push + open
  python scripts/dispatch.py finalize FR-XXXX --role rev --apply  # post gh pr review
  python scripts/dispatch.py finalize-all --role dev --apply      # finalize every dev branch with a complete log
  python scripts/dispatch.py reconcile-merged                # show post-merge cleanup plan (dry-run)
  python scripts/dispatch.py reconcile-merged --apply        # flip merged FR status, prune worktrees/branches/locks
  python scripts/dispatch.py backfill FR-XXXX                # show one-shot Reviewer-backfill plan (dry-run)
  python scripts/dispatch.py backfill FR-XXXX --apply        # spawn Reviewer to write missing AC tests for an already-merged FR
  python scripts/dispatch.py finalize FR-XXXX --role bkf --apply  # push + open `test(FR-XXXX): backfill AC coverage` PR

GitHub PR integration (finalize):
  Agents do NOT touch GitHub directly. The dispatcher owns:
    * `git push -u origin <branch>` (no force; round-2+ commits land as fast-forwards)
    * `gh pr create --draft --base main --head <branch> --body-file PR_BODY.md`
    * `gh pr review <num> {--approve|--request-changes|--comment} --body <reviewer text>`
  Finalize records the PR number in `_dispatch/<fr>.<role>.pr.json` so re-runs are
  idempotent (push happens; create no-ops; review is posted as a new review thread).

Prerequisites for finalize:
  * `gh` CLI installed and `gh auth status` green (run `gh auth login` if not)
  * a remote named `origin` configured on the repo
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Protocol

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(2)


# Cursor and Claude agents emit Unicode freely (em-dashes, arrows, smart
# quotes). On Windows, Python's default stdout encoding is cp1252 and any
# such character makes `print` raise UnicodeEncodeError mid-summary. Force
# UTF-8 with a lossy fallback so the dispatcher never crashes mid-output.
for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (OSError, ValueError):
            pass


REPO_ROOT = Path(__file__).resolve().parent.parent
SPECS_DIR = REPO_ROOT / "specs"
DISPATCH_DIR = REPO_ROOT / "_dispatch"
ESCALATIONS_DIR = REPO_ROOT / "specs" / "_escalations"
LAST_TICK_FILE = DISPATCH_DIR / ".last-tick"
COMMANDS_DIR = REPO_ROOT / ".claude" / "commands"

FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
TERMINAL_STATUSES = {"done", "merged", "deployed"}
STALE_LOCK_SECONDS = 2 * 60 * 60
IDLE_LOG_THRESHOLD_SECONDS = 5 * 60

SLASH_COMMAND_NAMES = {
    "dev": "developer",
    "rev": "reviewer",
    # `bkf` is a one-shot Reviewer-in-backfill-mode used when an FR was
    # merged without proper @covers test coverage (the deploy-gate gap
    # surfaced after `reconcile-merged` flips status to merged). Branches
    # off `main` (the dev work is already there); opens a small follow-up
    # PR titled `test(FR-XXXX): backfill AC coverage`.
    "bkf": "reviewer-backfill",
}

DEFAULT_HARNESS = os.environ.get("DISPATCH_HARNESS", "claude-code")


# ---------------------------------------------------------------------------
# Harness adapter abstraction
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class AgentLogSummary:
    """Harness-agnostic, post-hoc view of a single agent run's NDJSON log."""

    result_subtype: str | None = None
    is_error: bool = False
    tool_counts: dict[str, int] = dataclasses.field(default_factory=dict)
    error_objs: list[dict] = dataclasses.field(default_factory=list)
    last_text: str | None = None


class HarnessAdapter(Protocol):
    name: str
    binary_name: str

    def write_worktree_settings(self, wt_path: Path, role: str) -> None: ...
    def spawn(
        self,
        fr_id: str,
        role: str,
        wt_path: Path,
        log_path: Path,
    ) -> tuple[str, int | None]: ...
    def parse_log(self, log_path: Path) -> AgentLogSummary: ...
    def dry_run_preview(self, fr_id: str, role: str, wt_preview: Path) -> str: ...


def _slash_command_body(slash_name: str) -> str:
    """Return the body of `.claude/commands/<slash_name>.md` with YAML
    frontmatter stripped. Both harnesses derive their role-prompt template
    from this single source so the role definition isn't duplicated.
    """
    p = COMMANDS_DIR / f"{slash_name}.md"
    if not p.exists():
        raise FileNotFoundError(f"slash command template missing: {p}")
    text = p.read_text(encoding="utf-8")
    m = FRONTMATTER_PATTERN.match(text)
    if m:
        text = text[m.end() :]
    return text.strip()


# ---------------------------------------------------------------------------
# Claude Code adapter (preserves today's behaviour byte-for-byte)
# ---------------------------------------------------------------------------


class ClaudeCodeAdapter:
    """Headless `claude -p '/role FR-XXXX'` invocation.

    Permissions are written into each worktree as .claude/settings.local.json
    rather than passed via --allowedTools. This avoids the brittleness of CLI
    escaping on Windows and matches the format the agent itself reaches for
    (and which survives across resume / continue sessions).
    """

    name = "claude-code"
    binary_name = "claude"

    # Per-role settings are merged into the worktree's
    # .claude/settings.local.json at worktree-create time. Globs use
    # Claude Code's permission syntax.
    ROLE_PERMISSIONS: dict[str, dict[str, list[str]]] = {
        "dev": {
            "allow": [
                # Pure read tools — always safe
                "Read",
                "Glob",
                "Grep",
                "TodoWrite",
                # Production code, specs (frontmatter only), docs, PR body
                "Edit(src/**)",
                "Write(src/**)",
                "Edit(specs/FR-*.md)",
                "Edit(docs/**)",
                "Write(docs/**)",
                "Edit(PR_BODY.md)",
                "Write(PR_BODY.md)",
                # Repo-root scratch files agents commonly need (e.g. AC scripts
                # whose final home is scripts/ but which the dev can't write to
                # directly — they land at root and the next role relocates).
                "Edit(*.py)",
                "Write(*.py)",
                "Edit(*.md)",
                "Write(*.md)",
                # Test fixtures + conftest are infrastructure, not test code.
                # Test files themselves remain Reviewer-only (see deny list).
                "Edit(tests/fixtures/**)",
                "Write(tests/fixtures/**)",
                "Edit(tests/conftest.py)",
                "Write(tests/conftest.py)",
                # Python / venv / test runners — both system python and the
                # pre-provisioned worktree venv (see VENV_DIRNAME)
                "Bash(python *)",
                "Bash(python3 *)",
                "Bash(py *)",
                f"Bash({VENV_DIRNAME}/Scripts/python.exe *)",
                f"Bash({VENV_DIRNAME}/Scripts/python *)",
                f"Bash({VENV_DIRNAME}/Scripts/pip *)",
                f"Bash({VENV_DIRNAME}/Scripts/ruff *)",
                f"Bash({VENV_DIRNAME}/Scripts/pytest *)",
                f"Bash({VENV_DIRNAME}/bin/python *)",
                f"Bash({VENV_DIRNAME}/bin/pip *)",
                f"Bash({VENV_DIRNAME}/bin/ruff *)",
                f"Bash({VENV_DIRNAME}/bin/pytest *)",
                "Bash(ruff *)",
                "Bash(pytest *)",
                "Bash(pip *)",
                # Filesystem inspection
                "Bash(ls *)",
                "Bash(ls)",
                "Bash(cat *)",
                "Bash(head *)",
                "Bash(tail *)",
                "Bash(echo *)",
                "Bash(pwd)",
                "Bash(where *)",
                "Bash(which *)",
                # Git: status/inspect, plus add/commit/checkout/branch/show/stash
                # (push/origin handled below by deny-list; see Notes)
                "Bash(git status*)",
                "Bash(git diff*)",
                "Bash(git log*)",
                "Bash(git show*)",
                "Bash(git remote*)",
                "Bash(git add *)",
                "Bash(git commit *)",
                "Bash(git commit)",
                "Bash(git checkout *)",
                "Bash(git checkout)",
                "Bash(git branch*)",
                "Bash(git stash*)",
                "Bash(git restore *)",
                "Bash(git reset)",
                # Push + GitHub CLI removed: dispatcher owns push & PR creation.
                # Agents commit locally and write PR_BODY.md; `finalize` does the rest.
                # Compound shells that reduce to permitted parts. Claude Code
                # interprets `Bash(* && *)` etc. as a permission to allow
                # multi-segment commands provided each segment matches an
                # individually allowed pattern. This keeps `cd path && python …`
                # from popping permission prompts.
                "Bash(* && *)",
                "Bash(* || *)",
                "Bash(* | *)",
                "Bash(* ; *)",
            ],
            "deny": [
                # Spec graph + role boundaries (universal)
                "Edit(.agent-team/**)",
                "Write(.agent-team/**)",
                # Specific gate / dispatch scripts only — bootstrap/setup
                # scripts an FR may need to author live elsewhere under
                # scripts/ and stay write-allowable.
                "Edit(scripts/deploy-gate.py)",
                "Write(scripts/deploy-gate.py)",
                "Edit(scripts/index-specs.py)",
                "Write(scripts/index-specs.py)",
                "Edit(scripts/dispatch.py)",
                "Write(scripts/dispatch.py)",
                "Edit(scripts/agent-status.py)",
                "Write(scripts/agent-status.py)",
                "Edit(CLAUDE.md)",
                "Write(CLAUDE.md)",
                # Developer must NOT write tests — that's the Reviewer's job.
                # Fixtures and conftest are explicitly allowed above.
                "Edit(tests/test_*.py)",
                "Write(tests/test_*.py)",
                # Destructive git ops
                "Bash(git push --force*)",
                "Bash(git push -f*)",
                "Bash(git reset --hard*)",
                "Bash(rm -rf*)",
                "Bash(rm -r *)",
            ],
        },
        "rev": {
            "allow": [
                # Pure read tools — always safe
                "Read",
                "Glob",
                "Grep",
                "TodoWrite",
                # Tests are the Reviewer's primary deliverable.
                "Edit(tests/**)",
                "Write(tests/**)",
                # Review artefact + spec frontmatter (status flips to
                # in-review / approved are allowed; AC body is Architect-only)
                "Edit(REVIEW.md)",
                "Write(REVIEW.md)",
                "Edit(specs/FR-*.md)",
                # Repo-root scratch the Reviewer commonly drafts
                "Edit(*.md)",
                "Write(*.md)",
                # Same Python / venv / test-runner posture as Dev so the
                # Reviewer can run pytest, ruff, the deploy gate, etc.
                "Bash(python *)",
                "Bash(python3 *)",
                "Bash(py *)",
                f"Bash({VENV_DIRNAME}/Scripts/python.exe *)",
                f"Bash({VENV_DIRNAME}/Scripts/python *)",
                f"Bash({VENV_DIRNAME}/Scripts/pip *)",
                f"Bash({VENV_DIRNAME}/Scripts/ruff *)",
                f"Bash({VENV_DIRNAME}/Scripts/pytest *)",
                f"Bash({VENV_DIRNAME}/bin/python *)",
                f"Bash({VENV_DIRNAME}/bin/pip *)",
                f"Bash({VENV_DIRNAME}/bin/ruff *)",
                f"Bash({VENV_DIRNAME}/bin/pytest *)",
                "Bash(ruff *)",
                "Bash(pytest *)",
                "Bash(pip *)",
                # Filesystem inspection
                "Bash(ls *)",
                "Bash(ls)",
                "Bash(cat *)",
                "Bash(head *)",
                "Bash(tail *)",
                "Bash(echo *)",
                "Bash(pwd)",
                "Bash(where *)",
                "Bash(which *)",
                # Git: same posture as Dev (commit/push the rev branch,
                # inspect diffs); destructive variants denied below.
                "Bash(git status*)",
                "Bash(git diff*)",
                "Bash(git log*)",
                "Bash(git show*)",
                "Bash(git remote*)",
                "Bash(git add *)",
                "Bash(git commit *)",
                "Bash(git commit)",
                "Bash(git checkout *)",
                "Bash(git checkout)",
                "Bash(git branch*)",
                "Bash(git stash*)",
                "Bash(git restore *)",
                "Bash(git reset)",
                # Push + GitHub CLI removed: dispatcher owns push & PR creation.
                # Reviewer feedback flows through `finalize --role rev`.
                # Compound shells with each segment individually permitted
                "Bash(* && *)",
                "Bash(* || *)",
                "Bash(* | *)",
                "Bash(* ; *)",
            ],
            "deny": [
                # Reviewer must NOT modify production code — kicks back via
                # PR review state, doesn't fix.
                "Edit(src/**)",
                "Write(src/**)",
                # Same protected scripts as Dev
                "Edit(.agent-team/**)",
                "Write(.agent-team/**)",
                "Edit(scripts/deploy-gate.py)",
                "Write(scripts/deploy-gate.py)",
                "Edit(scripts/index-specs.py)",
                "Write(scripts/index-specs.py)",
                "Edit(scripts/dispatch.py)",
                "Write(scripts/dispatch.py)",
                "Edit(scripts/agent-status.py)",
                "Write(scripts/agent-status.py)",
                "Edit(CLAUDE.md)",
                "Write(CLAUDE.md)",
                # Reviewer must NOT modify the Developer's PR_BODY — they
                # respond via gh pr review.
                "Edit(PR_BODY.md)",
                "Write(PR_BODY.md)",
                # Destructive git ops
                "Bash(git push --force*)",
                "Bash(git push -f*)",
                "Bash(git reset --hard*)",
                "Bash(rm -rf*)",
                "Bash(rm -r *)",
            ],
        },
    }
    # `bkf` (Reviewer in backfill mode) runs against an already-merged FR.
    # Same surface as `rev` (write tests, run gate, draft PR_BODY) but
    # tighter: no spec edits (FR is at terminal status), and PR_BODY is
    # the agent's own deliverable rather than the Developer's, so allow
    # writing it.
    ROLE_PERMISSIONS["bkf"] = {
        "allow": [a for a in ROLE_PERMISSIONS["rev"]["allow"] if a != "Edit(specs/FR-*.md)"]
        + ["Edit(PR_BODY.md)", "Write(PR_BODY.md)"],
        "deny": [d for d in ROLE_PERMISSIONS["rev"]["deny"] if d not in ("Edit(PR_BODY.md)", "Write(PR_BODY.md)")]
        + ["Edit(specs/**)", "Write(specs/**)"],
    }

    def write_worktree_settings(self, wt_path: Path, role: str) -> None:
        perms = self.ROLE_PERMISSIONS.get(role)
        if perms is None:
            return
        settings_dir = wt_path / ".claude"
        settings_dir.mkdir(parents=True, exist_ok=True)
        payload = {"permissions": {"allow": perms["allow"], "deny": perms["deny"]}}
        (settings_dir / "settings.local.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )

    def spawn(
        self,
        fr_id: str,
        role: str,
        wt_path: Path,
        log_path: Path,
    ) -> tuple[str, int | None]:
        claude_bin = shutil.which(self.binary_name)
        if claude_bin is None:
            return ("binary-not-found", None)
        slash = SLASH_COMMAND_NAMES.get(role, role)
        cmd = [
            claude_bin,
            "-p",
            f"/{slash} {fr_id}",
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        log_fh = log_path.open("wb")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=wt_path,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError:
            log_fh.close()
            return ("spawn-failed", None)
        return ("spawned", proc.pid)

    def dry_run_preview(self, fr_id: str, role: str, wt_preview: Path) -> str:
        slash = SLASH_COMMAND_NAMES.get(role, role)
        return (
            f"claude -p '/{slash} {fr_id}'  "
            f"(cwd={wt_preview}; perms via .claude/settings.local.json)"
        )

    def parse_log(self, log_path: Path) -> AgentLogSummary:
        s = AgentLogSummary()
        with log_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = obj.get("type")
                if t == "assistant":
                    for c in obj.get("message", {}).get("content", []) or []:
                        if c.get("type") == "text":
                            s.last_text = c.get("text", "")
                        elif c.get("type") == "tool_use":
                            name = c.get("name", "?")
                            s.tool_counts[name] = s.tool_counts.get(name, 0) + 1
                elif t == "result":
                    s.result_subtype = obj.get("subtype") or "unknown"
                    s.is_error = bool(obj.get("is_error"))
                elif t == "user":
                    for c in obj.get("message", {}).get("content", []) or []:
                        if c.get("type") == "tool_result" and c.get("is_error"):
                            s.error_objs.append(c)
        return s


# ---------------------------------------------------------------------------
# Cursor adapter
# ---------------------------------------------------------------------------


class CursorAdapter:
    """Headless `cursor-agent -p ...` invocation.

    Differences vs Claude Code:
      * Permissions live at <worktree>/.cursor/cli.json with token syntax
        Shell(cmd[:args]), Read(glob), Write(glob), WebFetch(domain),
        Mcp(server:tool). No Edit-vs-Write split — Write covers both.
      * Slash commands aren't reachable from headless mode in the same
        `--print` ergonomic way, so we render the body of
        `.claude/commands/<role>.md` (frontmatter stripped, $ARGUMENTS
        substituted) into the positional prompt argument. This keeps a
        single source of truth for the role prompt.
      * NDJSON event shapes are different: type=tool_call with
        subtype=started|completed; type=result with subtype=success|error.
    """

    name = "cursor"
    binary_name = "cursor-agent"

    # Cursor permission tokens are coarser than Claude's. Translation is
    # deliberately on the safer side: Cursor's Write(src/**) covers both
    # edit and create, so we only emit Write tokens. Read tools are
    # default-allow in Cursor and don't need explicit grants.
    ROLE_PERMISSIONS: dict[str, dict[str, list[str]]] = {
        "dev": {
            "allow": [
                # Production code, specs (frontmatter only), docs, PR body
                "Write(src/**)",
                "Write(specs/FR-*.md)",
                "Write(docs/**)",
                "Write(PR_BODY.md)",
                "Write(*.py)",
                "Write(*.md)",
                # Test fixtures + conftest — infrastructure, not test code
                "Write(tests/fixtures/**)",
                "Write(tests/conftest.py)",
                # Python interpreters / runners. Cursor's Shell(cmd:args)
                # syntax is per-command, so we whitelist by command name.
                "Shell(python)",
                "Shell(python3)",
                "Shell(py)",
                "Shell(pip)",
                "Shell(ruff)",
                "Shell(pytest)",
                "Shell(uv)",
                # Filesystem inspection
                "Shell(ls)",
                "Shell(cat)",
                "Shell(head)",
                "Shell(tail)",
                "Shell(echo)",
                "Shell(pwd)",
                "Shell(where)",
                "Shell(which)",
                # Git: rely on per-command gating; destructive variants + push
                # are denied below by argument pattern.
                "Shell(git)",
                # GitHub CLI removed: dispatcher owns push & PR creation.
            ],
            "deny": [
                "Write(.agent-team/**)",
                "Write(scripts/deploy-gate.py)",
                "Write(scripts/index-specs.py)",
                "Write(scripts/dispatch.py)",
                "Write(scripts/agent-status.py)",
                "Write(CLAUDE.md)",
                "Write(tests/test_*.py)",
                # Destructive git ops — Cursor token form is Shell(cmd:args*)
                "Shell(git:push --force*)",
                "Shell(git:push -f*)",
                # Plain push also denied — dispatcher owns push.
                "Shell(git:push)",
                "Shell(git:push *)",
                "Shell(git:reset --hard*)",
                "Shell(rm)",
            ],
        },
        "rev": {
            "allow": [
                # Tests are the Reviewer's primary deliverable.
                "Write(tests/**)",
                # Review artefact + spec frontmatter
                "Write(REVIEW.md)",
                "Write(specs/FR-*.md)",
                "Write(*.md)",
                # Same Python / runner posture as Dev
                "Shell(python)",
                "Shell(python3)",
                "Shell(py)",
                "Shell(pip)",
                "Shell(ruff)",
                "Shell(pytest)",
                "Shell(uv)",
                # Filesystem inspection
                "Shell(ls)",
                "Shell(cat)",
                "Shell(head)",
                "Shell(tail)",
                "Shell(echo)",
                "Shell(pwd)",
                "Shell(where)",
                "Shell(which)",
                # Git for inspection; push denied below. GitHub CLI removed:
                # dispatcher owns push & PR creation via `finalize --role rev`.
                "Shell(git)",
            ],
            "deny": [
                # Reviewer must NOT modify production code.
                "Write(src/**)",
                # Reviewer must NOT modify the Developer's PR body.
                "Write(PR_BODY.md)",
                "Write(.agent-team/**)",
                "Write(scripts/deploy-gate.py)",
                "Write(scripts/index-specs.py)",
                "Write(scripts/dispatch.py)",
                "Write(scripts/agent-status.py)",
                "Write(CLAUDE.md)",
                "Shell(git:push --force*)",
                "Shell(git:push -f*)",
                # Plain push also denied — dispatcher owns push.
                "Shell(git:push)",
                "Shell(git:push *)",
                "Shell(git:reset --hard*)",
                "Shell(rm)",
            ],
        },
    }
    # See ClaudeAdapter.ROLE_PERMISSIONS["bkf"] for rationale: cloned from
    # `rev` but tightened (no spec edits) and PR_BODY allowed (the
    # backfill agent owns the PR body).
    ROLE_PERMISSIONS["bkf"] = {
        "allow": [a for a in ROLE_PERMISSIONS["rev"]["allow"] if a != "Write(specs/FR-*.md)"]
        + ["Write(PR_BODY.md)"],
        "deny": [d for d in ROLE_PERMISSIONS["rev"]["deny"] if d != "Write(PR_BODY.md)"]
        + ["Write(specs/**)"],
    }

    def write_worktree_settings(self, wt_path: Path, role: str) -> None:
        perms = self.ROLE_PERMISSIONS.get(role)
        if perms is None:
            return
        settings_dir = wt_path / ".cursor"
        settings_dir.mkdir(parents=True, exist_ok=True)
        payload = {"permissions": {"allow": perms["allow"], "deny": perms["deny"]}}
        (settings_dir / "cli.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )

    def _render_prompt(self, fr_id: str, role: str) -> str:
        slash = SLASH_COMMAND_NAMES.get(role, role)
        body = _slash_command_body(slash)
        return body.replace("$ARGUMENTS", fr_id)

    @staticmethod
    def _resolve_node_entrypoint(cursor_bin: str) -> tuple[str, str] | None:
        """Resolve (node.exe, index.js) inside the cursor-agent install dir.

        On Windows, `cursor-agent` on PATH is a `.cmd` shim that calls into
        a `.ps1` script which in turn calls `node.exe versions/<latest>/index.js`.
        Both shim layers chop multi-line argv at the first `\\n` (CMD `%*`
        and PowerShell `$args` quirks), which silently truncates our role
        prompt down to its first line. We bypass the shims by invoking
        node + index.js directly via subprocess.Popen, which uses
        CreateProcess and preserves argv exactly.

        Returns None on non-Windows or if the install layout doesn't match.
        """
        if sys.platform != "win32":
            return None
        install_dir = Path(cursor_bin).resolve().parent
        versions_dir = install_dir / "versions"
        if not versions_dir.is_dir():
            return None
        version_re = re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})-[a-f0-9]+$")
        candidates: list[tuple[tuple[int, int, int], Path]] = []
        for entry in versions_dir.iterdir():
            if not entry.is_dir():
                continue
            m = version_re.match(entry.name)
            if not m:
                continue
            key = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            candidates.append((key, entry))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        latest = candidates[0][1]
        node_exe = latest / "node.exe"
        index_js = latest / "index.js"
        if not node_exe.exists() or not index_js.exists():
            return None
        return (str(node_exe), str(index_js))

    def spawn(
        self,
        fr_id: str,
        role: str,
        wt_path: Path,
        log_path: Path,
    ) -> tuple[str, int | None]:
        cursor_bin = shutil.which(self.binary_name)
        if cursor_bin is None:
            return ("binary-not-found", None)
        prompt = self._render_prompt(fr_id, role)
        node_pair = self._resolve_node_entrypoint(cursor_bin)
        if node_pair is not None:
            node_exe, index_js = node_pair
            cmd = [
                node_exe,
                index_js,
                "-p",
                "--force",
                "--trust",
                "--approve-mcps",
                "--output-format",
                "stream-json",
                prompt,
            ]
        else:
            # Non-Windows or unrecognised install layout — fall back to the
            # PATH binary. On macOS/Linux this is a real ELF/Mach-O, so the
            # multi-line argv issue doesn't apply.
            cmd = [
                cursor_bin,
                "-p",
                "--force",
                "--trust",
                "--approve-mcps",
                "--output-format",
                "stream-json",
                prompt,
            ]
        log_fh = log_path.open("wb")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=wt_path,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError:
            log_fh.close()
            return ("spawn-failed", None)
        return ("spawned", proc.pid)

    def dry_run_preview(self, fr_id: str, role: str, wt_preview: Path) -> str:
        slash = SLASH_COMMAND_NAMES.get(role, role)
        return (
            f"cursor-agent -p --force --trust --approve-mcps "
            f"--output-format stream-json '<{slash} prompt for {fr_id}>'  "
            f"(cwd={wt_preview}; perms via .cursor/cli.json)"
        )

    def parse_log(self, log_path: Path) -> AgentLogSummary:
        s = AgentLogSummary()
        with log_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = obj.get("type")
                if t == "result":
                    sub = obj.get("subtype") or "unknown"
                    s.result_subtype = sub
                    s.is_error = sub != "success"
                elif t == "tool_call":
                    sub = obj.get("subtype")
                    if sub == "started":
                        name = obj.get("name") or obj.get("tool") or "?"
                        s.tool_counts[name] = s.tool_counts.get(name, 0) + 1
                    elif sub == "completed" and obj.get("is_error"):
                        s.error_objs.append(obj)
                elif t == "assistant":
                    msg = obj.get("message") or {}
                    content = msg.get("content")
                    if isinstance(content, str):
                        s.last_text = content
                    elif isinstance(content, list):
                        parts = [
                            c.get("text", "")
                            for c in content
                            if isinstance(c, dict) and c.get("type") == "text"
                        ]
                        if parts:
                            s.last_text = "".join(parts)
        return s


_ADAPTERS: dict[str, type] = {
    ClaudeCodeAdapter.name: ClaudeCodeAdapter,
    CursorAdapter.name: CursorAdapter,
}


def get_adapter(name: str) -> HarnessAdapter:
    cls = _ADAPTERS.get(name)
    if cls is None:
        valid = ", ".join(sorted(_ADAPTERS))
        raise SystemExit(f"unknown harness {name!r}; expected one of: {valid}")
    return cls()


# ---------------------------------------------------------------------------
# FR loading + lock management (harness-agnostic)
# ---------------------------------------------------------------------------


def load_frs() -> list[dict]:
    out: list[dict] = []
    for path in sorted(SPECS_DIR.glob("FR-*.md")):
        text = path.read_text(encoding="utf-8")
        m = FRONTMATTER_PATTERN.match(text)
        if not m:
            continue
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            continue
        fr_id = str(meta.get("id", ""))
        if not fr_id.startswith("FR-"):
            continue
        out.append(
            {
                "id": fr_id,
                "status": str(meta.get("status", "")),
                "depends_on": list(meta.get("depends_on") or []),
                "path": path,
            }
        )
    return out


def runnable_frs(frs: list[dict]) -> list[dict]:
    by_id = {fr["id"]: fr for fr in frs}
    ready = []
    for fr in frs:
        if fr["status"] != "ready":
            continue
        deps_ok = all(
            by_id.get(dep, {}).get("status") in TERMINAL_STATUSES
            for dep in fr["depends_on"]
        )
        if deps_ok:
            ready.append(fr)
    return ready


# Statuses where there's Developer work that a Reviewer should look at.
# `in-progress` is the canonical post-Dev-handoff state (Dev sets it at
# implementation start, leaves it there at handoff). `in-review` covers a
# resumed/kicked-back review pass.
REVIEWABLE_STATUSES = {"in-progress", "in-review"}


def _git_branch_exists(branch: str) -> bool:
    """True iff a local branch with this exact ref name exists."""
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
        ],
        capture_output=True,
        timeout=10,
    )
    return proc.returncode == 0


def _fr_status_on_branch(fr_id: str, branch: str) -> str | None:
    """Read this FR's `status` frontmatter as it appears on `branch`.

    The Developer flips status to `in-progress` in their own worktree
    branch; in a no-PR / no-merge workflow that flip never reaches `main`,
    so a Reviewer filter that reads on-disk frontmatter would never fire.
    Reading directly from the Dev branch via `git show` closes that gap.

    Returns None if the FR file isn't present on that branch or if
    frontmatter is unreadable.
    """
    ls = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-tree", "-r", "--name-only", branch, "specs/"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if ls.returncode != 0:
        return None
    spec_path: str | None = None
    prefix = f"specs/{fr_id}-"
    for line in ls.stdout.splitlines():
        if line.startswith(prefix) and line.endswith(".md"):
            spec_path = line
            break
    if spec_path is None:
        return None
    show = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", f"{branch}:{spec_path}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if show.returncode != 0:
        return None
    m = FRONTMATTER_PATTERN.match(show.stdout)
    if m is None:
        return None
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return None
    return str(meta.get("status", "")) or None


def runnable_review_frs(frs: list[dict]) -> list[dict]:
    """FRs whose Developer wave has produced a branch and is no longer
    actively working — i.e. eligible for a Reviewer pass.

    Filter:
      * `claude/dev-FR-XXXX` branch exists locally — there is something to review.
      * Effective status (read from the Dev branch, falling back to main's
        on-disk value) is in {in-progress, in-review}. Reading from the
        Dev branch matters because in a no-PR workflow the Dev's
        status-flip never reaches main, and a Reviewer that filtered on
        main alone would never fire.
      * No held Dev lock — Dev is finished (or never started this round).

    The held-Rev-lock check happens later in cmd_tick (same as for dev),
    so a Reviewer that's already running surfaces as SKIP rather than
    being filtered out here.
    """
    out: list[dict] = []
    for fr in frs:
        dev_branch = f"claude/dev-{fr['id']}"
        if not _git_branch_exists(dev_branch):
            continue
        effective = _fr_status_on_branch(fr["id"], dev_branch) or fr["status"]
        if effective not in REVIEWABLE_STATUSES:
            continue
        dev_state, _ = lock_state(fr["id"], "dev")
        if dev_state == "held":
            continue
        out.append(fr)
    return out


def lock_path(fr_id: str, role: str) -> Path:
    return DISPATCH_DIR / f"{fr_id}.{role}.lock"


def is_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0 and f'"{pid}"' in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def kill_pid(pid: int) -> bool:
    if not is_alive(pid):
        return False
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        return not is_alive(pid)
    try:
        os.kill(pid, 15)
    except (OSError, ProcessLookupError):
        return False
    return True


def log_idle_seconds(fr_id: str, role: str, started_at: float = 0.0) -> float | None:
    log_path = DISPATCH_DIR / f"{fr_id}.{role}.log"
    if not log_path.exists():
        return None
    last_activity = max(log_path.stat().st_mtime, started_at)
    return dt.datetime.now(dt.timezone.utc).timestamp() - last_activity


def lock_state(fr_id: str, role: str) -> tuple[str, dict | None]:
    p = lock_path(fr_id, role)
    if not p.exists():
        return ("free", None)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ("corrupt", None)
    pid = int(data.get("pid", 0))
    if not is_alive(pid):
        return ("dead", data)
    age = dt.datetime.now(dt.timezone.utc).timestamp() - data.get("started_at", 0)
    if age > STALE_LOCK_SECONDS:
        return ("stale", data)
    return ("held", data)


def write_lock(fr_id: str, role: str, pid: int, **extra: object) -> None:
    DISPATCH_DIR.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "fr_id": fr_id,
        "role": role,
        "pid": pid,
        "started_at": dt.datetime.now(dt.timezone.utc).timestamp(),
        "started_at_iso": dt.datetime.now(dt.timezone.utc).isoformat(
            timespec="seconds"
        ),
    }
    payload.update(extra)
    lock_path(fr_id, role).write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Worktree + venv provisioning (harness-agnostic except for settings file)
# ---------------------------------------------------------------------------


def _find_uv() -> str | None:
    """Locate the `uv` binary. Preferred over `python -m venv` because the
    workflow already standardises on uv-managed virtualenvs and pip installs
    are noticeably faster.
    """
    found = shutil.which("uv")
    if found:
        return found
    home = Path.home()
    for candidate in (
        home / ".local" / "bin" / "uv.exe",
        home / ".local" / "bin" / "uv",
    ):
        if candidate.exists():
            return str(candidate)
    return None


# Worktree venv configuration. Override via environment variables or edit
# these defaults to match your project's dependency footprint.
VENV_DIRNAME = os.environ.get("DISPATCH_VENV_DIRNAME", ".venv-dispatch")
VENV_PACKAGES = (os.environ.get("DISPATCH_VENV_PACKAGES") or "pyyaml,ruff,pytest").split(",")
VENV_PYTHON_VERSION = os.environ.get("DISPATCH_VENV_PYTHON", "3.11")


def ensure_dev_venv(wt_path: Path) -> tuple[bool, str]:
    """Pre-provision the dispatch venv in the worktree with the deps the
    agent needs. Returns (ok, message). Idempotent: skips if venv already present.
    """
    venv_path = wt_path / VENV_DIRNAME
    py_exe = (
        venv_path / "Scripts" / "python.exe"
        if sys.platform == "win32"
        else venv_path / "bin" / "python"
    )
    if py_exe.exists():
        return (True, "venv-reused")
    uv = _find_uv()
    if uv is None:
        return (False, "uv-not-found")
    create = subprocess.run(
        [uv, "venv", str(venv_path), "--python", VENV_PYTHON_VERSION],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if create.returncode != 0:
        return (False, f"venv-create-failed:{create.stderr.strip()[:200]}")
    install = subprocess.run(
        [uv, "pip", "install", "-p", str(py_exe), *VENV_PACKAGES],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if install.returncode != 0:
        return (False, f"venv-install-failed:{install.stderr.strip()[:200]}")
    return (True, "venv-created")


def _base_branch_for_role(fr_id: str, role: str) -> str:
    """Which existing ref does a fresh role worktree branch off?

      * dev — always `main` (Dev starts from latest merged work).
      * rev — `claude/dev-FR-XXXX` if that branch exists, else `main`.
        Basing off the Dev branch is what makes the Reviewer's worktree
        actually contain the Developer's diff.
      * bkf — always `main`. Backfill mode runs against an already-merged
        FR; the implementation is on `main`, the dev branch may already
        have been deleted by `reconcile-merged`.

    For roles we don't recognise, default to `main`.
    """
    if role == "rev":
        dev_branch = f"claude/dev-{fr_id}"
        if _git_branch_exists(dev_branch):
            return dev_branch
    return "main"


def ensure_role_worktree(
    fr_id: str, role: str, adapter: HarnessAdapter
) -> tuple[Path | None, str]:
    """Create or reuse a per-FR worktree at .claude/worktrees/<role>-<FR-id>.

    On (re)entry, always rewrites the harness-appropriate settings file
    (Claude: .claude/settings.local.json; Cursor: .cursor/cli.json) and
    pre-provisions the dispatch venv so the agent never has to ask for
    env-setup approval mid-task.

    The new branch is `claude/<role>-<fr_id>`, based off `main` for Dev and
    off `claude/dev-<fr_id>` for Reviewer (so the Reviewer's worktree
    actually contains the Developer's diff).

    Returns (worktree_path, status) where status is one of: "created",
    "reused", "error:<msg>".
    """
    wt_root = REPO_ROOT / ".claude" / "worktrees"
    wt_path = wt_root / f"{role}-{fr_id}"
    branch = f"claude/{role}-{fr_id}"
    base_branch = _base_branch_for_role(fr_id, role)
    wt_status: str
    if wt_path.exists():
        wt_status = "reused"
    else:
        wt_root.mkdir(parents=True, exist_ok=True)
        add_with_new_branch = subprocess.run(
            [
                "git",
                "-C",
                str(REPO_ROOT),
                "worktree",
                "add",
                "-b",
                branch,
                str(wt_path),
                base_branch,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if add_with_new_branch.returncode == 0:
            wt_status = "created"
        else:
            add_existing_branch = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "worktree", "add", str(wt_path), branch],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if add_existing_branch.returncode == 0:
                wt_status = "created"
            else:
                err = (
                    add_with_new_branch.stderr.strip()
                    or add_existing_branch.stderr.strip()
                )
                return (None, f"error:{err}")
    adapter.write_worktree_settings(wt_path, role)
    venv_ok, venv_msg = ensure_dev_venv(wt_path)
    if not venv_ok:
        # Soft-fail: the agent can still run against system python; surface
        # the issue in the status string so the dispatcher logs it but
        # doesn't abort the spawn.
        return (wt_path, f"{wt_status},{venv_msg}")
    return (wt_path, f"{wt_status},{venv_msg}")


# Backwards-compat alias for any external callers (and for clarity in older
# logs / hooks). New code should use ensure_role_worktree directly.
ensure_dev_worktree = ensure_role_worktree


def spawn_role(
    fr_id: str, role: str, apply: bool, adapter: HarnessAdapter
) -> tuple[str, int | None, Path | None]:
    if not apply:
        return ("dry-run", None, None)
    wt_path, wt_status = ensure_role_worktree(fr_id, role, adapter)
    if wt_path is None:
        return (wt_status, None, None)
    log_path = DISPATCH_DIR / f"{fr_id}.{role}.log"
    DISPATCH_DIR.mkdir(parents=True, exist_ok=True)
    result, pid = adapter.spawn(fr_id, role, wt_path, log_path)
    if result != "spawned":
        return (result, None, wt_path)
    write_lock(
        fr_id,
        role,
        pid,
        worktree=str(wt_path),
        branch=f"claude/{role}-{fr_id}",
        base_branch=_base_branch_for_role(fr_id, role),
        worktree_status=wt_status,
        harness=adapter.name,
    )
    return ("spawned", pid, wt_path)


# ---------------------------------------------------------------------------
# Reconcile-merged: project GitHub-merged PRs back into the spec graph
# ---------------------------------------------------------------------------
#
# After a PR merges on origin/main, the dispatcher's local view goes stale
# in three places:
#   1. Spec frontmatter `status` still reads `ready` / `in-progress` /
#      `in-review`. `runnable_frs` filters on this and will re-fire the
#      same FR. Worse, dependents that gate on `status in TERMINAL_STATUSES`
#      stay blocked.
#   2. The post-merge worktree, branch, and lockfile all survive locally.
#      A re-fire reuses the stale worktree at the merged tip and either
#      no-ops confusingly or piles new commits onto an already-merged
#      branch.
#   3. No-one writes the changelog entry that records the merge.
#
# `reconcile-merged` (and the auto-tick variant of the same) closes all
# three. It treats GitHub PR state as the source of truth ("this branch
# is merged") and projects it back into the spec graph + working state.
# Read-only by default; `--apply` commits the frontmatter flips and
# removes the worktrees/branches/locks. Idempotent: re-runs against
# already-merged-and-cleaned FRs are no-ops.

_FR_BRANCH_RE = re.compile(r"^claude/(dev|rev|bkf)-(FR-\d{4})$")


@dataclasses.dataclass
class ReconcileSummary:
    flipped: list[str]
    cleaned: list[str]
    skipped: list[tuple[str, str]]
    gh_unavailable: bool = False


def _gh_merged_prs(limit: int = 100) -> list[dict] | None:
    """Returns parsed JSON of merged PRs, or None if gh is unavailable.

    Each row has shape:
      {"number": int, "headRefName": str, "mergedAt": str,
       "mergeCommit": {"oid": str}, "title": str}
    """
    nwo = _repo_nwo()
    if nwo is None:
        return None
    proc = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            nwo,
            "--state",
            "merged",
            "--limit",
            str(limit),
            "--json",
            "number,headRefName,mergedAt,mergeCommit,title",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None


def _on_default_branch(branch: str = "main") -> bool:
    """True if the primary worktree is currently on `branch`."""
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.returncode == 0 and proc.stdout.strip() == branch


def _flip_frontmatter_to_merged(
    spec_path: Path, prev_status: str, pr_number: int, merge_sha: str
) -> bool:
    """Edit `spec_path` in place: flip `status:` to `merged`, bump `updated:`,
    and append a Changelog entry citing the PR + merge SHA. Returns True iff
    the file was modified.

    Convention: Changelog is the
    final section in every spec, so a tail-append is the right surface.
    Falls back to creating a Changelog section if one is somehow missing.
    """
    text = spec_path.read_text(encoding="utf-8")
    m = FRONTMATTER_PATTERN.match(text)
    if m is None:
        return False
    fm_span = text[m.start() : m.end()]
    today = dt.date.today().isoformat()
    new_fm = re.sub(
        r"^status:\s*\S+",
        "status: merged",
        fm_span,
        count=1,
        flags=re.MULTILINE,
    )
    new_fm = re.sub(
        r"^updated:\s*\S+",
        f"updated: {today}",
        new_fm,
        count=1,
        flags=re.MULTILINE,
    )
    body = text[m.end() :]
    nwo = _repo_nwo() or "unknown/unknown"
    short_sha = merge_sha[:7] if merge_sha else "unknown"
    entry = (
        f"- {today}: status `{prev_status}` → `merged`. "
        f"PR [#{pr_number}](https://github.com/{nwo}/pull/{pr_number}) "
        f"merged into `main` as commit `{short_sha}`. "
        f"Auto-projected by `dispatch.py reconcile-merged`."
    )
    if "## Changelog" in body:
        body = body.rstrip() + "\n" + entry + "\n"
    else:
        body = body.rstrip() + "\n\n## Changelog\n\n" + entry + "\n"
    spec_path.write_text(new_fm + body, encoding="utf-8")
    return True


def _refresh_spec_index() -> bool:
    """Re-run `scripts/index-specs.py` so `specs/INDEX.md` catches up with
    the just-flipped frontmatter. Returns True on success (or no-op).
    """
    indexer = REPO_ROOT / "scripts" / "index-specs.py"
    if not indexer.exists():
        return True
    proc = subprocess.run(
        [sys.executable, str(indexer)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.returncode == 0


def _commit_spec_flip(fr_id: str, pr_number: int, merge_sha: str) -> tuple[bool, str]:
    """Stage + commit the frontmatter flip (and refreshed INDEX.md) on the
    primary worktree. Returns (ok, note).
    """
    _refresh_spec_index()
    add = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "add",
            "--",
            f"specs/{fr_id}-*.md",
            "specs/INDEX.md",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if add.returncode != 0:
        return (False, (add.stderr or add.stdout or "git add failed").strip())
    diff = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", "--cached", "--quiet"],
        capture_output=True,
        timeout=10,
    )
    if diff.returncode == 0:
        return (True, "no-op")
    short_sha = merge_sha[:7] if merge_sha else "unknown"
    msg = f"docs({fr_id}): mark merged (PR #{pr_number}, {short_sha})"
    cm = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "commit", "-m", msg],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if cm.returncode != 0:
        err = (cm.stderr or cm.stdout or "git commit failed").strip().splitlines()
        return (False, err[-1] if err else "git commit failed")
    return (True, "committed")


def _git_remove_worktree(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return (True, "absent")
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "worktree", "remove", "--force", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        return (False, err[-1] if err else "git worktree remove failed")
    return (True, "removed")


def _git_delete_branch(branch: str) -> tuple[bool, str]:
    if not _git_branch_exists(branch):
        return (True, "absent")
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "branch", "-D", branch],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        return (False, err[-1] if err else "git branch -D failed")
    return (True, "deleted")


def reconcile_merged_prs(
    apply: bool = False, verbose: bool = True
) -> ReconcileSummary:
    """Project every merged `claude/{dev,rev}-FR-XXXX` PR back into local state.

    For each FR with at least one merged PR on origin:
      * Flip frontmatter `status` -> `merged` (with date + Changelog entry),
        and commit `docs(FR-XXXX): mark merged (PR #N, <sha>)` per FR.
        Skipped if the primary worktree is not on `main` (commit would land
        on the wrong branch).
      * Remove the role's worktree, local branch, and lockfile. Skipped if
        the lock is currently `held` (an agent is actively running there).

    Idempotent: terminal-status FRs trigger only the cleanup step; FRs whose
    worktree/branch/lock are already gone are no-ops.
    """
    summary = ReconcileSummary(flipped=[], cleaned=[], skipped=[])
    ok, _ = _gh_preflight(verbose=False)
    if not ok:
        summary.gh_unavailable = True
        if verbose:
            print(
                "reconcile-merged: gh unavailable; skipping. Run `gh auth login` to enable."
            )
        return summary
    rows = _gh_merged_prs()
    if rows is None:
        summary.gh_unavailable = True
        if verbose:
            print("reconcile-merged: failed to query merged PRs from origin")
        return summary

    by_fr: dict[str, dict[str, dict | None]] = {}
    for row in rows:
        head = str(row.get("headRefName", ""))
        m = _FR_BRANCH_RE.match(head)
        if not m:
            continue
        role, fr_id = m.group(1), m.group(2)
        slot = by_fr.setdefault(fr_id, {"dev": None, "rev": None, "bkf": None})
        prev = slot[role]
        if prev is None or str(row.get("mergedAt", "")) > str(
            prev.get("mergedAt", "")
        ):
            slot[role] = row

    if not by_fr:
        if verbose:
            print(
                "reconcile-merged: no merged PRs match `claude/{dev,rev}-FR-XXXX`"
            )
        return summary

    on_main = _on_default_branch("main")

    for fr_id in sorted(by_fr):
        slot = by_fr[fr_id]
        pr_for_flip = slot["dev"] or slot["rev"]
        spec_paths = list(SPECS_DIR.glob(f"{fr_id}-*.md"))
        if not spec_paths:
            summary.skipped.append((fr_id, "no spec file under specs/"))
            if verbose:
                print(f"  {fr_id}  SKIP — no spec file")
            continue
        spec_path = spec_paths[0]
        text = spec_path.read_text(encoding="utf-8")
        m = FRONTMATTER_PATTERN.match(text)
        prev_status = ""
        if m:
            try:
                meta = yaml.safe_load(m.group(1)) or {}
                prev_status = str(meta.get("status", ""))
            except yaml.YAMLError:
                prev_status = ""

        flip_msg = ""
        if prev_status in TERMINAL_STATUSES:
            flip_msg = f"already {prev_status}"
        elif not pr_for_flip:
            flip_msg = "no PR record for flip (cleanup-only)"
        elif not on_main:
            summary.skipped.append(
                (
                    fr_id,
                    f"primary worktree off main; {prev_status} -> merged not committed",
                )
            )
            flip_msg = f"BLOCKED off-main; would flip {prev_status} -> merged"
        else:
            pr_num = int(pr_for_flip.get("number", 0))
            merge_sha = str((pr_for_flip.get("mergeCommit") or {}).get("oid", ""))
            if apply:
                modified = _flip_frontmatter_to_merged(
                    spec_path, prev_status, pr_num, merge_sha
                )
                if not modified:
                    summary.skipped.append(
                        (fr_id, "frontmatter unparseable; flip skipped")
                    )
                    flip_msg = "flip skipped (unparseable frontmatter)"
                else:
                    cm_ok, cm_note = _commit_spec_flip(fr_id, pr_num, merge_sha)
                    if cm_ok:
                        summary.flipped.append(fr_id)
                        flip_msg = (
                            f"flipped {prev_status} -> merged ({cm_note}, "
                            f"PR #{pr_num})"
                        )
                    else:
                        summary.skipped.append(
                            (fr_id, f"git commit failed: {cm_note}")
                        )
                        flip_msg = f"flipped on disk; commit failed: {cm_note}"
            else:
                summary.flipped.append(fr_id)
                flip_msg = f"WOULD flip {prev_status} -> merged (PR #{pr_num})"

        # Cleanup runs for BOTH dev and rev role artefacts whenever the dev
        # PR is merged. Rev branches don't get their own PRs (the reviewer's
        # verdict is posted as `gh pr review` against the parent dev PR), so
        # `slot["rev"]` is almost always None even when a `rev-FR-XXXX`
        # worktree exists locally. Trigger rev cleanup off the dev merge.
        # Backfill (`bkf`) PRs DO open their own PRs, so they're handled
        # independently when their own merge appears.
        roles_to_clean: list[str] = []
        if slot["dev"] is not None:
            roles_to_clean.extend(["dev", "rev"])
        elif slot["rev"] is not None:
            roles_to_clean.append("rev")
        if slot["bkf"] is not None:
            roles_to_clean.append("bkf")

        cleanup_msgs: list[str] = []
        any_cleaned = False
        for role in roles_to_clean:
            wt_path = REPO_ROOT / ".claude" / "worktrees" / f"{role}-{fr_id}"
            branch = _branch_for(fr_id, role)
            lock_p = lock_path(fr_id, role)
            wt_exists = wt_path.exists()
            br_exists = _git_branch_exists(branch)
            lock_exists = lock_p.exists()
            if not (wt_exists or br_exists or lock_exists):
                continue
            state, _ = lock_state(fr_id, role)
            if state == "held":
                cleanup_msgs.append(
                    f"{role}: lock held (agent running); cleanup skipped"
                )
                continue
            if apply:
                if wt_exists:
                    _, note = _git_remove_worktree(wt_path)
                    cleanup_msgs.append(f"{role}: worktree {note}")
                if br_exists:
                    _, note = _git_delete_branch(branch)
                    cleanup_msgs.append(f"{role}: branch {note}")
                if lock_exists:
                    lock_p.unlink(missing_ok=True)
                    cleanup_msgs.append(f"{role}: lock removed")
                any_cleaned = True
            else:
                parts = []
                if wt_exists:
                    parts.append("worktree")
                if br_exists:
                    parts.append("branch")
                if lock_exists:
                    parts.append("lock")
                cleanup_msgs.append(
                    f"{role}: WOULD remove {', '.join(parts)}"
                )
                any_cleaned = True

        if any_cleaned:
            summary.cleaned.append(fr_id)

        if verbose:
            print(f"  {fr_id}  {flip_msg}")
            for msg in cleanup_msgs:
                print(f"    - {msg}")

    return summary


def cmd_reconcile_merged(apply: bool) -> int:
    print(
        f"Reconciling merged PRs from origin "
        f"({'APPLY' if apply else 'dry-run'})..."
    )
    s = reconcile_merged_prs(apply=apply, verbose=True)
    if s.gh_unavailable:
        return 1
    n_flipped = len(s.flipped)
    n_cleaned = len(s.cleaned)
    n_skipped = len(s.skipped)
    print()
    verb_flip = "flipped" if apply else "would flip"
    verb_clean = "cleaned" if apply else "would clean"
    print(
        f"Summary: {verb_flip} {n_flipped} FR(s); "
        f"{verb_clean} {n_cleaned} FR worktree/branch/lock set(s); "
        f"skipped {n_skipped}"
    )
    if s.skipped:
        for fr_id, reason in s.skipped:
            print(f"  skip {fr_id}: {reason}")
    if not apply and (n_flipped or n_cleaned):
        print()
        print(
            "Re-run with --apply to commit frontmatter flips and remove "
            "worktrees/branches/locks."
        )
    return 0


# ---------------------------------------------------------------------------
# Tick / status / prune / kill / summary
# ---------------------------------------------------------------------------


def read_last_tick() -> dt.datetime | None:
    if not LAST_TICK_FILE.exists():
        return None
    try:
        return dt.datetime.fromisoformat(
            LAST_TICK_FILE.read_text(encoding="utf-8").strip()
        )
    except (OSError, ValueError):
        return None


def write_last_tick() -> None:
    DISPATCH_DIR.mkdir(parents=True, exist_ok=True)
    LAST_TICK_FILE.write_text(
        dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        encoding="utf-8",
    )


def new_escalations(since: dt.datetime | None) -> list[Path]:
    if not ESCALATIONS_DIR.exists():
        return []
    out = []
    for p in sorted(ESCALATIONS_DIR.glob("*.md")):
        if p.name.lower() in {"readme.md", "index.md"}:
            continue
        mtime = dt.datetime.fromtimestamp(p.stat().st_mtime, tz=dt.timezone.utc)
        if since is None or mtime > since:
            out.append(p)
    return out


ROLE_LABELS = {"dev": "Developer", "rev": "Reviewer"}


# ---------------------------------------------------------------------------
# GitHub integration (finalize)
# ---------------------------------------------------------------------------
#
# The dispatcher owns the GitHub side of the workflow. Agents produce
# PR_BODY.md (Developer) and a structured Approve / Request-changes block
# in their final assistant text (Reviewer); finalize parses those, pushes
# the branch, and runs `gh pr create` / `gh pr review` accordingly.
#
# Idempotency model:
#   * Per-(FR, role) record at _dispatch/<fr>.<role>.pr.json. For dev:
#     {pr_number, pr_url, branch, head_sha, finalized_at}. For rev:
#     {parent_pr_number, verdict, finalized_at}. Re-running finalize on
#     the same FR re-pushes the branch (fast-forward), no-ops on the PR
#     if one is open against this head, and posts a new review thread.
#   * The dispatcher never deletes branches or PRs. Cleanup is human work.

GH_AUTH_PREFLIGHT_HINT = (
    "GitHub integration disabled: `gh` CLI not on PATH or `gh auth status` failed. "
    "Run `winget install GitHub.cli` then `gh auth login --web` to enable."
)


def _gh_available() -> bool:
    if shutil.which("gh") is None:
        return False
    try:
        proc = subprocess.run(["gh", "--version"], capture_output=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _gh_authed() -> bool:
    if not _gh_available():
        return False
    try:
        proc = subprocess.run(["gh", "auth", "status"], capture_output=True, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _remote_url(remote: str = "origin") -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "config", "--get", f"remote.{remote}.url"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _repo_nwo() -> str | None:
    """Owner/repo string parsed from `origin`'s URL.

    Returns e.g. "owner/repo" for both
    `https://github.com/owner/repo.git` and
    `git@github.com:owner/repo.git`. None if the URL
    isn't a recognisable GitHub form.
    """
    url = _remote_url()
    if not url:
        return None
    https = re.match(r"^https?://[^/]+/([^/]+)/(.+?)(?:\.git)?/?$", url)
    if https:
        return f"{https.group(1)}/{https.group(2)}"
    ssh = re.match(r"^git@[^:]+:([^/]+)/(.+?)(?:\.git)?/?$", url)
    if ssh:
        return f"{ssh.group(1)}/{ssh.group(2)}"
    return None


def _fr_title(fr_id: str) -> str | None:
    for path in SPECS_DIR.glob(f"{fr_id}-*.md"):
        text = path.read_text(encoding="utf-8")
        m = FRONTMATTER_PATTERN.match(text)
        if m is None:
            return None
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            return None
        title = meta.get("title")
        return str(title) if title else None
    return None


def _branch_for(fr_id: str, role: str) -> str:
    return f"claude/{role}-{fr_id}"


def _push_branch(branch: str) -> tuple[bool, str]:
    """`git push -u origin <branch>`. Fast-forward only — no force.

    Returns (ok, message). `message` is human-readable (last line of
    git's stderr on failure; "pushed" / "up-to-date" on success).
    """
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "push", "-u", "origin", branch],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        last = err[-1] if err else "git push failed"
        return (False, last)
    out = (proc.stderr or proc.stdout or "").strip()
    if "Everything up-to-date" in out:
        return (True, "up-to-date")
    return (True, "pushed")


def _branch_head_sha(branch: str) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", branch],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _pr_record_path(fr_id: str, role: str) -> Path:
    return DISPATCH_DIR / f"{fr_id}.{role}.pr.json"


def _load_pr_record(fr_id: str, role: str) -> dict | None:
    p = _pr_record_path(fr_id, role)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_pr_record(fr_id: str, role: str, payload: dict) -> None:
    DISPATCH_DIR.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload.setdefault("fr_id", fr_id)
    payload.setdefault("role", role)
    payload["finalized_at"] = dt.datetime.now(dt.timezone.utc).isoformat(
        timespec="seconds"
    )
    _pr_record_path(fr_id, role).write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def _gh_pr_for_branch(branch: str) -> int | None:
    """Look up an open PR with this head branch via `gh pr list`."""
    nwo = _repo_nwo()
    if nwo is None:
        return None
    proc = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            nwo,
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "number",
            "--limit",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        return None
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None
    if not rows:
        return None
    return int(rows[0].get("number") or 0) or None


# Markers the Reviewer's structured handoff includes when proposing a
# review verdict. We tolerate variation across observed forms:
#   - "### Suggested GitHub review (Approve)"
#   - "### Suggested review body (Approve):"
#   - "### Suggested GitHub review (Request changes)"
#   - "### Request changes (cannot honestly Approve yet)" / **Request changes**
#   - "Cannot honestly Approve" prose (verdict implicit)
# Both regexes use `re.search` (first match wins). For FRs that emit
# competing markers (rare), the earliest-match-in-text rule chooses the
# one nearest the reviewer's primary handoff section, which is what we
# want.
_VERDICT_RE_APPROVE = re.compile(
    r"suggested\s+(?:github\s+)?review(?:\s+body)?\s*\(\s*approve\s*\)"
    r"|(?:^|\n)\s*##+\s*approve\b"
    r"|(?:^|\n)\s*\*\*approve\*\*",
    re.IGNORECASE,
)
_VERDICT_RE_REQUEST = re.compile(
    r"suggested\s+(?:github\s+)?review(?:\s+body)?\s*\(\s*request[\s-]+changes\s*\)"
    r"|(?:^|\n)\s*##+\s*request\s+changes\b"
    r"|(?:^|\n)\s*\*\*request\s+changes\*\*"
    r"|cannot\s+(?:honestly\s+)?(?:use\s+an\s+)?approve",
    re.IGNORECASE,
)


def _extract_review_verdict(text: str | None) -> tuple[str, str]:
    """Map a Reviewer's final assistant text to a (verdict, body) pair.

    verdict is one of: "approve", "request-changes", "comment".
    body is the full text passed verbatim to `gh pr review --body`. We
    do NOT attempt to slice out a sub-section: posting the entire
    structured handoff is the right surface for a human reading the PR.

    When both markers match, the one that appears EARLIEST wins. This
    matters because reviewers often mention the opposite verdict in a
    later "what to do if you disagree" footnote; the earliest match is
    almost always inside their primary handoff section.

    Falls back to "comment" when neither marker matches confidently —
    the Reviewer still gets a review thread, just not a verdict, and
    the human can decide whether to convert it.
    """
    if not text:
        return ("comment", "(reviewer log empty — finalize was called with no text)")
    approve_match = _VERDICT_RE_APPROVE.search(text)
    request_match = _VERDICT_RE_REQUEST.search(text)
    if approve_match and request_match:
        if approve_match.start() <= request_match.start():
            return ("approve", text)
        return ("request-changes", text)
    if request_match:
        return ("request-changes", text)
    if approve_match:
        return ("approve", text)
    return ("comment", text)


def _gh_preflight(verbose: bool) -> tuple[bool, str | None]:
    """Returns (ok, error_msg). Cheap to call before any finalize work."""
    if not _gh_available():
        return (False, "gh CLI not on PATH")
    if not _gh_authed():
        return (False, "gh auth status failed (run `gh auth login`)")
    if _repo_nwo() is None:
        return (
            False,
            "no `origin` remote configured (run `git remote add origin <url>`)",
        )
    return (True, None)


def cmd_finalize(
    fr_id: str,
    role: str,
    apply: bool,
    draft: bool,
    adapter: HarnessAdapter | None = None,
) -> int:
    """Push the role's branch and post the corresponding PR action.

    For role=dev: opens (or refreshes) a draft PR sourced from the
    Developer's PR_BODY.md in the worktree.

    For role=rev: posts a `gh pr review` against the parent dev PR with
    the verdict extracted from the Reviewer's final assistant text.
    """
    label = ROLE_LABELS.get(role, role)
    branch = _branch_for(fr_id, role)

    # Lock-state safety: if the agent is still running, finalize would
    # race with active commits. Refuse politely.
    state, _lock = lock_state(fr_id, role)
    if state == "held":
        print(
            f"  {fr_id}.{role}  SKIP — {label} still running (lock held); finalize after it exits"
        )
        return 1

    if not _git_branch_exists(branch):
        print(f"  {fr_id}.{role}  ERROR — branch {branch} does not exist locally")
        return 1

    ok, err = _gh_preflight(verbose=False)
    if not ok:
        print(f"  {fr_id}.{role}  ERROR — {err}")
        return 1

    head_sha = _branch_head_sha(branch)
    nwo = _repo_nwo()

    if not apply:
        if role == "dev":
            print(
                f"  {fr_id}.dev  WOULD-FINALIZE  push {branch} -> origin; "
                f"gh pr create {'--draft ' if draft else ''}--base main --head {branch} "
                f"(repo={nwo}, head={head_sha[:7] if head_sha else '?'})"
            )
        elif role == "bkf":
            print(
                f"  {fr_id}.bkf  WOULD-FINALIZE  push {branch} -> origin; "
                f"gh pr create {'--draft ' if draft else ''}--base main --head {branch} "
                f"--title 'test({fr_id}): backfill AC coverage' "
                f"(repo={nwo}, head={head_sha[:7] if head_sha else '?'})"
            )
        else:
            existing = _load_pr_record(fr_id, "dev")
            parent_pr = (existing or {}).get("pr_number")
            print(
                f"  {fr_id}.rev  WOULD-FINALIZE  push {branch} -> origin; "
                f"gh pr review #{parent_pr or '?'} --{{verdict}} --body <reviewer text>  "
                f"(repo={nwo})"
            )
        return 0

    # Push the role branch. Fast-forward only.
    push_ok, push_msg = _push_branch(branch)
    if not push_ok:
        print(f"  {fr_id}.{role}  ERROR — push failed: {push_msg}")
        return 1
    print(f"  {fr_id}.{role}  PUSHED       {branch} ({push_msg})")

    if role == "dev":
        return _finalize_dev(fr_id, branch, head_sha, nwo, draft)
    if role == "rev":
        return _finalize_rev(fr_id, branch, head_sha, nwo, adapter)
    if role == "bkf":
        return _finalize_bkf(fr_id, branch, head_sha, nwo, draft)
    print(f"  {fr_id}.{role}  ERROR — unknown role")
    return 1


def _finalize_dev(
    fr_id: str, branch: str, head_sha: str | None, nwo: str | None, draft: bool
) -> int:
    """Open or refresh a (draft) PR for the Developer's branch."""
    wt_path = REPO_ROOT / ".claude" / "worktrees" / f"dev-{fr_id}"
    pr_body_path = wt_path / "PR_BODY.md"
    if not pr_body_path.exists():
        # Try the worktree on the dev branch via git show as a fallback —
        # the body file might have been committed but worktree pruned.
        show = subprocess.run(
            [
                "git",
                "-C",
                str(REPO_ROOT),
                "show",
                f"{branch}:PR_BODY.md",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if show.returncode != 0:
            print(
                f"  {fr_id}.dev  ERROR — PR_BODY.md not found at {pr_body_path} "
                "and not present on the branch; cannot create PR"
            )
            return 1
        # Materialise to a tempfile inside _dispatch so subsequent re-runs
        # can find it (and it survives worktree removal).
        pr_body_path = DISPATCH_DIR / f"{fr_id}.dev.pr_body.md"
        pr_body_path.write_text(show.stdout, encoding="utf-8")

    title_subject = _fr_title(fr_id) or fr_id
    pr_title = f"feat({fr_id}): {title_subject}"

    existing_pr = _gh_pr_for_branch(branch)
    record = _load_pr_record(fr_id, "dev") or {}
    if existing_pr is not None:
        # PR already open against this head: don't recreate. Surface and
        # update the local record so it stays accurate.
        nwo_url = f"https://github.com/{nwo}/pull/{existing_pr}"
        record.update(
            {
                "pr_number": existing_pr,
                "pr_url": nwo_url,
                "branch": branch,
                "head_sha": head_sha,
                "draft": draft,
                "title": pr_title,
                "note": "PR already open; commits pushed as fast-forward",
            }
        )
        _save_pr_record(fr_id, "dev", record)
        print(f"  {fr_id}.dev  PR-EXISTS    #{existing_pr}  {nwo_url}")
        return 0

    # Create a fresh PR.
    cmd = [
        "gh",
        "pr",
        "create",
        "--repo",
        nwo or "",
        "--base",
        "main",
        "--head",
        branch,
        "--title",
        pr_title,
        "--body-file",
        str(pr_body_path),
    ]
    if draft:
        cmd.append("--draft")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        last = err[-1] if err else "gh pr create failed"
        print(f"  {fr_id}.dev  ERROR — gh pr create failed: {last}")
        return 1
    pr_url = (proc.stdout or "").strip().splitlines()[-1]
    pr_number_match = re.search(r"/pull/(\d+)$", pr_url)
    pr_number = int(pr_number_match.group(1)) if pr_number_match else None
    record.update(
        {
            "pr_number": pr_number,
            "pr_url": pr_url,
            "branch": branch,
            "head_sha": head_sha,
            "draft": draft,
            "title": pr_title,
        }
    )
    _save_pr_record(fr_id, "dev", record)
    tag = "DRAFT-OPENED" if draft else "PR-OPENED"
    print(f"  {fr_id}.dev  {tag} #{pr_number}  {pr_url}")
    return 0


def _finalize_bkf(
    fr_id: str, branch: str, head_sha: str | None, nwo: str | None, draft: bool
) -> int:
    """Open or refresh a PR for the Reviewer-in-backfill-mode branch.

    Mirrors `_finalize_dev` but uses the bkf worktree path and a
    backfill-specific PR title (`test(FR-XXXX): backfill AC coverage`).
    PR_BODY.md is the agent's own deliverable in this mode.
    """
    wt_path = REPO_ROOT / ".claude" / "worktrees" / f"bkf-{fr_id}"
    pr_body_path = wt_path / "PR_BODY.md"
    if not pr_body_path.exists():
        show = subprocess.run(
            [
                "git",
                "-C",
                str(REPO_ROOT),
                "show",
                f"{branch}:PR_BODY.md",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if show.returncode != 0:
            print(
                f"  {fr_id}.bkf  ERROR — PR_BODY.md not found at {pr_body_path} "
                "and not present on the branch; cannot create PR"
            )
            return 1
        pr_body_path = DISPATCH_DIR / f"{fr_id}.bkf.pr_body.md"
        pr_body_path.write_text(show.stdout, encoding="utf-8")

    pr_title = f"test({fr_id}): backfill AC coverage"

    existing_pr = _gh_pr_for_branch(branch)
    record = _load_pr_record(fr_id, "bkf") or {}
    if existing_pr is not None:
        nwo_url = f"https://github.com/{nwo}/pull/{existing_pr}"
        record.update(
            {
                "pr_number": existing_pr,
                "pr_url": nwo_url,
                "branch": branch,
                "head_sha": head_sha,
                "draft": draft,
                "title": pr_title,
                "note": "PR already open; commits pushed as fast-forward",
            }
        )
        _save_pr_record(fr_id, "bkf", record)
        print(f"  {fr_id}.bkf  PR-EXISTS    #{existing_pr}  {nwo_url}")
        return 0

    cmd = [
        "gh",
        "pr",
        "create",
        "--repo",
        nwo or "",
        "--base",
        "main",
        "--head",
        branch,
        "--title",
        pr_title,
        "--body-file",
        str(pr_body_path),
    ]
    if draft:
        cmd.append("--draft")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        last = err[-1] if err else "gh pr create failed"
        print(f"  {fr_id}.bkf  ERROR — gh pr create failed: {last}")
        return 1
    pr_url = (proc.stdout or "").strip().splitlines()[-1]
    pr_number_match = re.search(r"/pull/(\d+)$", pr_url)
    pr_number = int(pr_number_match.group(1)) if pr_number_match else None
    record.update(
        {
            "pr_number": pr_number,
            "pr_url": pr_url,
            "branch": branch,
            "head_sha": head_sha,
            "draft": draft,
            "title": pr_title,
        }
    )
    _save_pr_record(fr_id, "bkf", record)
    tag = "DRAFT-OPENED" if draft else "PR-OPENED"
    print(f"  {fr_id}.bkf  {tag} #{pr_number}  {pr_url}")
    return 0


def _finalize_rev(
    fr_id: str,
    branch: str,
    head_sha: str | None,
    nwo: str | None,
    adapter: HarnessAdapter | None,
) -> int:
    """Post a gh pr review against the parent dev PR."""
    dev_record = _load_pr_record(fr_id, "dev")
    parent_pr = (dev_record or {}).get("pr_number")
    if parent_pr is None:
        # Fallback: look up the open PR for the dev branch directly.
        parent_pr = _gh_pr_for_branch(_branch_for(fr_id, "dev"))
    if parent_pr is None:
        print(
            f"  {fr_id}.rev  ERROR — no parent PR for {_branch_for(fr_id, 'dev')}; "
            "run `dispatch.py finalize FR-XXXX --role dev --apply` first"
        )
        return 1

    log_path = DISPATCH_DIR / f"{fr_id}.rev.log"
    if not log_path.exists():
        print(f"  {fr_id}.rev  ERROR — no reviewer log at {log_path}")
        return 1
    if adapter is None:
        adapter = _adapter_for_lock(fr_id, "rev")
    summary = adapter.parse_log(log_path)
    verdict, body = _extract_review_verdict(summary.last_text)

    # gh pr review's body has a 65k char limit; truncate aggressively
    # and link to the log file for the full text.
    MAX_BODY = 60000
    if len(body) > MAX_BODY:
        body = (
            body[:MAX_BODY] + "\n\n---\n_Reviewer body truncated for GitHub. "
            f"Full transcript: `_dispatch/{fr_id}.rev.log`._"
        )

    body_path = DISPATCH_DIR / f"{fr_id}.rev.review_body.md"
    body_path.write_text(body, encoding="utf-8")

    flag_for_verdict = {
        "approve": "--approve",
        "request-changes": "--request-changes",
        "comment": "--comment",
    }
    flag = flag_for_verdict[verdict]

    cmd = [
        "gh",
        "pr",
        "review",
        str(parent_pr),
        "--repo",
        nwo or "",
        flag,
        "--body-file",
        str(body_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    posted_as = verdict
    fallback_note: str | None = None
    if proc.returncode != 0:
        stderr = (proc.stderr or proc.stdout or "").strip()
        # GitHub forbids approve/request-changes on PRs the reviewer
        # authored. When that fires, retry as a plain --comment with the
        # verdict prepended to the body so the verdict is still legible.
        own_pr = "your own pull request" in stderr.lower()
        if own_pr and verdict in ("approve", "request-changes"):
            verdict_label = "APPROVE" if verdict == "approve" else "REQUEST CHANGES"
            fallback_body = (
                f"> **Reviewer verdict: {verdict_label}**  \n"
                f"> _Posted as a comment because GitHub does not permit "
                f"`{flag}` on a PR authored by the same account. The full "
                f"reviewer handoff follows verbatim._\n\n"
                f"---\n\n{body}"
            )
            body_path.write_text(fallback_body, encoding="utf-8")
            cmd_fb = [
                "gh",
                "pr",
                "review",
                str(parent_pr),
                "--repo",
                nwo or "",
                "--comment",
                "--body-file",
                str(body_path),
            ]
            proc = subprocess.run(cmd_fb, capture_output=True, text=True, timeout=60)
            if proc.returncode == 0:
                posted_as = f"comment (intended {verdict})"
                fallback_note = "own-PR fallback to --comment"
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip().splitlines()
            last = err[-1] if err else "gh pr review failed"
            print(f"  {fr_id}.rev  ERROR — gh pr review failed: {last}")
            return 1

    record = _load_pr_record(fr_id, "rev") or {}
    record.update(
        {
            "parent_pr_number": parent_pr,
            "verdict": verdict,
            "posted_as": posted_as,
            "head_sha": head_sha,
            "body_chars": len(body),
            "rev_branch_pushed": branch,
        }
    )
    if fallback_note:
        record["note"] = fallback_note
    _save_pr_record(fr_id, "rev", record)
    pr_url = f"https://github.com/{nwo}/pull/{parent_pr}"
    print(
        f"  {fr_id}.rev  REVIEW-POSTED  #{parent_pr}  verdict={verdict}"
        f"{' (posted as comment, see body)' if fallback_note else ''}  {pr_url}"
    )
    return 0


def cmd_finalize_all(role: str, apply: bool, draft: bool) -> int:
    """Finalize every FR whose role branch exists locally and whose lock
    is not held. Useful after a wave to push everything in one shot.
    """
    ok, err = _gh_preflight(verbose=False)
    if not ok:
        print(f"finalize-all aborted: {err}")
        return 1

    candidates: list[str] = []
    for fr in load_frs():
        fr_id = fr["id"]
        branch = _branch_for(fr_id, role)
        if not _git_branch_exists(branch):
            continue
        # Don't reopen PRs for FRs that are already merged/done/deployed.
        # Their dev branches survive locally for archaeology but the PR
        # work is over.
        if fr["status"] in TERMINAL_STATUSES:
            print(f"  {fr_id}.{role}  SKIP — FR status={fr['status']} (terminal)")
            continue
        state, _ = lock_state(fr_id, role)
        if state == "held":
            print(f"  {fr_id}.{role}  SKIP — lock held")
            continue
        candidates.append(fr_id)

    if not candidates:
        print(
            f"No FRs eligible for finalize (no `claude/{role}-*` branches with finished agents)."
        )
        return 0

    print(
        f"Finalizing {role} branches for: {candidates}  "
        f"({'APPLY' if apply else 'dry-run'}, draft={draft if role == 'dev' else 'n/a'})"
    )
    rc_total = 0
    for fr_id in candidates:
        rc = cmd_finalize(fr_id, role, apply=apply, draft=draft, adapter=None)
        if rc != 0:
            rc_total = rc
    return rc_total


def cmd_backfill(fr_id: str, apply: bool, adapter: HarnessAdapter) -> int:
    """One-shot Reviewer-in-backfill-mode spawn for an already-merged FR.

    Use case: `reconcile-merged` flipped an FR to `merged` and the deploy
    gate now reports missing `@covers` annotations because the original
    PR landed without proper test coverage. This command spawns a
    Reviewer session in backfill mode against `main`, with explicit
    instructions (see `.claude/commands/reviewer-backfill.md`) to write
    only the missing tests, run the gate, and produce a small follow-up
    PR titled `test(FR-XXXX): backfill AC coverage`.

    Branch: `claude/bkf-FR-XXXX`. Worktree: `.claude/worktrees/bkf-FR-XXXX`.
    No tick-time scheduling — this is one-shot, dispatched per-FR by hand.
    """
    label = ROLE_LABELS.get("bkf", "Reviewer (backfill)")
    state, lock = lock_state(fr_id, "bkf")
    if state == "held":
        print(
            f"  {fr_id}.bkf  SKIP — {label} lock held "
            f"(pid={lock['pid']}, since {lock['started_at_iso']})"
        )
        return 1
    if state == "stale":
        print(
            f"  {fr_id}.bkf  WARN — stale {label} lock "
            f"(pid={lock['pid']}, started {lock['started_at_iso']}); not auto-clearing"
        )
        return 1
    if state == "dead":
        print(
            f"  {fr_id}.bkf  SKIP — orphaned lock "
            f"(pid={lock['pid']} no longer alive); run `dispatch.py prune`"
        )
        return 1
    if state == "corrupt":
        print(
            f"  {fr_id}.bkf  WARN — corrupt lockfile at {lock_path(fr_id, 'bkf')}; skipping"
        )
        return 1

    spec_paths = list(SPECS_DIR.glob(f"{fr_id}-*.md"))
    if not spec_paths:
        print(f"  {fr_id}.bkf  ERROR — no spec at specs/{fr_id}-*.md")
        return 1

    result, pid, wt_path = spawn_role(fr_id, "bkf", apply=apply, adapter=adapter)
    if result == "dry-run":
        wt_preview = REPO_ROOT / ".claude" / "worktrees" / f"bkf-{fr_id}"
        preview = adapter.dry_run_preview(fr_id, "bkf", wt_preview)
        print(f"  {fr_id}.bkf  WOULD-SPAWN  {preview}  (base=main)")
        return 0
    if result == "spawned":
        print(
            f"  {fr_id}.bkf  SPAWNED      pid={pid}, worktree={wt_path}, "
            f"log={DISPATCH_DIR / f'{fr_id}.bkf.log'}"
        )
        return 0
    if result == "binary-not-found":
        hint = (
            "install via `winget install Anthropic.ClaudeCode`"
            if adapter.name == "claude-code"
            else "install Cursor + run `cursor-agent login`, or set DISPATCH_HARNESS=claude-code"
        )
        print(
            f"  {fr_id}.bkf  ERROR        `{adapter.binary_name}` CLI not on PATH; {hint}"
        )
        return 1
    print(f"  {fr_id}.bkf  ERROR        {result}")
    return 1


def cmd_tick(apply: bool, adapter: HarnessAdapter, role: str = "dev") -> int:
    role_label = ROLE_LABELS.get(role, role)
    frs = load_frs()
    if not frs:
        print("No FRs found in specs/")
        return 0

    # Warn-only preflight: tick proceeds even when gh is unavailable so
    # local-only flows aren't blocked. finalize will surface the error
    # again at finalize-time.
    gh_ok, gh_err = _gh_preflight(verbose=False)
    if not gh_ok:
        print(f"NOTE: GitHub PR finalization disabled ({gh_err}). Tick continues.")

    pruned = prune_dead_locks(verbose=False)
    if pruned:
        print(f"Auto-pruned {pruned} dead lock(s) before tick.")

    # Project merged PRs back into the spec graph + working state. Without
    # this, a `tick` after a PR merge re-fires the same FR (status still
    # reads `ready` on main) and dependents stay blocked. Runs in the same
    # mode as the surrounding tick: `--apply` commits the flips and removes
    # the worktrees; dry-run prints intent only. NO-OP when gh is offline.
    if gh_ok:
        recon = reconcile_merged_prs(apply=apply, verbose=False)
        if recon.flipped or recon.cleaned:
            verb = "Reconciled merged PRs" if apply else "WOULD reconcile merged PRs"
            bits = []
            if recon.flipped:
                bits.append(
                    f"{'flipped' if apply else 'flip'} status: {sorted(set(recon.flipped))}"
                )
            if recon.cleaned:
                bits.append(
                    f"{'cleaned' if apply else 'clean'} worktree/branch/lock for: {sorted(set(recon.cleaned))}"
                )
            print(f"{verb} — {'; '.join(bits)}")
            for fr_id, reason in recon.skipped:
                print(f"  reconcile skip {fr_id}: {reason}")
            if apply and recon.flipped:
                # Re-load FRs so runnable_frs reads the post-flip state.
                frs = load_frs()

    last_tick = read_last_tick()
    if role == "rev":
        runnable = runnable_review_frs(frs)
        empty_msg = (
            "No runnable FRs for Reviewer "
            "(need status in {in-progress,in-review}, dev branch present, no held dev lock)."
        )
    else:
        runnable = runnable_frs(frs)
        empty_msg = "No runnable FRs (none with status=ready and deps fully merged)."

    if not runnable:
        print(empty_msg)
    else:
        print(
            f"Runnable FRs for {role_label} "
            f"({'APPLY' if apply else 'dry-run'}, harness={adapter.name}): "
            f"{[fr['id'] for fr in runnable]}"
        )

    for fr in runnable:
        state, lock = lock_state(fr["id"], role)
        if state == "held":
            print(
                f"  {fr['id']}  SKIP — {role_label} lock held (pid={lock['pid']}, since {lock['started_at_iso']})"
            )
            continue
        if state == "stale":
            print(
                f"  {fr['id']}  WARN — stale {role_label} lock (pid={lock['pid']}, started {lock['started_at_iso']}); not auto-clearing"
            )
            continue
        if state == "dead":
            print(
                f"  {fr['id']}  SKIP — orphaned lock (pid={lock['pid']} no longer alive); run `dispatch.py prune`"
            )
            continue
        if state == "corrupt":
            print(
                f"  {fr['id']}  WARN — corrupt lockfile at {lock_path(fr['id'], role)}; skipping"
            )
            continue
        result, pid, wt_path = spawn_role(fr["id"], role, apply=apply, adapter=adapter)
        if result == "dry-run":
            wt_preview = REPO_ROOT / ".claude" / "worktrees" / f"{role}-{fr['id']}"
            preview = adapter.dry_run_preview(fr["id"], role, wt_preview)
            base = _base_branch_for_role(fr["id"], role)
            print(f"  {fr['id']}  WOULD-SPAWN  {preview}  (base={base})")
        elif result == "spawned":
            print(
                f"  {fr['id']}  SPAWNED      pid={pid}, worktree={wt_path}, log={DISPATCH_DIR / f'{fr['id']}.{role}.log'}"
            )
        elif result == "binary-not-found":
            hint = (
                "install via `winget install Anthropic.ClaudeCode`"
                if adapter.name == "claude-code"
                else "install Cursor + run `cursor-agent login`, or set DISPATCH_HARNESS=claude-code"
            )
            print(
                f"  {fr['id']}  ERROR        `{adapter.binary_name}` CLI not on PATH; {hint}"
            )
        elif result == "spawn-failed":
            print(
                f"  {fr['id']}  ERROR        spawn failed (OSError); see {DISPATCH_DIR / f'{fr['id']}.{role}.log'}"
            )
        elif result.startswith("error:"):
            print(f"  {fr['id']}  ERROR        worktree setup failed: {result[6:]}")

    escalations = new_escalations(last_tick)
    if escalations:
        print()
        print(f"New escalations since last tick ({last_tick or 'never'}):")
        for p in escalations:
            print(
                f"  - {p.relative_to(REPO_ROOT)}  ({dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec='seconds')})"
            )

    if apply:
        write_last_tick()
    return 0


def _fmt_secs(s: float) -> str:
    s = int(s)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    h, m = divmod(s // 60, 60)
    return f"{h}h{m}m"


def cmd_status() -> int:
    print(f"Dispatch dir: {DISPATCH_DIR}")
    if not DISPATCH_DIR.exists():
        print("  (does not exist yet — no ticks have run)")
        return 0
    locks = sorted(DISPATCH_DIR.glob("*.lock"))
    if not locks:
        print("  No active locks.")
    else:
        print(f"  {len(locks)} lock(s):")
        for lp in locks:
            stem = lp.stem
            try:
                fr_id, role = stem.rsplit(".", 1)
            except ValueError:
                fr_id, role = stem, "dev"
            state, data = lock_state(fr_id, role)
            if state == "corrupt" or data is None:
                print(f"    {lp.name}  CORRUPT")
                continue
            pid = data.get("pid")
            harness = data.get("harness", "claude-code")
            started_at = data.get("started_at", 0)
            uptime = dt.datetime.now(dt.timezone.utc).timestamp() - started_at
            idle = log_idle_seconds(fr_id, role, started_at=started_at)
            log_path = DISPATCH_DIR / f"{fr_id}.{role}.log"
            log_size = log_path.stat().st_size if log_path.exists() else 0
            if state == "dead":
                tag = "DEAD     "
                hint = "(process exited; run `prune`)"
            elif state == "stale":
                tag = "STALE    "
                hint = f"(alive >{_fmt_secs(STALE_LOCK_SECONDS)} — verify manually)"
            elif idle is not None and idle > IDLE_LOG_THRESHOLD_SECONDS:
                tag = f"IDLE {_fmt_secs(idle):>5}"
                hint = "(alive but log static — likely permission-blocked)"
            else:
                tag = "WORKING  "
                hint = f"(log last write {_fmt_secs(idle) if idle is not None else '?'} ago)"
            print(
                f"    {lp.name}  pid={pid}  harness={harness}  {tag}  uptime={_fmt_secs(uptime)}  log={log_size}B  {hint}"
            )
    last = read_last_tick()
    print(f"Last tick: {last.isoformat(timespec='seconds') if last else 'never'}")
    return 0


def prune_dead_locks(verbose: bool = True) -> int:
    """Remove locks whose process is dead or whose JSON is corrupt.

    Returns the count pruned. `verbose=True` prints one line per removal;
    callers that want a quieter, summary-only output (e.g. cmd_tick) can
    pass False.
    """
    if not DISPATCH_DIR.exists():
        return 0
    pruned = 0
    for lp in sorted(DISPATCH_DIR.glob("*.lock")):
        stem = lp.stem
        try:
            fr_id, role = stem.rsplit(".", 1)
        except ValueError:
            continue
        state, data = lock_state(fr_id, role)
        if state == "dead":
            lp.unlink()
            if verbose:
                pid = data.get("pid") if data else "?"
                print(f"  pruned {lp.name} (pid={pid} no longer alive)")
            pruned += 1
        elif state == "corrupt":
            lp.unlink()
            if verbose:
                print(f"  pruned {lp.name} (corrupt)")
            pruned += 1
    return pruned


def cmd_prune() -> int:
    if not DISPATCH_DIR.exists():
        print("No dispatch dir; nothing to prune.")
        return 0
    pruned = prune_dead_locks(verbose=True)
    if pruned == 0:
        print("No prunable locks (all live processes; nothing to clean).")
    else:
        print(f"Pruned {pruned} lock(s).")
    return 0


def cmd_kill(fr_id: str, role: str) -> int:
    state, data = lock_state(fr_id, role)
    if state == "free":
        print(f"No lock for {fr_id}.{role}; nothing to kill.")
        return 0
    if state == "corrupt" or data is None:
        print(f"Lock for {fr_id}.{role} is corrupt; removing.")
        lock_path(fr_id, role).unlink(missing_ok=True)
        return 0
    pid = int(data.get("pid", 0))
    wt = data.get("worktree") if data else None
    if state == "dead":
        print(f"Process pid={pid} for {fr_id}.{role} already dead; removing lock.")
        lock_path(fr_id, role).unlink(missing_ok=True)
    else:
        killed = kill_pid(pid)
        if killed:
            print(f"Killed pid={pid} for {fr_id}.{role}.")
        else:
            print(
                f'WARN: failed to kill pid={pid}; check manually with `tasklist /FI "PID eq {pid}"`.'
            )
        lock_path(fr_id, role).unlink(missing_ok=True)
        print(f"Removed lock {lock_path(fr_id, role).name}.")
    print()
    if wt:
        print(f"Worktree at {wt} preserved (any partial work intact).")
        print("To discard and re-fire fresh, manually run:")
        print(f'  git -C "{REPO_ROOT}" worktree remove --force "{wt}"')
        print(f'  git -C "{REPO_ROOT}" branch -D claude/{role}-{fr_id}')
    print(
        "Reminder: this did NOT touch the FR frontmatter on main. If the killed agent flipped"
    )
    print(
        "the FR's status in its worktree branch, that branch is isolated — main's status is"
    )
    print(
        "untouched, so re-firing tick will see the FR as still runnable (assuming worktree removed)."
    )
    return 0


def _adapter_for_lock(fr_id: str, role: str) -> HarnessAdapter:
    """Resolve which adapter to use for log parsing/status display.

    Reads the lockfile's `harness` field if present (newer locks); falls
    back to claude-code for legacy locks written before the cursor adapter
    landed.
    """
    state, data = lock_state(fr_id, role)
    if data and isinstance(data, dict):
        return get_adapter(str(data.get("harness", "claude-code")))
    return get_adapter("claude-code")


def cmd_summary(fr_id: str, role: str, harness: str | None) -> int:
    log_path = DISPATCH_DIR / f"{fr_id}.{role}.log"
    if not log_path.exists():
        print(f"No log at {log_path}")
        return 1
    adapter = get_adapter(harness) if harness else _adapter_for_lock(fr_id, role)
    s = adapter.parse_log(log_path)
    state, lock_data = lock_state(fr_id, role)
    print(f"=== {fr_id}.{role} summary (harness={adapter.name}) ===")
    print(f"Lock state:  {state}")
    if lock_data:
        wt = lock_data.get("worktree")
        print(
            f"  pid={lock_data.get('pid')}  started={lock_data.get('started_at_iso')}"
        )
        if wt:
            print(f"  worktree={wt}")
            print(f"  branch=claude/{role}-{fr_id}")
    print(
        f"Result:      {s.result_subtype or '(no result line — process likely killed mid-run)'}{' (ERROR)' if s.is_error else ''}"
    )
    print(f"Tool calls:  {s.tool_counts or '(none recorded)'}")
    if s.error_objs:
        print(f"Tool errors: {len(s.error_objs)}")
        for e in s.error_objs[-3:]:
            content = e.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    str(c.get("text", "")) for c in content if isinstance(c, dict)
                )
            print(f"  - {str(content)[:200]}")
    print()
    print("Final assistant text:")
    if s.last_text:
        for line in s.last_text.splitlines():
            print(f"  {line}")
    else:
        print("  (no text emitted)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Auto-dispatch Developer waves on FR readiness"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_harness(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--harness",
            choices=sorted(_ADAPTERS),
            default=DEFAULT_HARNESS,
            help=f"agent harness to use (default: {DEFAULT_HARNESS}; env: DISPATCH_HARNESS)",
        )

    p_tick = sub.add_parser(
        "tick", help="Fire next Developer or Reviewer wave (dry-run unless --apply)"
    )
    p_tick.add_argument(
        "--apply", action="store_true", help="Actually spawn agent sessions"
    )
    p_tick.add_argument(
        "--role",
        choices=("dev", "rev"),
        default="dev",
        help="dev: spawn Developers for ready FRs (default). rev: spawn Reviewers for FRs whose dev branch exists and dev is finished.",
    )
    add_harness(p_tick)

    sub.add_parser("status", help="Show liveness/idle/dead per lock")
    sub.add_parser("prune", help="Remove locks whose process is no longer alive")

    p_kill = sub.add_parser(
        "kill", help="Force-terminate a running role + remove its lock"
    )
    p_kill.add_argument("fr_id", help="FR id (e.g., FR-0002)")
    p_kill.add_argument(
        "--role",
        choices=("dev", "rev", "bkf"),
        default="dev",
        help="role label (default: dev)",
    )

    p_sum = sub.add_parser(
        "summary",
        help="Pretty-print the result + tool counts + final message from a log",
    )
    p_sum.add_argument("fr_id", help="FR id (e.g., FR-0002)")
    p_sum.add_argument(
        "--role",
        choices=("dev", "rev", "bkf"),
        default="dev",
        help="role label (default: dev)",
    )
    p_sum.add_argument(
        "--harness",
        choices=sorted(_ADAPTERS),
        default=None,
        help="override harness for log parsing (default: read from lockfile, fallback claude-code)",
    )

    p_fin = sub.add_parser(
        "finalize",
        help="Push the role branch and post the PR (dev) or review (rev). Idempotent.",
    )
    p_fin.add_argument("fr_id", help="FR id (e.g., FR-XXXX)")
    p_fin.add_argument(
        "--role",
        choices=("dev", "rev", "bkf"),
        default="dev",
        help=(
            "dev: push + open draft PR. rev: push + post gh pr review against "
            "parent. bkf: push + open backfill PR (`test(FR-XXXX): backfill AC coverage`)."
        ),
    )
    p_fin.add_argument(
        "--apply",
        action="store_true",
        help="Actually push and call gh (dry-run by default)",
    )
    p_fin.add_argument(
        "--ready",
        action="store_true",
        help="(dev only) Open the PR as ready-for-review instead of --draft",
    )

    p_bkf = sub.add_parser(
        "backfill",
        help=(
            "One-shot Reviewer-in-backfill-mode spawn. Branches off `main` "
            "and writes the AC tests an already-merged FR was missing. "
            "Opens a follow-up PR titled `test(FR-XXXX): backfill AC coverage`."
        ),
    )
    p_bkf.add_argument("fr_id", help="FR id (e.g., FR-XXXX)")
    p_bkf.add_argument(
        "--apply",
        action="store_true",
        help="Actually spawn the agent (dry-run by default)",
    )
    add_harness(p_bkf)

    p_recon = sub.add_parser(
        "reconcile-merged",
        help=(
            "Project merged PR state from origin back into local state: "
            "flip FR frontmatter status to `merged` (commit per-FR) and "
            "remove the corresponding worktrees/branches/locks. Idempotent."
        ),
    )
    p_recon.add_argument(
        "--apply",
        action="store_true",
        help="Actually edit specs, commit, and clean working state (default: dry-run)",
    )

    p_finall = sub.add_parser(
        "finalize-all",
        help="Finalize every FR whose role branch exists and lock is free. Idempotent.",
    )
    p_finall.add_argument(
        "--role",
        choices=("dev", "rev", "bkf"),
        default="dev",
        help="dev (default), rev, or bkf",
    )
    p_finall.add_argument(
        "--apply",
        action="store_true",
        help="Actually push and call gh (dry-run by default)",
    )
    p_finall.add_argument(
        "--ready",
        action="store_true",
        help="(dev only) Open PRs as ready-for-review instead of --draft",
    )

    args = parser.parse_args()
    if args.cmd == "tick":
        adapter = get_adapter(args.harness)
        return cmd_tick(apply=args.apply, adapter=adapter, role=args.role)
    if args.cmd == "status":
        return cmd_status()
    if args.cmd == "prune":
        return cmd_prune()
    if args.cmd == "kill":
        return cmd_kill(args.fr_id, args.role)
    if args.cmd == "summary":
        return cmd_summary(args.fr_id, args.role, args.harness)
    if args.cmd == "finalize":
        return cmd_finalize(
            args.fr_id, args.role, apply=args.apply, draft=not args.ready
        )
    if args.cmd == "finalize-all":
        return cmd_finalize_all(args.role, apply=args.apply, draft=not args.ready)
    if args.cmd == "reconcile-merged":
        return cmd_reconcile_merged(apply=args.apply)
    if args.cmd == "backfill":
        adapter = get_adapter(args.harness)
        return cmd_backfill(args.fr_id, apply=args.apply, adapter=adapter)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
