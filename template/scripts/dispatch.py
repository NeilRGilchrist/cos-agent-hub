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
  python scripts/dispatch.py maintain FR-XXXX                # show one-shot maintainer plan (dry-run)
  python scripts/dispatch.py maintain FR-XXXX --apply        # spawn Developer-in-maintainer-mode for a control-plane FR (footprint from `owns:`)
  python scripts/dispatch.py finalize FR-XXXX --role mnt --apply  # push + open `chore(FR-XXXX): <title>` PR

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
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import time
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
    # `mnt` is a one-shot Developer-in-maintainer-mode used for FRs whose
    # declared `owns:` footprint intersects the control-plane paths every
    # other role denies (`.agent-team/**`, the gate/indexer/dispatcher
    # scripts). Its writable footprint is derived from that FR's `owns:` —
    # see MNT_PROTECTED_GLOBS and mnt_permission_rules. Dev-shaped:
    # branches off `main`, authors PR_BODY.md, opens its own PR.
    "mnt": "maintainer",
}

# Role -> Claude Code --model alias. The Reviewer and its backfill variant run
# on the stronger model (adversarial coverage review benefits most from it);
# the implementation roles (dev, mnt) run on the faster model. Override per
# role with env DISPATCH_MODEL_<ROLE> (e.g. DISPATCH_MODEL_DEV=opus). A role
# absent from this map and unset in the env gets no --model flag, so the
# harness default applies. Only the Claude Code adapter consumes this; Cursor
# selects models through its own config.
ROLE_MODEL: dict[str, str] = {
    "dev": "sonnet",
    "rev": "opus",
    "bkf": "opus",
    "mnt": "sonnet",
}


def _resolve_model(role: str) -> str | None:
    """Resolve the Claude Code --model alias for a role.

    Env override DISPATCH_MODEL_<ROLE> (role uppercased) wins; otherwise
    ROLE_MODEL supplies the default. Returns None when neither is set, in
    which case the caller passes no --model flag and the harness default
    applies.
    """
    override = os.environ.get(f"DISPATCH_MODEL_{role.upper()}")
    if override and override.strip():
        return override.strip()
    return ROLE_MODEL.get(role)


DEFAULT_HARNESS = os.environ.get("DISPATCH_HARNESS", "claude-code")

# Worktree venv configuration. Override via environment variables or edit
# these defaults to match your project's dependency footprint. Defined here
# (before the adapter classes) because ROLE_PERMISSIONS interpolates
# VENV_DIRNAME at class-definition time.
VENV_DIRNAME = os.environ.get("DISPATCH_VENV_DIRNAME", ".venv-dispatch")
VENV_PACKAGES = (os.environ.get("DISPATCH_VENV_PACKAGES") or "pyyaml,ruff,pytest").split(",")
VENV_PYTHON_VERSION = os.environ.get("DISPATCH_VENV_PYTHON", "3.11")

# Paths every role's harness permissions deny. `mnt` un-denies exactly the
# subset the target FR declares in `owns:` — never more, and never CLAUDE.md.
MNT_PROTECTED_GLOBS = (
    ".agent-team/**",
    "scripts/deploy-gate.py",
    "scripts/index-specs.py",
    "scripts/dispatch.py",
    "scripts/agent-status.py",
    "CLAUDE.md",
)

# An agent that can rewrite the working agreement can rewrite its own
# constraints, so this is denied to `mnt` whatever an FR claims to own.
MNT_NEVER_WRITABLE = ("CLAUDE.md",)

# Paths the harness write-guards on its own, independently of any settings
# file. Granting one reads as permission the agent does not actually have,
# which pushes it toward writing the file from an interpreter instead. A
# `mnt` spawn whose `owns:` intersects these is warned about by name, and
# the canonical source is named in the warning.
HARNESS_GUARDED_GLOBS = (".claude/**",)

# `.claude/commands/<x>.md` is generated from `.agent-team/commands/<x>.md`
# by the hub bootstrapper, which is the sanctioned way to change it.
HARNESS_GUARDED_SOURCE_HINT = {
    ".claude/commands/": (
        "edit `.agent-team/commands/<name>.md` (the canonical source) and "
        "regenerate with `python <hub>/scripts/bootstrap.py --upgrade <project>`"
    ),
}

# Interpreters and entrypoints `mnt` may invoke. `dev` holds a blanket
# `Bash(python *)`, which makes every Edit/Write deny bypassable by writing
# the file from an interpreter — decorative denials in a role whose deny list
# guards the control plane. `mnt` therefore inherits `dev`'s Bash surface
# minus anything that executes arbitrary code or redirects output, plus these
# sanctioned entrypoints.
MNT_INTERPRETERS = (
    "python",
    "python3",
    "py",
    f"{VENV_DIRNAME}/Scripts/python.exe",
    f"{VENV_DIRNAME}/Scripts/python",
    f"{VENV_DIRNAME}/bin/python",
)
MNT_SANCTIONED_ENTRYPOINTS = (
    "scripts/deploy-gate.py",
    "scripts/index-specs.py",
    "scripts/dispatch.py",
    "scripts/agent-status.py",
    "-m pytest",
    "-m ruff",
)

# `dev` Bash grants `mnt` does not inherit. Interpreters and package
# installers execute arbitrary code; `echo` and the compound-shell wildcards
# reach denied paths through redirection.
MNT_BASH_NOT_INHERITED = (
    "Bash(python *)",
    "Bash(python3 *)",
    "Bash(py *)",
    "Bash(pip *)",
    f"Bash({VENV_DIRNAME}/Scripts/python.exe *)",
    f"Bash({VENV_DIRNAME}/Scripts/python *)",
    f"Bash({VENV_DIRNAME}/Scripts/pip *)",
    f"Bash({VENV_DIRNAME}/bin/python *)",
    f"Bash({VENV_DIRNAME}/bin/pip *)",
    "Bash(echo *)",
    "Bash(* && *)",
    "Bash(* || *)",
    "Bash(* | *)",
    "Bash(* ; *)",
)

# Deny beats allow, so these are belt-and-braces over the narrowed allow
# list: they state the intent explicitly rather than relying on absence.
MNT_BASH_DENIES = (
    "Bash(python -c*)",
    "Bash(python3 -c*)",
    "Bash(py -c*)",
    "Bash(python)",
    "Bash(python3)",
    "Bash(pip *)",
    "Bash(uv *)",
    "Bash(node *)",
    "Bash(npx *)",
    "Bash(bash *)",
    "Bash(sh *)",
    "Bash(powershell *)",
    "Bash(pwsh *)",
    "Bash(cmd *)",
    "Bash(perl *)",
    "Bash(ruby *)",
    "Bash(tee *)",
    "Bash(sed -i*)",
    "Bash(cp *)",
    "Bash(mv *)",
)


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

    def write_worktree_settings(self, wt_path: Path, role: str, fr_id: str) -> None: ...
    def spawn(
        self,
        fr_id: str,
        role: str,
        wt_path: Path,
        log_path: Path,
        extra_prompt: str | None = None,
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
                # Docker, deploy, and infrastructure config files
                "Edit(deploy/**)",
                "Write(deploy/**)",
                "Edit(.dockerignore)",
                "Write(.dockerignore)",
                "Edit(Dockerfile*)",
                "Write(Dockerfile*)",
                "Edit(docker-compose*)",
                "Write(docker-compose*)",
                "Edit(*.yaml)",
                "Write(*.yaml)",
                "Edit(*.yml)",
                "Write(*.yml)",
                "Edit(*.toml)",
                "Write(*.toml)",
                "Edit(*.cfg)",
                "Write(*.cfg)",
                "Edit(*.txt)",
                "Write(*.txt)",
                "Edit(*.sh)",
                "Write(*.sh)",
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

    def mnt_permissions(self, fr_id: str) -> dict[str, list[str]] | None:
        """`dev`'s surface, re-scoped to the target FR's `owns:` footprint.

        Returns None when the FR declares no `owns:` — `mnt` has nothing to
        derive a footprint from and must not run.

        Two ways this is *narrower* than `dev`, both required to make the
        derived footprint enforced rather than advisory: arbitrary interpreter
        invocation is replaced by a sanctioned entrypoint list, and `specs/**`
        is denied outright — an FR body is the Architect's, and
        `reconcile-merged` owns the status flip, so a `mnt` run has no reason
        to touch one. That also keeps every path a `mnt` branch touches inside
        `owns:`.
        """
        owns = fr_owns_globs(fr_id)
        if not owns:
            return None
        granted, protected_denies = mnt_permission_rules(owns)
        base = self.ROLE_PERMISSIONS["dev"]
        keep = [d for d in base["deny"] if not d.startswith(("Edit(", "Write("))]
        inherited = [
            a
            for a in base["allow"]
            if a not in MNT_BASH_NOT_INHERITED and a != "Edit(specs/FR-*.md)"
        ]
        return {
            "allow": inherited
            + _mnt_bash_allow()
            + [f"Edit({g})" for g in granted]
            + [f"Write({g})" for g in granted],
            # Rebuild the write denials from the derived protected set; keep
            # the non-path denials (destructive git) exactly as `dev` has them.
            "deny": [f"Edit({d})" for d in protected_denies]
            + [f"Write({d})" for d in protected_denies]
            + ["Edit(tests/test_*.py)", "Write(tests/test_*.py)"]
            + ["Edit(specs/**)", "Write(specs/**)"]
            + list(MNT_BASH_DENIES)
            + keep,
        }

    def write_worktree_settings(self, wt_path: Path, role: str, fr_id: str) -> None:
        if role == "mnt":
            perms = self.mnt_permissions(fr_id)
        else:
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
        extra_prompt: str | None = None,
    ) -> tuple[str, int | None]:
        claude_bin = shutil.which(self.binary_name)
        if claude_bin is None:
            return ("binary-not-found", None)
        slash = SLASH_COMMAND_NAMES.get(role, role)
        # `extra_prompt` carries a generated rework brief on kick-back rounds.
        # It is appended after the slash invocation on its own lines so
        # `$ARGUMENTS` still resolves to the bare FR id.
        prompt = f"/{slash} {fr_id}"
        if extra_prompt:
            prompt = f"{prompt}\n\n{extra_prompt}"
        cmd = [
            claude_bin,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        model = _resolve_model(role)
        if model:
            cmd += ["--model", model]
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
        model = _resolve_model(role)
        model_note = f"; model={model}" if model else ""
        return (
            f"claude -p '/{slash} {fr_id}'  "
            f"(cwd={wt_preview}; perms via .claude/settings.local.json{model_note})"
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

    # Cursor's Shell token is per-command, so the interpreter narrowing is the
    # same idea in a different syntax: drop the bare interpreter grants and
    # re-add them bound to a sanctioned entrypoint via `Shell(cmd:args*)`.
    MNT_SHELL_NOT_INHERITED = (
        "Shell(python)",
        "Shell(python3)",
        "Shell(py)",
        "Shell(pip)",
        "Shell(uv)",
        "Shell(echo)",
    )
    MNT_SHELL_DENIES = (
        "Shell(python:-c*)",
        "Shell(python3:-c*)",
        "Shell(py:-c*)",
        "Shell(pip)",
        "Shell(uv)",
        "Shell(node)",
        "Shell(npx)",
        "Shell(bash)",
        "Shell(sh)",
        "Shell(powershell)",
        "Shell(pwsh)",
        "Shell(cmd)",
        "Shell(perl)",
        "Shell(ruby)",
        "Shell(tee)",
        "Shell(sed)",
        "Shell(cp)",
        "Shell(mv)",
    )

    def mnt_permissions(self, fr_id: str) -> dict[str, list[str]] | None:
        """Cursor equivalent of ClaudeCodeAdapter.mnt_permissions.

        Cursor has no separate edit token, so only `Write(...)` is emitted.
        """
        owns = fr_owns_globs(fr_id)
        if not owns:
            return None
        granted, protected_denies = mnt_permission_rules(owns)
        base = self.ROLE_PERMISSIONS["dev"]
        keep = [d for d in base["deny"] if not d.startswith("Write(")]
        inherited = [
            a
            for a in base["allow"]
            if a not in self.MNT_SHELL_NOT_INHERITED and a != "Write(specs/FR-*.md)"
        ]
        sanctioned = [
            f"Shell({interp}:{entry}*)"
            for interp in ("python", "python3", "py")
            for entry in MNT_SANCTIONED_ENTRYPOINTS
        ]
        return {
            "allow": inherited + sanctioned + [f"Write({g})" for g in granted],
            "deny": [f"Write({d})" for d in protected_denies]
            + ["Write(tests/test_*.py)", "Write(specs/**)"]
            + list(self.MNT_SHELL_DENIES)
            + keep,
        }

    def write_worktree_settings(self, wt_path: Path, role: str, fr_id: str) -> None:
        if role == "mnt":
            perms = self.mnt_permissions(fr_id)
        else:
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
        extra_prompt: str | None = None,
    ) -> tuple[str, int | None]:
        cursor_bin = shutil.which(self.binary_name)
        if cursor_bin is None:
            return ("binary-not-found", None)
        prompt = self._render_prompt(fr_id, role)
        # A generated rework brief is appended to the rendered role prompt. The
        # node-entrypoint path below preserves multi-line argv on Windows, so
        # the brief survives the shim-truncation quirk documented in
        # `_resolve_node_entrypoint`.
        if extra_prompt:
            prompt = f"{prompt}\n\n{extra_prompt}"
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


def _fr_spec_committed_on_branch(fr_id: str, branch: str) -> bool:
    """True iff this FR's spec file is committed on `branch`.

    A role worktree is a clean checkout of `branch` (see
    `ensure_role_worktree`). If the spec was authored but never committed —
    e.g. the Architect handoff skipped the commit step — the worktree won't
    contain it and the agent fails with "FR not found". The dispatcher uses
    this to refuse the spawn with an actionable error rather than launching a
    blind agent against an invisible spec.

    Mirrors the `git ls-tree` lookup in `_fr_status_on_branch`. A `git`
    failure (e.g. the branch doesn't exist yet) is treated as "not committed"
    so the guard fails safe.
    """
    ls = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-tree", "-r", "--name-only", branch, "specs/"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if ls.returncode != 0:
        return False
    prefix = f"specs/{fr_id}-"
    return any(
        line.startswith(prefix) and line.endswith(".md")
        for line in ls.stdout.splitlines()
    )


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


_FR_ID_RE = re.compile(r"^FR-\d{4}$")

_LOCK_BLOCKED_STATES = {
    "held": "{role} lock held (pid={pid}, since {since})",
    "stale": "stale {role} lock (pid={pid}, started {since}); not auto-clearing",
    "dead": "orphaned {role} lock (pid={pid} no longer alive); run `dispatch.py prune`",
    "corrupt": "corrupt {role} lockfile; remove it or run `dispatch.py prune`",
}


def parse_fr_selector(values: list[str] | None) -> list[str] | None:
    """Normalise `--fr` into a de-duplicated, order-preserving list of FR ids.

    Accepts a repeated flag, a comma-separated list, or both. Returns None
    when the flag was not supplied, which means "no filter" and preserves the
    whole-runnable-set behaviour exactly.
    """
    if not values:
        return None
    out: list[str] = []
    for raw in values:
        for part in str(raw).replace(",", " ").split():
            fr_id = part.strip().upper()
            if fr_id and fr_id not in out:
                out.append(fr_id)
    return out or None


def explain_not_runnable(fr_id: str, frs: list[dict], role: str) -> str | None:
    """Why `--fr <fr_id>` cannot run for `role`, or None if it can.

    Ordered most-specific-first so the message names the actual blocker rather
    than a downstream symptom. `--fr` must never silently no-op or fall back
    to the full runnable set, so every branch here returns a reason a human
    can act on.
    """
    if not _FR_ID_RE.match(fr_id):
        return "not a valid FR id (expected FR-XXXX)"
    by_id = {fr["id"]: fr for fr in frs}
    fr = by_id.get(fr_id)
    if fr is None:
        return f"unknown FR id (no specs/{fr_id}-*.md with readable frontmatter)"
    state, lock = lock_state(fr_id, role)
    if state in _LOCK_BLOCKED_STATES:
        return _LOCK_BLOCKED_STATES[state].format(
            role=role,
            pid=(lock or {}).get("pid", "?"),
            since=(lock or {}).get("started_at_iso", "?"),
        )
    if role == "rev":
        dev_branch = f"claude/dev-{fr_id}"
        if not _git_branch_exists(dev_branch):
            return f"no {dev_branch} branch locally — nothing for a Reviewer to review"
        effective = _fr_status_on_branch(fr_id, dev_branch) or fr["status"]
        if effective not in REVIEWABLE_STATUSES:
            return (
                f"status is {effective!r} on {dev_branch}; rev tick requires one of "
                f"{sorted(REVIEWABLE_STATUSES)}"
            )
        dev_state, dev_lock = lock_state(fr_id, "dev")
        if dev_state == "held":
            return (
                "Developer still running (dev lock held, "
                f"pid={(dev_lock or {}).get('pid', '?')})"
            )
        return None
    if fr["status"] != "ready":
        return f"status is {fr['status']!r}; {role} tick requires status='ready'"
    blocking = [
        f"{dep} (status={by_id.get(dep, {}).get('status') or 'missing'})"
        for dep in fr["depends_on"]
        if by_id.get(dep, {}).get("status") not in TERMINAL_STATUSES
    ]
    if blocking:
        return "unmet depends_on: " + ", ".join(blocking)
    return None


def filter_frs_by_selector(
    frs: list[dict], selection: list[str], role: str
) -> tuple[list[dict], list[str]]:
    """Resolve a `--fr` selection to FR records, plus per-FR blocking reasons.

    Reports every non-runnable FR in one pass: a partially-executed targeted
    wave is harder to reason about than a clean failure, so callers abort on
    any problem rather than proceeding with the runnable subset.
    """
    by_id = {fr["id"]: fr for fr in frs}
    chosen: list[dict] = []
    problems: list[str] = []
    for fr_id in selection:
        reason = explain_not_runnable(fr_id, frs, role)
        if reason is not None:
            problems.append(f"{fr_id}: {reason}")
        else:
            chosen.append(by_id[fr_id])
    return chosen, problems


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
      * mnt — always `main`. Maintainer mode is dev-shaped: it implements a
        control-plane FR from scratch, so it starts from latest merged work.

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
    # Guard: a worktree is a clean checkout of `base_branch`. If the FR's spec
    # was authored but never committed onto that branch, the worktree can't see
    # it and the agent fails with "FR not found" (the FR-0014 failure mode).
    # Refuse the spawn with an actionable error instead of launching blind.
    if not _fr_spec_committed_on_branch(fr_id, base_branch):
        return (
            None,
            f"error:{fr_id} spec is untracked on '{base_branch}' — commit it "
            f"before dispatch (git add specs/{fr_id}-*.md specs/INDEX.md "
            f"CODEOWNERS && git commit)",
        )
    # `mnt` has no static footprint — it is granted exactly what the target FR
    # declares in `owns:`. With nothing declared there is no footprint to
    # derive, so refuse rather than run unscoped.
    if role == "mnt" and not fr_owns_globs(fr_id):
        return (
            None,
            f"error:{fr_id} declares no `owns:` frontmatter — `mnt` derives its "
            f"writable footprint from it and will not run unscoped",
        )
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
    adapter.write_worktree_settings(wt_path, role, fr_id)
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
    fr_id: str,
    role: str,
    apply: bool,
    adapter: HarnessAdapter,
    extra_prompt: str | None = None,
) -> tuple[str, int | None, Path | None]:
    if not apply:
        return ("dry-run", None, None)
    wt_path, wt_status = ensure_role_worktree(fr_id, role, adapter)
    if wt_path is None:
        return (wt_status, None, None)
    log_path = DISPATCH_DIR / f"{fr_id}.{role}.log"
    DISPATCH_DIR.mkdir(parents=True, exist_ok=True)
    # Only forward `extra_prompt` when set: adapter doubles that predate the
    # parameter (test fakes, older harnesses) keep working for the common
    # non-rework path, which passes nothing.
    if extra_prompt is None:
        result, pid = adapter.spawn(fr_id, role, wt_path, log_path)
    else:
        result, pid = adapter.spawn(fr_id, role, wt_path, log_path, extra_prompt=extra_prompt)
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

_FR_BRANCH_RE = re.compile(r"^claude/(dev|rev|bkf|mnt)-(FR-\d{4})$")

# Conventional-commit header: `type(scope): subject`. The project convention is
# that scope is the FR id(s) a PR touches, so the scope is a second, independent
# way to identify the FR behind a merged PR.
_CC_SCOPE_BODY_RE = re.compile(r"^[a-z]+\(([^)]*)\)!?:", re.IGNORECASE)


def fr_ids_from_pr_title(title: str) -> list[str]:
    """Distinct FR ids in a PR title's conventional-commit scope.

    Only the scope is consulted, never the subject: a subject mentioning a
    neighbouring FR ("feat(FR-0041): unblock FR-0009") must not be read as
    touching it. Returns [] when the title is not a conventional-commit header
    or its scope names no FR, which callers treat as "cannot attribute".
    """
    m = _CC_SCOPE_BODY_RE.match(title.strip())
    if m is None:
        return []
    out: list[str] = []
    for fr_id in re.findall(r"FR-\d{4}", m.group(1), flags=re.IGNORECASE):
        upper = fr_id.upper()
        if upper not in out:
            out.append(upper)
    return out


@dataclasses.dataclass
class ReconcileSummary:
    flipped: list[str]
    cleaned: list[str]
    skipped: list[tuple[str, str]]
    gh_unavailable: bool = False


def _git_fetch_origin_main(timeout_s: int = 15) -> tuple[bool, str]:
    """Fetch origin/main. Failure is non-fatal."""
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "fetch", "origin", "main"],
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "git fetch failed").strip()
        return (False, err)
    return (True, "ok")


def _commits_behind_origin_main() -> int:
    """Return commit count of main..origin/main."""
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "rev-list",
            "--count",
            "main..origin/main",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        return 0
    try:
        return int((proc.stdout or "0").strip() or "0")
    except ValueError:
        return 0


def _run_deploy_gate(stage: str) -> tuple[bool, str]:
    """Run deploy-gate.py; return (ok, combined output)."""
    gate = REPO_ROOT / "scripts" / "deploy-gate.py"
    proc = subprocess.run(
        [sys.executable, str(gate), "--stage", stage],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    out = "\n".join(
        x for x in [(proc.stdout or "").strip(), (proc.stderr or "").strip()] if x
    )
    return proc.returncode == 0, out


def _prepend_forced_gate_banner(path: Path, operator_note: str) -> None:
    banner = f"> ⚠️ Forced past red gate by {operator_note}\n\n"
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if banner.strip() not in text:
            path.write_text(banner + text, encoding="utf-8")


def _finalize_gate_preflight(
    *,
    fr_id: str,
    role: str,
    stage: str,
    draft: bool,
    force: bool,
    pr_body_path: Path | None = None,
) -> tuple[bool, str | None]:
    """Refuse finalize when the deploy gate is red (draft PRs exempt)."""
    if force:
        note = os.environ.get("USER") or os.environ.get("USERNAME") or "operator"
        print(
            f"  {fr_id}.{role}  WARN — --force bypasses deploy gate ({stage})",
            file=sys.stderr,
        )
        if pr_body_path is not None:
            _prepend_forced_gate_banner(
                pr_body_path, f"{note} via dispatch finalize --force"
            )
        return (True, "forced")
    ok, output = _run_deploy_gate(stage)
    if ok:
        return (True, None)
    print(output)
    if draft and role in ("dev", "bkf"):
        print(
            f"  {fr_id}.{role}  WARN — deploy gate failed but proceeding "
            f"(draft PR exempt)"
        )
        record = _load_pr_record(fr_id, role) or {}
        record["gate_warning_at_finalize"] = output[-2000:]
        _save_pr_record(fr_id, role, record)
        return (True, "draft-exempt")
    print(f"  {fr_id}.{role}  ERROR — deploy gate ({stage}) failed; refusing finalize")
    return (False, "gate-failed")


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
        slot = by_fr.setdefault(
            fr_id, {"dev": None, "rev": None, "bkf": None, "mnt": None}
        )
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
        # `mnt` PRs carry the FR's implementation the same way `dev` PRs do,
        # so a merged mnt PR is a valid trigger for the status flip.
        pr_for_flip = slot["dev"] or slot["mnt"] or slot["rev"]
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
        # Backfill (`bkf`) and maintainer (`mnt`) PRs DO open their own PRs,
        # so they're handled independently when their own merge appears. `mnt`
        # is dev-shaped, so a merged mnt PR also retires any rev artefacts.
        roles_to_clean: list[str] = []
        if slot["dev"] is not None:
            roles_to_clean.extend(["dev", "rev"])
        elif slot["mnt"] is not None:
            roles_to_clean.extend(["mnt", "rev"])
        elif slot["rev"] is not None:
            roles_to_clean.append("rev")
        if slot["bkf"] is not None:
            roles_to_clean.append("bkf")
        if slot["mnt"] is not None and "mnt" not in roles_to_clean:
            roles_to_clean.append("mnt")

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


ROLE_LABELS = {
    "dev": "Developer",
    "rev": "Reviewer",
    "bkf": "Reviewer (backfill)",
    "mnt": "Maintainer",
}


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


def _oneshot_pr_title(fr_id: str, role: str) -> str:
    """Conventional-Commits PR title for a one-shot role's own PR.

    `mnt` uses the `chore` type because maintainer runs target the control
    plane (role definitions, dispatcher, gate) rather than product behaviour;
    what matters for reviewability is that the `(FR-XXXX)` scope is present so
    a normal `rev` phase can pick the PR up.
    """
    if role == "mnt":
        return f"chore({fr_id}): {_fr_title(fr_id) or fr_id}"
    return f"test({fr_id}): backfill AC coverage"


def mnt_footprint_violations(
    fr_id: str,
    branch: str,
    base: str = "main",
    extra: list[str] | None = None,
) -> tuple[list[str], str | None]:
    """Paths a `mnt` branch touches that fall outside the target FR's `owns:`.

    Write-time enforcement and push-time verification fail differently: any
    interpreter or shell construct the allow-list did not anticipate is a
    silent hole in the former, and only an artifact-level check catches a
    genuine *derivation* bug, where the grant itself was wrong. `fr_owns_globs`
    is the authority for the grant in both cases. `extra` folds in paths that
    are staged but not yet committed so the check is meaningful in dry-run.

    Returns (violations, error). A non-None error means the check could not
    run, which callers must treat as a refusal rather than a pass.
    """
    owns = fr_owns_globs(fr_id)
    if not owns:
        return ([], f"{fr_id} declares no `owns:`; cannot verify footprint")
    ok, out = _git_capture(["diff", "--name-only", f"{base}...{branch}"], REPO_ROOT)
    if not ok:
        lines = _path_lines(out)
        return ([], f"git diff {base}...{branch} failed: {lines[-1] if lines else '?'}")
    touched = set(_path_lines(out)) | set(extra or [])
    return (sorted(p for p in touched if not _owned(p, owns)), None)


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


def _role_worktree_path(fr_id: str, role: str) -> Path:
    """Where `ensure_role_worktree` put this role's checkout for this FR."""
    return REPO_ROOT / ".claude" / "worktrees" / f"{role}-{fr_id}"


def _git_capture(args: list[str], cwd: Path) -> tuple[bool, str]:
    """Run git in `cwd`; return (ok, trimmed stdout) or (False, stderr)."""
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        return (False, (proc.stderr or proc.stdout or "").strip())
    return (True, (proc.stdout or "").strip())


def _path_lines(out: str) -> list[str]:
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _finalize_commit_subject(fr_id: str, role: str) -> str:
    """Conventional-Commits subject for an agent's `finalize` commit."""
    if role == "rev":
        return f"test({fr_id}): AC coverage"
    if role == "bkf":
        return f"test({fr_id}): backfill AC coverage"
    if role == "mnt":
        return f"chore({fr_id}): {_fr_title(fr_id) or fr_id}"
    return f"feat({fr_id}): {_fr_title(fr_id) or fr_id}"


def _staged_paths(wt_path: Path) -> list[str]:
    """Paths in the role worktree's index. Empty when the worktree is gone."""
    if not wt_path.exists():
        return []
    ok, out = _git_capture(["diff", "--cached", "--name-only"], wt_path)
    return _path_lines(out) if ok else []


def _unstaged_paths(wt_path: Path) -> list[str]:
    """Tracked files modified in the worktree but not staged."""
    if not wt_path.exists():
        return []
    ok, out = _git_capture(["diff", "--name-only"], wt_path)
    return _path_lines(out) if ok else []


def _commits_ahead(branch: str, base: str) -> int | None:
    """Commits on `branch` that `base` lacks. None when git cannot tell."""
    ok, out = _git_capture(["rev-list", "--count", f"{base}..{branch}"], REPO_ROOT)
    if not ok:
        return None
    try:
        return int(out.split()[0])
    except (IndexError, ValueError):
        return None


def _commit_staged_work(fr_id: str, role: str, branch: str) -> tuple[str, list[str]]:
    """Commit whatever the agent staged in its worktree, before pushing.

    Dispatched agents cannot commit — the harness refuses `git commit` in every
    form — so the normal outcome of a *successful* run is a worktree of staged,
    gate-passing work on a branch with no commits, which is indistinguishable
    from a failed run once `finalize` pushes it. Staging is the agent's signal
    of intent, so the index is committed verbatim: plain `git commit` (no `-a`)
    cannot pick up unstaged modifications.

    Returns (outcome, staged_paths); outcome is "committed", "nothing-staged",
    or "error:<msg>".
    """
    wt_path = _role_worktree_path(fr_id, role)
    staged = _staged_paths(wt_path)
    if not staged:
        return ("nothing-staged", [])
    # Never commit onto a branch other than the one being finalized: a
    # worktree left on a detached HEAD or a stale branch would otherwise
    # silently land the agent's work somewhere nobody looks.
    ok, head = _git_capture(["rev-parse", "--abbrev-ref", "HEAD"], wt_path)
    if not ok:
        return (f"error:cannot read HEAD in {wt_path}", staged)
    if head != branch:
        return (f"error:worktree {wt_path} is on {head}, not {branch}", staged)
    ok, err = _git_capture(
        ["commit", "-m", _finalize_commit_subject(fr_id, role)], wt_path
    )
    if not ok:
        lines = _path_lines(err)
        return (f"error:{lines[-1] if lines else 'git commit failed'}", staged)
    return ("committed", staged)


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

    # Artifact-level backstop on the derived footprint. Runs before the push
    # so a derivation or agent defect cannot reach a PR.
    if role == "mnt":
        violations, footprint_err = mnt_footprint_violations(fr_id, branch)
        if footprint_err is not None:
            print(f"  {fr_id}.mnt  ERROR — footprint check failed: {footprint_err}")
            return 1
        if violations:
            print(
                f"  {fr_id}.mnt  ERROR — branch touches {len(violations)} path(s) "
                f"outside {fr_id} `owns:`: " + ", ".join(violations)
            )
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
        elif role in ("bkf", "mnt"):
            print(
                f"  {fr_id}.{role}  WOULD-FINALIZE  push {branch} -> origin; "
                f"gh pr create {'--draft ' if draft else ''}--base main --head {branch} "
                f"--title '{_oneshot_pr_title(fr_id, role)}' "
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
    if role in ("bkf", "mnt"):
        return _finalize_oneshot(
            fr_id,
            branch,
            head_sha,
            nwo,
            draft,
            role=role,
            pr_title=_oneshot_pr_title(fr_id, role),
        )
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


def _finalize_oneshot(
    fr_id: str,
    branch: str,
    head_sha: str | None,
    nwo: str | None,
    draft: bool,
    role: str,
    pr_title: str,
) -> int:
    """Open or refresh a PR for a one-shot role's own branch (`bkf` / `mnt`).

    Mirrors `_finalize_dev` but uses the role's worktree path and the role's
    own PR title. PR_BODY.md is the agent's own deliverable in these modes.
    """
    wt_path = REPO_ROOT / ".claude" / "worktrees" / f"{role}-{fr_id}"
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
                f"  {fr_id}.{role}  ERROR — PR_BODY.md not found at {pr_body_path} "
                "and not present on the branch; cannot create PR"
            )
            return 1
        pr_body_path = DISPATCH_DIR / f"{fr_id}.{role}.pr_body.md"
        pr_body_path.write_text(show.stdout, encoding="utf-8")

    existing_pr = _gh_pr_for_branch(branch)
    record = _load_pr_record(fr_id, role) or {}
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
        _save_pr_record(fr_id, role, record)
        print(f"  {fr_id}.{role}  PR-EXISTS    #{existing_pr}  {nwo_url}")
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
        print(f"  {fr_id}.{role}  ERROR — gh pr create failed: {last}")
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
    _save_pr_record(fr_id, role, record)
    tag = "DRAFT-OPENED" if draft else "PR-OPENED"
    print(f"  {fr_id}.{role}  {tag} #{pr_number}  {pr_url}")
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


def cmd_oneshot(fr_id: str, role: str, apply: bool, adapter: HarnessAdapter) -> int:
    """One-shot per-FR spawn for a role that isn't tick-scheduled.

    `bkf` (Reviewer in backfill mode) runs when `reconcile-merged` flipped an
    FR to `merged` and the deploy gate now reports missing `@covers` because
    the original PR landed without test coverage. It writes only the missing
    tests and opens `test(FR-XXXX): backfill AC coverage`.

    `mnt` (Developer in maintainer mode) runs for an FR whose `owns:` footprint
    intersects the control-plane paths every other role denies. Its writable
    footprint is derived from that FR's `owns:` and nothing else, so it refuses
    to start against an FR that declares none.

    Both branch off `main` into `claude/<role>-FR-XXXX`, author `PR_BODY.md`,
    and open their own PR via `finalize --role <role>`. Neither is scheduled by
    `tick` — they are dispatched per-FR by hand.
    """
    label = ROLE_LABELS.get(role, role)
    state, lock = lock_state(fr_id, role)
    if state == "held":
        print(
            f"  {fr_id}.{role}  SKIP — {label} lock held "
            f"(pid={lock['pid']}, since {lock['started_at_iso']})"
        )
        return 1
    if state == "stale":
        print(
            f"  {fr_id}.{role}  WARN — stale {label} lock "
            f"(pid={lock['pid']}, started {lock['started_at_iso']}); not auto-clearing"
        )
        return 1
    if state == "dead":
        print(
            f"  {fr_id}.{role}  SKIP — orphaned lock "
            f"(pid={lock['pid']} no longer alive); run `dispatch.py prune`"
        )
        return 1
    if state == "corrupt":
        print(
            f"  {fr_id}.{role}  WARN — corrupt lockfile at {lock_path(fr_id, role)}; skipping"
        )
        return 1

    spec_paths = list(SPECS_DIR.glob(f"{fr_id}-*.md"))
    if not spec_paths:
        print(f"  {fr_id}.{role}  ERROR — no spec at specs/{fr_id}-*.md")
        return 1

    if role == "mnt":
        owns = fr_owns_globs(fr_id)
        if not owns:
            print(
                f"  {fr_id}.mnt  ERROR — {fr_id} declares no `owns:` frontmatter; "
                "mnt derives its writable footprint from it and will not run unscoped"
            )
            return 1
        granted, _ = mnt_permission_rules(owns)
        print(f"  {fr_id}.mnt  FOOTPRINT    {', '.join(granted)}")

    result, pid, wt_path = spawn_role(fr_id, role, apply=apply, adapter=adapter)
    if result == "dry-run":
        wt_preview = REPO_ROOT / ".claude" / "worktrees" / f"{role}-{fr_id}"
        preview = adapter.dry_run_preview(fr_id, role, wt_preview)
        print(f"  {fr_id}.{role}  WOULD-SPAWN  {preview}  (base=main)")
        return 0
    if result == "spawned":
        print(
            f"  {fr_id}.{role}  SPAWNED      pid={pid}, worktree={wt_path}, "
            f"log={DISPATCH_DIR / f'{fr_id}.{role}.log'}"
        )
        return 0
    if result == "binary-not-found":
        hint = (
            "install via `winget install Anthropic.ClaudeCode`"
            if adapter.name == "claude-code"
            else "install Cursor + run `cursor-agent login`, or set DISPATCH_HARNESS=claude-code"
        )
        print(
            f"  {fr_id}.{role}  ERROR        `{adapter.binary_name}` CLI not on PATH; {hint}"
        )
        return 1
    print(f"  {fr_id}.{role}  ERROR        {result}")
    return 1


def cmd_tick(
    apply: bool,
    adapter: HarnessAdapter,
    role: str = "dev",
    fr_filter: list[str] | None = None,
) -> int:
    """`fr_filter` is None when `--fr` was not supplied, which dispatches the
    whole runnable set exactly as before. When supplied, every named FR must be
    runnable for this role or the tick aborts non-zero without spawning.
    """
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

    if fr_filter is not None:
        runnable, problems = filter_frs_by_selector(frs, fr_filter, role)
        if problems:
            print(f"--fr selection cannot run for {role_label}:")
            for problem in problems:
                print(f"  {problem}")
            return 1
        empty_msg = f"--fr selection is empty for {role_label}."
        print(f"--fr filter active: {[fr['id'] for fr in runnable]}")

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
            log_file = DISPATCH_DIR / "{}.{}.log".format(fr["id"], role)
            print(
                f"  {fr['id']}  SPAWNED      pid={pid}, worktree={wt_path}, log={log_file}"
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
            log_file = DISPATCH_DIR / "{}.{}.log".format(fr["id"], role)
            print(
                f"  {fr['id']}  ERROR        spawn failed (OSError); see {log_file}"
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


REVIEW_CYCLE_BUDGET = 3



def fr_owns_globs(fr_id: str) -> list[str]:
    """The target FR's `owns:` frontmatter, as a list of path globs.

    Empty when the spec is missing, unparseable, or declares no `owns:`.
    This is the sole authority for a role run's writable footprint, so an
    empty result must abort the spawn rather than fall back to a default.
    """
    for path in sorted(SPECS_DIR.glob(f"{fr_id}-*.md")):
        m = FRONTMATTER_PATTERN.match(path.read_text(encoding="utf-8"))
        if m is None:
            return []
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            return []
        return [str(g) for g in (meta.get("owns") or []) if str(g).strip()]
    return []


def _owned(path: str, owns: list[str]) -> bool:
    """True iff a concrete repo-relative path is covered by an `owns:` glob."""
    return any(path == o or fnmatch.fnmatch(path, o) for o in owns)


def _protected_touched(protected: str, owns: list[str]) -> bool:
    """True iff the FR owns something at or under a protected glob."""
    if "**" in protected:
        root = protected.split("**", 1)[0]
        return any(o.startswith(root) for o in owns)
    return protected in owns


def mnt_permission_rules(owns: list[str]) -> tuple[list[str], list[str]]:
    """Derive a `mnt` run's (granted_globs, protected_denies) from `owns:`.

    A broad protected glob is *narrowed*, never dropped: Claude Code resolves
    deny over allow, so leaving `.agent-team/**` denied would silently defeat
    an `Edit(.agent-team/roles/reviewer.md)` grant. Instead the broad entry is
    replaced by a concrete deny for each currently-tracked sibling the FR does
    not own, which keeps the rest of the tree protected without granting a
    subtree-wide escape.
    """
    granted = [g for g in owns if g not in MNT_NEVER_WRITABLE]
    denies: list[str] = []
    for protected in MNT_PROTECTED_GLOBS:
        if protected in MNT_NEVER_WRITABLE or not _protected_touched(protected, owns):
            denies.append(protected)
            continue
        # The FR owns something under this glob. Deny each unowned sibling.
        root = protected.split("**", 1)[0].rstrip("/")
        siblings = sorted(
            p.relative_to(REPO_ROOT).as_posix()
            for p in (REPO_ROOT / root).rglob("*")
            if p.is_file()
        )
        denies.extend(s for s in siblings if not _owned(s, owns))
    return granted, denies


def mnt_unwritable_grants(owns: list[str]) -> list[str]:
    """`owns:` globs that are grantable in a settings file but harness-guarded.

    The derivation cannot make these writable, so a `mnt` run needs to know
    which of its declared paths it will not be able to touch — otherwise the
    only way to finish its declared work looks like bypassing the deny list.
    """
    out: list[str] = []
    for glob in owns:
        for guarded in HARNESS_GUARDED_GLOBS:
            root = guarded.split("**", 1)[0]
            if glob.startswith(root) or fnmatch.fnmatch(glob, guarded):
                out.append(glob)
                break
    return out


def mnt_unwritable_hint(glob: str) -> str | None:
    """The sanctioned route for updating a harness-guarded path, if known."""
    for prefix, hint in HARNESS_GUARDED_SOURCE_HINT.items():
        if glob.startswith(prefix):
            return hint
    return None


def _mnt_bash_allow() -> list[str]:
    """Sanctioned Bash grants for `mnt` (see MNT_SANCTIONED_ENTRYPOINTS)."""
    out = [
        f"Bash({interp} {entry}*)"
        for interp in MNT_INTERPRETERS
        for entry in MNT_SANCTIONED_ENTRYPOINTS
    ]
    out.extend(
        [
            "Bash(pytest *)",
            "Bash(pytest)",
            "Bash(ruff *)",
            f"Bash({VENV_DIRNAME}/Scripts/pytest *)",
            f"Bash({VENV_DIRNAME}/Scripts/ruff *)",
            f"Bash({VENV_DIRNAME}/bin/pytest *)",
            f"Bash({VENV_DIRNAME}/bin/ruff *)",
        ]
    )
    return out


_ESCALATION_MARKER = re.compile(r"^##\s+ESCALATION\b", re.MULTILINE)


@dataclasses.dataclass
class AgentOutcome:
    """Structured evaluation of a completed agent run."""

    status: str  # "success" | "error" | "escalation" | "timeout" | "no-log"
    summary: str
    verdict: str | None = None  # rev-only: "approve" | "request-changes" | "comment"


def _wait_for_phase(
    targets: list[tuple[str, str]],
    poll_interval: float,
    phase_timeout: float,
) -> dict[str, str]:
    """Poll until all (fr_id, role) locks are no longer held or timeout fires.

    Returns {fr_id: "completed" | "timeout"} for each target.
    """
    results: dict[str, str] = {}
    pending = list(targets)
    deadline = time.monotonic() + phase_timeout

    while pending and time.monotonic() < deadline:
        still_pending: list[tuple[str, str]] = []
        for fr_id, role in pending:
            state, _ = lock_state(fr_id, role)
            if state == "held":
                still_pending.append((fr_id, role))
            else:
                results[fr_id] = "completed"
        pending = still_pending
        if pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(poll_interval, remaining))

    for fr_id, role in pending:
        state, data = lock_state(fr_id, role)
        if state == "held":
            pid = int((data or {}).get("pid", 0))
            if pid:
                kill_pid(pid)
                lock_path(fr_id, role).unlink(missing_ok=True)
                print(f"  {fr_id}.{role}  TIMEOUT — killed pid={pid}")
        results[fr_id] = "timeout"

    return results


def _evaluate_agent_outcome(
    fr_id: str,
    role: str,
    adapter: HarnessAdapter,
) -> AgentOutcome:
    """Parse the agent's NDJSON log and return a structured outcome."""
    log_path = DISPATCH_DIR / f"{fr_id}.{role}.log"
    if not log_path.exists():
        return AgentOutcome(status="no-log", summary="no log file found")

    summary = adapter.parse_log(log_path)

    if summary.is_error:
        detail = summary.result_subtype or "unknown error"
        return AgentOutcome(status="error", summary=f"agent error: {detail}")

    if summary.last_text and _ESCALATION_MARKER.search(summary.last_text):
        return AgentOutcome(status="escalation", summary="agent emitted ESCALATION block")

    if role == "rev":
        verdict, _ = _extract_review_verdict(summary.last_text)
        return AgentOutcome(status="success", summary="reviewer completed", verdict=verdict)

    return AgentOutcome(status="success", summary="agent completed successfully")



@dataclasses.dataclass
class WaveFRResult:
    """Per-FR outcome tracked through the wave pipeline."""

    fr_id: str
    dev_outcome: AgentOutcome | None = None
    dev_finalize_ok: bool = False
    pr_number: int | None = None
    pr_url: str | None = None
    rev_outcome: AgentOutcome | None = None
    rev_finalize_ok: bool = False
    rev_verdict: str | None = None
    dropped_at: str | None = None
    drop_reason: str | None = None
    # Multi-round bookkeeping. `rounds_ran` counts completed Dev->Reviewer
    # cycles (round 1 is the base pipeline); `round_verdicts` holds one verdict
    # per completed round in order; `force_escalated` is set when the budget was
    # exhausted without an approval.
    rounds_ran: int = 1
    round_verdicts: list[str] = dataclasses.field(default_factory=list)
    force_escalated: bool = False

    @property
    def completed_e2e(self) -> bool:
        return self.dev_finalize_ok and self.rev_finalize_ok


def _write_wave_synthesis(
    results: list[WaveFRResult],
    started_at: str,
    elapsed_s: float,
) -> Path:
    """Write a synthesis report to stdout and _dispatch/wave-<timestamp>.md."""
    # A rework FR finalized both legs in round 1, so completed_e2e
    # stays True even if a later round dropped mechanically — exclude dropped
    # from "completed" so the drop is not masked.
    completed = [r for r in results if r.completed_e2e and not r.dropped_at]
    dropped = [r for r in results if r.dropped_at]
    dev_only = [r for r in results if r.dev_finalize_ok and not r.rev_finalize_ok and not r.dropped_at]
    escalations = [r for r in results if r.dev_outcome and r.dev_outcome.status == "escalation"
                   or r.rev_outcome and r.rev_outcome.status == "escalation"]
    force_escalated = [r for r in results if r.force_escalated]

    lines: list[str] = []
    lines.append(f"# Wave Synthesis — {started_at}")
    lines.append("")
    lines.append("## Summary")
    lines.append(
        f"Processed {len(results)} FR(s). "
        f"{len(completed)} completed end-to-end. "
        f"{len(dev_only)} dev-only (reviewer pending/dropped). "
        f"{len(dropped)} dropped. "
        f"Elapsed: {_fmt_secs(elapsed_s)}."
    )
    lines.append("")
    lines.append("## Per-FR Results")
    for r in results:
        # AC-9: a multi-round run is auditable — record how many rounds ran and
        # each round's verdict. Kept off single-round lines to preserve today's
        # output exactly when --rounds is 1.
        rounds_note = ""
        if r.rounds_ran > 1 or len(r.round_verdicts) > 1:
            rounds_note = (
                f", rounds: {r.rounds_ran}, verdicts: [{', '.join(r.round_verdicts)}]"
            )
        if r.dropped_at:
            lines.append(f"- {r.fr_id}: DROPPED at {r.dropped_at} ({r.drop_reason}){rounds_note}")
        elif r.completed_e2e:
            verdict_note = f", reviewer: {r.rev_verdict}" if r.rev_verdict else ""
            escalate_note = " [FORCE-ESCALATE: budget exhausted]" if r.force_escalated else ""
            pr_ref = f"PR #{r.pr_number}" if r.pr_number else "PR opened"
            lines.append(f"- {r.fr_id}: {pr_ref} (draft){verdict_note}{rounds_note}{escalate_note}")
        elif r.dev_finalize_ok:
            pr_ref = f"PR #{r.pr_number}" if r.pr_number else "PR opened"
            lines.append(f"- {r.fr_id}: {pr_ref} (draft), reviewer phase incomplete")
        else:
            lines.append(f"- {r.fr_id}: incomplete (dev not finalized)")
    lines.append("")

    # AC-2 / AC-9: budget-exhaustion force-escalations are their own section so
    # the human sees them without reading per-FR lines.
    if force_escalated:
        lines.append("## Force Escalations (budget exhausted)")
        for r in force_escalated:
            lines.append(
                f"- {r.fr_id}: {r.rounds_ran} Dev<->Reviewer cycle(s) ran, "
                f"last verdict request-changes — escalate to a human "
                f"(verdicts: [{', '.join(r.round_verdicts)}])"
            )
        lines.append("")

    if escalations:
        lines.append("## Open Escalations")
        for r in escalations:
            phase = "dev" if r.dev_outcome and r.dev_outcome.status == "escalation" else "rev"
            lines.append(f"- {r.fr_id}: escalation during {phase} (see _dispatch/{r.fr_id}.{phase}.log)")
    else:
        lines.append("## Open Escalations")
        lines.append("(none)")
    lines.append("")

    next_steps: list[str] = []
    mergeable = [r for r in completed if r.rev_verdict == "approve"]
    reviewable = [r for r in completed if r.rev_verdict != "approve"]
    if mergeable:
        prs = ", ".join(f"PR #{r.pr_number}" for r in mergeable if r.pr_number)
        next_steps.append(f"- Review and merge: {prs}")
    if reviewable:
        prs = ", ".join(f"PR #{r.pr_number}" for r in reviewable if r.pr_number)
        next_steps.append(f"- Address reviewer feedback: {prs}")
    if dev_only:
        ids = ", ".join(r.fr_id for r in dev_only)
        next_steps.append(f"- Reviewer phase pending: {ids}")
    for r in dropped:
        phase = r.dropped_at or "unknown"
        next_steps.append(f"- Investigate: {r.fr_id} dropped at {phase} (see _dispatch/{r.fr_id}.*.log)")

    if next_steps:
        lines.append("## Next Steps")
        lines.extend(next_steps)
    lines.append("")

    report = "\n".join(lines)
    print()
    print("=" * 60)
    for line in lines:
        print(line)
    print("=" * 60)

    DISPATCH_DIR.mkdir(parents=True, exist_ok=True)
    ts = started_at.replace(":", "-")
    report_path = DISPATCH_DIR / f"wave-{ts}.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\nSynthesis written to {report_path}")
    return report_path


# ---------------------------------------------------------------------------
# Wave rework loop: re-dispatch the Developer on a request-changes
# verdict, up to the CLAUDE.md iteration budget.
# ---------------------------------------------------------------------------


def _dev_worktree(fr_id: str) -> Path:
    return REPO_ROOT / ".claude" / "worktrees" / f"dev-{fr_id}"


def _worktree_python(wt_path: Path) -> str:
    """The worktree's dispatch venv interpreter if present, else sys.executable.

    The venv carries the test dependencies `ensure_dev_venv` provisioned; the
    dispatcher's own interpreter may not.
    """
    for rel in (
        f"{VENV_DIRNAME}/Scripts/python.exe",
        f"{VENV_DIRNAME}/Scripts/python",
        f"{VENV_DIRNAME}/bin/python",
    ):
        cand = wt_path / rel
        if cand.exists():
            return str(cand)
    return sys.executable


_PYTEST_FAIL_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)


def _parse_failing_tests(pytest_output: str | None) -> list[str]:
    """Failing/erroring test node ids from a `pytest -rfE` summary, in order.

    Reads the machine-stable `FAILED <nodeid>` / `ERROR <nodeid>` summary lines,
    not human prose, so a brief names real tests rather than a paraphrase.
    De-duplicated, order-preserving.
    """
    seen: list[str] = []
    for m in _PYTEST_FAIL_RE.finditer(pytest_output or ""):
        node = m.group(1)
        if node not in seen:
            seen.append(node)
    return seen


def _collect_failing_tests(wt_path: Path, timeout_s: float = 900) -> list[str]:
    """Run the worktree's suite and return failing test node ids.

    The dev worktree, synced with the Reviewer's committed tests, is the ground
    truth for "what the Developer must make pass". A crash or
    timeout yields an empty list; the brief still carries the verdict text.
    """
    py = _worktree_python(wt_path)
    try:
        proc = subprocess.run(
            [py, "-m", "pytest", "-q", "--no-header", "--tb=no", "-rfE",
             "-p", "no:cacheprovider"],
            cwd=str(wt_path),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return _parse_failing_tests((proc.stdout or "") + "\n" + (proc.stderr or ""))


def _read_review_md_on_branch(fr_id: str) -> str | None:
    """The committed `REVIEW.md` on the Reviewer's branch, or None."""
    branch = _branch_for(fr_id, "rev")
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", f"{branch}:REVIEW.md"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def _reviewer_final_text(fr_id: str, adapter: HarnessAdapter) -> str:
    """The Reviewer's full final message, for quoting in a rework brief.

    Prefers the parsed reviewer log; falls back to the committed REVIEW.md.
    """
    log_path = DISPATCH_DIR / f"{fr_id}.rev.log"
    if log_path.exists():
        text = adapter.parse_log(log_path).last_text
        if text:
            return text
    return _read_review_md_on_branch(fr_id) or ""


def _resolve_rework_verdict(fr_id: str, log_verdict: str | None) -> str:
    """The verdict that drives the loop, from the Reviewer's own artifacts.

    Never GitHub's `reviewDecision`. Prefer the verdict the
    dispatcher already extracts from the parsed reviewer log (the same
    `_extract_review_verdict` used to choose the `gh pr review` flag); when that
    is inconclusive ("comment"), fall back to the committed REVIEW.md and run
    the identical extraction over it. A request-changes that had to be posted
    via the own-PR `--comment` fallback still resolves to request-changes here,
    because the source is the reviewer's text — not how GitHub recorded it.

    """
    if log_verdict in ("approve", "request-changes"):
        return log_verdict
    review_text = _read_review_md_on_branch(fr_id)
    if review_text:
        verdict, _ = _extract_review_verdict(review_text)
        return verdict
    return log_verdict or "comment"


def _generate_rework_brief(
    *,
    fr_id: str,
    fr_title: str | None,
    round_num: int,
    budget: int,
    verdict_text: str,
    failing_tests: list[str],
    owns: list[str],
) -> str:
    """A generated round-2+ Developer rework brief.

    Carries the FR id + title, the round number against the budget, the
    Reviewer's verdict text, the failing test names, and the FR's `owns:`
    footprint verbatim as the authoritative permitted write surface. It does
    NOT narrow the footprint and does NOT name a file as the place a fix
    belongs — scoping within `owns` is the Developer's judgement. Naming a file
    the failing assertion never reads is the error this generated brief
    exists to prevent.

    """
    title = fr_title or fr_id
    lines: list[str] = []
    lines.append(f"# Rework brief — {fr_id}: {title}")
    lines.append("")
    lines.append(
        f"This is Dev<->Reviewer round {round_num} of a budget of {budget} "
        f"(CLAUDE.md). The Reviewer requested changes on the previous round. "
        f"Address the review, commit on the current branch, and refresh "
        f"PR_BODY.md."
    )
    lines.append("")
    lines.append("## Reviewer verdict")
    lines.append("")
    lines.append((verdict_text or "(no verdict text captured)").strip())
    lines.append("")
    lines.append("## Failing tests")
    lines.append("")
    if failing_tests:
        for node in failing_tests:
            lines.append(f"- `{node}`")
    else:
        lines.append(
            "- (none captured mechanically — read the Reviewer verdict above "
            "and REVIEW.md in your worktree)"
        )
    lines.append("")
    lines.append("## Permitted write footprint")
    lines.append("")
    lines.append(
        "These are the paths this FR declares in `owns:`. This is your "
        "authoritative and complete permitted write footprint — no narrower, "
        "no wider. Choosing which of these to change to satisfy the review is "
        "your judgement; this brief does not prescribe a file."
    )
    lines.append("")
    if owns:
        for glob in owns:
            lines.append(f"- `{glob}`")
    else:
        lines.append(
            "- (the FR declares no `owns:` — do not write code; escalate, as "
            "there is no permitted footprint)"
        )
    lines.append("")
    return "\n".join(lines)


def _write_rework_brief(wt_path: Path, fr_id: str, round_num: int, text: str) -> str:
    """Write the generated brief into the worktree and return its rel path."""
    brief_dir = wt_path / "_dispatch"
    brief_dir.mkdir(parents=True, exist_ok=True)
    rel = f"_dispatch/round{round_num}-{fr_id}-dev.md"
    (wt_path / rel).write_text(text, encoding="utf-8")
    return rel


def _covers_tests_present(wt_path: Path) -> bool:
    """True iff any file under the worktree's tests/ carries an @covers tag."""
    tests_dir = wt_path / "tests"
    if not tests_dir.is_dir():
        return False
    for path in tests_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "@covers" in text:
            return True
    return False


def _prepare_dev_worktree_for_rework(fr_id: str) -> tuple[bool, str]:
    """Sync the Reviewer's committed work into the dev worktree and verify it.

    Before a rework round starts, REVIEW.md and the @covers test
    files must be present in the Developer's worktree, or the round does not
    start. The Reviewer commits both onto `claude/rev-<FR>` (branched off the
    dev branch); merging that branch into the dev worktree is what puts them in
    front of the Developer. A failed merge or a missing artifact is a
    mechanical failure that stops the round without consuming a budget round.

    """
    dev_wt = _dev_worktree(fr_id)
    if not dev_wt.exists():
        return (False, f"dev worktree missing at {dev_wt}")
    rev_branch = _branch_for(fr_id, "rev")
    if not _git_branch_exists(rev_branch):
        return (False, f"reviewer branch {rev_branch} does not exist — no review to sync")
    merge = subprocess.run(
        ["git", "-C", str(dev_wt), "merge", rev_branch, "--no-edit",
         "-m", f"merge {rev_branch} into dev worktree (rework sync)"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if merge.returncode != 0:
        # Abort a half-applied merge so the worktree is left usable.
        subprocess.run(
            ["git", "-C", str(dev_wt), "merge", "--abort"],
            capture_output=True, text=True, timeout=15,
        )
        err = (merge.stderr or merge.stdout or "merge failed").strip()[:200]
        return (False, f"could not merge {rev_branch} into dev worktree: {err}")
    if not (dev_wt / "REVIEW.md").exists():
        return (False, "REVIEW.md not present in dev worktree after sync")
    if not _covers_tests_present(dev_wt):
        return (False, "no @covers test files present in dev worktree after sync")
    return (True, f"synced {rev_branch} (REVIEW.md + @covers tests present)")


def _run_one_rework_round(
    fr_id: str,
    round_num: int,
    budget: int,
    verdict_text: str,
    draft: bool,
    adapter: HarnessAdapter,
    phase_timeout: float,
    poll_interval: float,
) -> tuple[str | None, str]:
    """Run a single Dev->Reviewer rework cycle for one FR.

    Returns `(verdict, note)`. `verdict` is the Reviewer's resolved verdict when
    the round completed end-to-end; it is None on a mechanical failure (AC-7
    precondition, spawn/monitor/finalize error, timeout), which stops the FR
    without consuming a budget round.
    """
    ok, prep_note = _prepare_dev_worktree_for_rework(fr_id)
    if not ok:
        return (None, f"precondition: {prep_note}")

    dev_wt = _dev_worktree(fr_id)
    failing = _collect_failing_tests(dev_wt)
    brief = _generate_rework_brief(
        fr_id=fr_id,
        fr_title=_fr_title(fr_id),
        round_num=round_num,
        budget=budget,
        verdict_text=verdict_text,
        failing_tests=failing,
        owns=fr_owns_globs(fr_id),
    )
    rel = _write_rework_brief(dev_wt, fr_id, round_num, brief)
    extra = (
        f"You are in Dev<->Reviewer rework round {round_num} of {budget}. "
        f"A generated rework brief is at `{rel}` in this worktree: read it "
        f"first. It carries the Reviewer's verdict, the failing tests, and your "
        f"authoritative permitted write footprint (the FR's `owns:`). The "
        f"Reviewer's tests and REVIEW.md are already in this worktree."
    )

    # Developer rework leg
    result, _pid, _wt = spawn_role(fr_id, "dev", apply=True, adapter=adapter, extra_prompt=extra)
    if result != "spawned":
        return (None, f"dev re-dispatch failed: {result}")
    poll = _wait_for_phase([(fr_id, "dev")], poll_interval, phase_timeout)
    if poll.get(fr_id) == "timeout":
        return (None, "dev rework timed out")
    dev_outcome = _evaluate_agent_outcome(fr_id, "dev", adapter)
    if dev_outcome.status != "success":
        return (None, f"dev rework did not complete: {dev_outcome.summary}")
    if cmd_finalize(fr_id, "dev", apply=True, draft=draft, adapter=adapter) != 0:
        return (None, "dev rework finalize failed")

    # Reviewer re-review leg
    result, _pid, _wt = spawn_role(fr_id, "rev", apply=True, adapter=adapter)
    if result != "spawned":
        return (None, f"reviewer re-dispatch failed: {result}")
    poll = _wait_for_phase([(fr_id, "rev")], poll_interval, phase_timeout)
    if poll.get(fr_id) == "timeout":
        return (None, "reviewer re-review timed out")
    rev_outcome = _evaluate_agent_outcome(fr_id, "rev", adapter)
    if rev_outcome.status != "success":
        return (None, f"reviewer re-review did not complete: {rev_outcome.summary}")
    if cmd_finalize(fr_id, "rev", apply=True, adapter=adapter) != 0:
        return (None, "reviewer re-review finalize failed")

    verdict = _resolve_rework_verdict(fr_id, rev_outcome.verdict)
    return (verdict, "round completed")


def _run_rework_rounds(
    results_by_id: dict[str, WaveFRResult],
    rounds: int,
    draft: bool,
    adapter: HarnessAdapter,
    phase_timeout: float,
    poll_interval: float,
) -> None:
    """Drive rounds 2..N for every FR whose round-1 verdict was request-changes.

    Re-dispatch Dev then Reviewer while the verdict stays
    request-changes and budget remains. Exhausting the budget without an
    approval flags a force-escalation and does not start a further round.
    """
    for fr_id, r in results_by_id.items():
        if r.rev_verdict != "request-changes" or r.dropped_at:
            continue
        while r.rounds_ran < rounds:
            round_num = r.rounds_ran + 1
            print(f"  {fr_id}  REWORK round {round_num}/{rounds} — "
                  f"reviewer requested changes")
            verdict_text = _reviewer_final_text(fr_id, adapter)
            new_verdict, note = _run_one_rework_round(
                fr_id, round_num, rounds, verdict_text, draft, adapter,
                phase_timeout, poll_interval,
            )
            if new_verdict is None:
                # Mechanical / precondition failure: stop the FR, do not consume
                # a budget round (open-question default).
                r.dropped_at = f"rework-round-{round_num}"
                r.drop_reason = note
                print(f"  {fr_id}  REWORK STOPPED — {note}")
                break
            r.rounds_ran = round_num
            r.round_verdicts.append(new_verdict)
            r.rev_verdict = new_verdict
            print(f"  {fr_id}  REWORK round {round_num} verdict={new_verdict}")
            if new_verdict == "approve":
                break
            if new_verdict != "request-changes":
                # Inconclusive ("comment"): the loop only re-drives on an
                # explicit request-changes. Leave it for a human.
                break
        if (
            r.rev_verdict == "request-changes"
            and r.rounds_ran >= rounds
            and not r.dropped_at
        ):
            r.force_escalated = True
            print(f"  {fr_id}  FORCE-ESCALATE — {rounds} Dev<->Reviewer cycle(s) "
                  f"exhausted without approval; escalate to a human")


def cmd_wave(
    apply: bool,
    adapter: HarnessAdapter,
    draft: bool = True,
    timeout: float = 7200,
    phase_timeout: float = 3600,
    poll_interval: float = 30,
    fr_filter: list[str] | None = None,
    rounds: int = 1,
) -> int:
    """Autonomous dispatch-to-PR pipeline.

    Chains: pre-flight -> dev tick -> dev monitor -> dev evaluate/finalize
    -> rev tick -> rev monitor -> rev evaluate/finalize -> synthesis.

    In dry-run mode (apply=False), previews what each phase would do
    without spawning agents, pushing branches, or touching GitHub.


    `fr_filter` restricts Phase 1's dev selection; every later phase derives
    its own worklist from that set (`results_by_id`, `dev_finalized`), so the
    filter propagates through monitor, finalize, rev tick and synthesis without
    per-phase re-filtering. None preserves the unfiltered behaviour exactly.

    `rounds` is the Dev<->Reviewer iteration budget for this wave.
    The default of 1 preserves the single-pass behaviour exactly. Values above
    2..REVIEW_CYCLE_BUDGET re-dispatch the Developer on a request-changes
    verdict; a request beyond the budget is rejected rather than clamped.

    """
    if rounds < 1 or rounds > REVIEW_CYCLE_BUDGET:
        print(
            f"Wave aborted: --rounds {rounds} is out of range. The Dev<->Reviewer "
            f"budget is {REVIEW_CYCLE_BUDGET} cycles (CLAUDE.md); pass a value "
            f"between 1 and {REVIEW_CYCLE_BUDGET}."
        )
        return 2

    wave_start = time.monotonic()
    started_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    print(f"Wave started at {started_at} ({'APPLY' if apply else 'dry-run'}, "
          f"harness={adapter.name}, draft={draft}, rounds={rounds})")
    print(f"Timeouts: total={_fmt_secs(timeout)}, phase={_fmt_secs(phase_timeout)}, "
          f"poll={poll_interval}s")
    print()

    # ------------------------------------------------------------------
    # Phase 0: Pre-flight
    # ------------------------------------------------------------------
    print("--- Phase 0: Pre-flight ---")

    ok, err = _gh_preflight(verbose=False)
    if not ok:
        print(f"Wave aborted: GitHub integration required ({err})")
        print(GH_AUTH_PREFLIGHT_HINT)
        return 1

    pruned = prune_dead_locks(verbose=False)
    if pruned:
        print(f"Auto-pruned {pruned} dead lock(s).")

    if apply:
        recon = reconcile_merged_prs(apply=True, verbose=False)
        if recon.flipped or recon.cleaned:
            bits = []
            if recon.flipped:
                bits.append(f"flipped status: {sorted(set(recon.flipped))}")
            if recon.cleaned:
                bits.append(f"cleaned: {sorted(set(recon.cleaned))}")
            print(f"Reconciled merged PRs — {'; '.join(bits)}")
    else:
        recon = reconcile_merged_prs(apply=False, verbose=False)
        if recon.flipped:
            print(f"WOULD reconcile: {sorted(set(recon.flipped))}")

    frs = load_frs()
    if not frs:
        print("No FRs found in specs/")
        return 0

    runnable = runnable_frs(frs)
    if fr_filter is not None:
        runnable, problems = filter_frs_by_selector(frs, fr_filter, "dev")
        if problems:
            print("Wave aborted: --fr selection cannot run for Developer:")
            for problem in problems:
                print(f"  {problem}")
            return 1
        print(f"--fr filter active: {[fr['id'] for fr in runnable]}")
    if not runnable:
        print("No runnable FRs (none with status=ready and deps fully merged).")
        print()
        _write_wave_synthesis([], started_at, time.monotonic() - wave_start)
        return 0

    print(f"Runnable FRs for Developer: {[fr['id'] for fr in runnable]}")
    print()

    results: list[WaveFRResult] = [WaveFRResult(fr_id=fr["id"]) for fr in runnable]
    results_by_id: dict[str, WaveFRResult] = {r.fr_id: r for r in results}

    # ------------------------------------------------------------------
    # Phase 1: Dev Tick
    # ------------------------------------------------------------------
    print("--- Phase 1: Dev Tick ---")

    dev_spawned: list[tuple[str, str]] = []
    for fr in runnable:
        fr_id = fr["id"]
        state, lock = lock_state(fr_id, "dev")
        if state == "held":
            print(f"  {fr_id}  SKIP — Developer lock held (pid={lock['pid']})")
            r = results_by_id[fr_id]
            r.dropped_at = "dev-tick"
            r.drop_reason = "lock already held"
            continue
        if state in ("stale", "dead", "corrupt"):
            print(f"  {fr_id}  SKIP — lock state={state}; run prune first")
            r = results_by_id[fr_id]
            r.dropped_at = "dev-tick"
            r.drop_reason = f"lock state: {state}"
            continue

        if not apply:
            wt_preview = REPO_ROOT / ".claude" / "worktrees" / f"dev-{fr_id}"
            base = _base_branch_for_role(fr_id, "dev")
            preview = adapter.dry_run_preview(fr_id, "dev", wt_preview)
            print(f"  {fr_id}  WOULD-SPAWN  {preview}  (base={base})")
            continue

        result, pid, wt_path = spawn_role(fr_id, "dev", apply=True, adapter=adapter)
        if result == "spawned":
            log_file = DISPATCH_DIR / f"{fr_id}.dev.log"
            print(f"  {fr_id}  SPAWNED      pid={pid}, worktree={wt_path}, log={log_file}")
            dev_spawned.append((fr_id, "dev"))
        elif result == "binary-not-found":
            hint = (
                "install via `winget install Anthropic.ClaudeCode`"
                if adapter.name == "claude-code"
                else "install Cursor + run `cursor-agent login`"
            )
            print(f"  {fr_id}  ERROR — `{adapter.binary_name}` not on PATH; {hint}")
            results_by_id[fr_id].dropped_at = "dev-tick"
            results_by_id[fr_id].drop_reason = "binary not found"
        else:
            print(f"  {fr_id}  ERROR — {result}")
            results_by_id[fr_id].dropped_at = "dev-tick"
            results_by_id[fr_id].drop_reason = result

    if not apply:
        selected = [fr["id"] for fr in runnable if not results_by_id[fr["id"]].dropped_at]
        print()
        print(f"--- Dry-run: remaining phases, for these FRs only (rounds={rounds}) ---")
        for phase in (
            "Phase 2: Dev Monitor",
            "Phase 3: Dev Evaluate + Finalize",
            "Phase 4: Rev Tick",
            "Phase 5: Rev Monitor",
            "Phase 6: Rev Evaluate + Finalize",
        ):
            print(f"  WOULD-RUN  {phase}  {selected}")
        # AC-8: name the rework rounds that a request-changes verdict would drive.
        for extra_round in range(2, rounds + 1):
            print(
                f"  WOULD-RUN  Round {extra_round}/{rounds}: Dev rework -> "
                f"Reviewer re-review  {selected}  "
                f"(only for FRs whose round {extra_round - 1} verdict is "
                f"request-changes)"
            )
        print()
        _write_wave_synthesis(results, started_at, time.monotonic() - wave_start)
        return 0

    if not dev_spawned:
        print("No Developer agents spawned; nothing to do.")
        print()
        _write_wave_synthesis(results, started_at, time.monotonic() - wave_start)
        return 0

    # ------------------------------------------------------------------
    # Phase 2: Dev Monitor
    # ------------------------------------------------------------------
    print()
    print(f"--- Phase 2: Dev Monitor ({len(dev_spawned)} agent(s), "
          f"timeout={_fmt_secs(phase_timeout)}) ---")

    dev_poll_results = _wait_for_phase(dev_spawned, poll_interval, phase_timeout)

    # Check total wave timeout
    if time.monotonic() - wave_start > timeout:
        print("Wave total timeout reached; skipping remaining phases.")
        for fr_id, status in dev_poll_results.items():
            if status == "timeout":
                results_by_id[fr_id].dropped_at = "dev-monitor"
                results_by_id[fr_id].drop_reason = "phase timeout"
        _write_wave_synthesis(results, started_at, time.monotonic() - wave_start)
        return 1

    # ------------------------------------------------------------------
    # Phase 3: Dev Evaluate + Finalize
    # ------------------------------------------------------------------
    print()
    print("--- Phase 3: Dev Evaluate + Finalize ---")

    dev_finalized: list[str] = []
    for fr_id, poll_status in dev_poll_results.items():
        r = results_by_id[fr_id]

        if poll_status == "timeout":
            r.dropped_at = "dev-monitor"
            r.drop_reason = "phase timeout (agent killed)"
            print(f"  {fr_id}  DROPPED — timeout")
            continue

        outcome = _evaluate_agent_outcome(fr_id, "dev", adapter)
        r.dev_outcome = outcome

        if outcome.status != "success":
            r.dropped_at = "dev-evaluate"
            r.drop_reason = outcome.summary
            print(f"  {fr_id}  DROPPED — {outcome.summary}")
            continue

        wt_path = REPO_ROOT / ".claude" / "worktrees" / f"dev-{fr_id}"
        pr_body = wt_path / "PR_BODY.md"
        if not pr_body.exists():
            branch = _branch_for(fr_id, "dev")
            show = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "show", f"{branch}:PR_BODY.md"],
                capture_output=True, text=True, timeout=10,
            )
            if show.returncode != 0:
                r.dropped_at = "dev-evaluate"
                r.drop_reason = "PR_BODY.md not found"
                print(f"  {fr_id}  DROPPED — PR_BODY.md missing")
                continue

        rc = cmd_finalize(fr_id, "dev", apply=True, draft=draft, adapter=adapter)
        if rc != 0:
            r.dropped_at = "dev-finalize"
            r.drop_reason = "finalize failed (see output above)"
            print(f"  {fr_id}  DROPPED — finalize failed")
            continue

        r.dev_finalize_ok = True
        pr_record = _load_pr_record(fr_id, "dev")
        if pr_record:
            r.pr_number = pr_record.get("pr_number")
            r.pr_url = pr_record.get("pr_url")
        dev_finalized.append(fr_id)
        print(f"  {fr_id}  FINALIZED    PR #{r.pr_number or '?'}")

    if not dev_finalized:
        print()
        print("No Developer agents succeeded; skipping Reviewer phase.")
        _write_wave_synthesis(results, started_at, time.monotonic() - wave_start)
        return 0

    # Check total wave timeout before rev phase
    if time.monotonic() - wave_start > timeout:
        print("Wave total timeout reached; skipping Reviewer phase.")
        _write_wave_synthesis(results, started_at, time.monotonic() - wave_start)
        return 0

    # ------------------------------------------------------------------
    # Phase 4: Rev Tick
    # ------------------------------------------------------------------
    print()
    print("--- Phase 4: Rev Tick ---")

    frs = load_frs()
    rev_runnable = runnable_review_frs(frs)
    rev_eligible = [fr for fr in rev_runnable if fr["id"] in dev_finalized]

    if not rev_eligible:
        print("No FRs eligible for Reviewer (dev branches may need status flip).")
        print()
        _write_wave_synthesis(results, started_at, time.monotonic() - wave_start)
        return 0

    print(f"Eligible FRs for Reviewer: {[fr['id'] for fr in rev_eligible]}")

    rev_spawned: list[tuple[str, str]] = []
    for fr in rev_eligible:
        fr_id = fr["id"]
        state, lock = lock_state(fr_id, "rev")
        if state == "held":
            print(f"  {fr_id}  SKIP — Reviewer lock held")
            continue
        if state in ("stale", "dead", "corrupt"):
            print(f"  {fr_id}  SKIP — rev lock state={state}")
            continue

        result, pid, wt_path = spawn_role(fr_id, "rev", apply=True, adapter=adapter)
        if result == "spawned":
            log_file = DISPATCH_DIR / f"{fr_id}.rev.log"
            print(f"  {fr_id}  SPAWNED      pid={pid}, worktree={wt_path}, log={log_file}")
            rev_spawned.append((fr_id, "rev"))
        else:
            print(f"  {fr_id}  ERROR — {result}")

    if not rev_spawned:
        print("No Reviewer agents spawned.")
        print()
        _write_wave_synthesis(results, started_at, time.monotonic() - wave_start)
        return 0

    # ------------------------------------------------------------------
    # Phase 5: Rev Monitor
    # ------------------------------------------------------------------
    print()
    remaining_timeout = timeout - (time.monotonic() - wave_start)
    effective_phase_timeout = min(phase_timeout, max(remaining_timeout, 0))
    print(f"--- Phase 5: Rev Monitor ({len(rev_spawned)} agent(s), "
          f"timeout={_fmt_secs(effective_phase_timeout)}) ---")

    rev_poll_results = _wait_for_phase(rev_spawned, poll_interval, effective_phase_timeout)

    # ------------------------------------------------------------------
    # Phase 6: Rev Evaluate + Finalize
    # ------------------------------------------------------------------
    print()
    print("--- Phase 6: Rev Evaluate + Finalize ---")

    for fr_id, poll_status in rev_poll_results.items():
        r = results_by_id.get(fr_id)
        if r is None:
            continue

        if poll_status == "timeout":
            r.dropped_at = "rev-monitor"
            r.drop_reason = "phase timeout (agent killed)"
            print(f"  {fr_id}  DROPPED — timeout")
            continue

        outcome = _evaluate_agent_outcome(fr_id, "rev", adapter)
        r.rev_outcome = outcome

        if outcome.status != "success":
            r.dropped_at = "rev-evaluate"
            r.drop_reason = outcome.summary
            print(f"  {fr_id}  DROPPED — {outcome.summary}")
            continue

        rc = cmd_finalize(fr_id, "rev", apply=True, adapter=adapter)
        if rc != 0:
            r.dropped_at = "rev-finalize"
            r.drop_reason = "finalize failed (see output above)"
            print(f"  {fr_id}  DROPPED — rev finalize failed")
            continue

        r.rev_finalize_ok = True
        r.rev_verdict = outcome.verdict
        r.round_verdicts.append(outcome.verdict)
        print(f"  {fr_id}  REVIEW-POSTED  verdict={outcome.verdict}")

    # ------------------------------------------------------------------
    # Rework rounds: re-dispatch Dev->Reviewer on request-changes
    # ------------------------------------------------------------------
    if rounds > 1:
        print()
        print(f"--- Rework rounds (budget={rounds}) ---")
        # Resolve each round-1 verdict from the Reviewer's own artifacts
        # (AC-3/AC-4) before deciding whether to loop, so a request-changes
        # that had to degrade to the own-PR comment fallback still re-drives.
        for fr_id, r in results_by_id.items():
            if not r.rev_finalize_ok or r.dropped_at:
                continue
            resolved = _resolve_rework_verdict(fr_id, r.rev_verdict)
            if resolved != r.rev_verdict:
                r.rev_verdict = resolved
                if r.round_verdicts:
                    r.round_verdicts[-1] = resolved
        _run_rework_rounds(
            results_by_id, rounds, draft, adapter, phase_timeout, poll_interval
        )

    # ------------------------------------------------------------------
    # Synthesis
    # ------------------------------------------------------------------
    _write_wave_synthesis(results, started_at, time.monotonic() - wave_start)

    any_dropped = any(r.dropped_at for r in results)
    return 1 if any_dropped and not any(r.completed_e2e for r in results) else 0


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

    def add_fr_selector(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--fr",
            action="append",
            metavar="FR-XXXX[,FR-YYYY]",
            help=(
                "Restrict dispatch to these FR ids. Repeatable and/or "
                "comma-separated. Omit for every runnable FR. A named FR that "
                "cannot run is an error, never a silent skip."
            ),
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
    add_fr_selector(p_tick)
    add_harness(p_tick)

    sub.add_parser("status", help="Show liveness/idle/dead per lock")
    sub.add_parser("prune", help="Remove locks whose process is no longer alive")

    p_kill = sub.add_parser(
        "kill", help="Force-terminate a running role + remove its lock"
    )
    p_kill.add_argument("fr_id", help="FR id (e.g., FR-0002)")
    p_kill.add_argument(
        "--role",
        choices=("dev", "rev", "bkf", "mnt"),
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
        choices=("dev", "rev", "bkf", "mnt"),
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
        choices=("dev", "rev", "bkf", "mnt"),
        default="dev",
        help=(
            "dev: push + open draft PR. rev: push + post gh pr review against "
            "parent. bkf: push + open backfill PR (`test(FR-XXXX): backfill AC "
            "coverage`). mnt: push + open maintainer PR (`chore(FR-XXXX): <title>`)."
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

    p_mnt = sub.add_parser(
        "maintain",
        help=(
            "One-shot Developer-in-maintainer-mode spawn for an FR whose "
            "`owns:` footprint intersects the control-plane paths every other "
            "role denies. Writable footprint is derived from that FR's `owns:` "
            "and nothing else. Opens `chore(FR-XXXX): <title>`."
        ),
    )
    p_mnt.add_argument("fr_id", help="FR id (e.g., FR-XXXX)")
    p_mnt.add_argument(
        "--apply",
        action="store_true",
        help="Actually spawn the agent (dry-run by default)",
    )
    add_harness(p_mnt)

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
        choices=("dev", "rev", "bkf", "mnt"),
        default="dev",
        help="dev (default), rev, bkf, or mnt",
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

    p_wave = sub.add_parser(
        "wave",
        help=(
            "Autonomous dispatch-to-PR pipeline. Chains: pre-flight -> "
            "dev tick -> dev monitor -> dev finalize -> rev tick -> rev monitor -> "
            "rev finalize -> synthesis. Single command, no human intervention in "
            "the happy path. PRs open as --draft; human reviews and merges."
        ),
    )
    p_wave.add_argument(
        "--apply",
        action="store_true",
        help="Actually spawn agents, push branches, and open PRs (dry-run by default)",
    )
    p_wave.add_argument(
        "--ready",
        action="store_true",
        help="Open PRs as ready-for-review instead of --draft",
    )
    p_wave.add_argument(
        "--timeout",
        type=float,
        default=7200,
        help="Total wave timeout in seconds (default: 7200 = 2h)",
    )
    p_wave.add_argument(
        "--phase-timeout",
        type=float,
        default=3600,
        help="Max time per phase (dev wave or rev wave) in seconds (default: 3600 = 1h)",
    )
    p_wave.add_argument(
        "--poll-interval",
        type=float,
        default=30,
        help="Seconds between lock-state polls (default: 30)",
    )
    p_wave.add_argument(
        "--rounds",
        type=int,
        default=1,
        help=(
            "Max Dev<->Reviewer cycles per FR (default: 1, today's single-pass "
            f"behaviour). Capped at the {REVIEW_CYCLE_BUDGET}-cycle budget from "
            "CLAUDE.md; a higher value exits non-zero. When the reviewer requests "
            "changes and rounds remain, the Developer is re-dispatched with a "
            "generated rework brief."
        ),
    )
    add_fr_selector(p_wave)
    add_harness(p_wave)

    args = parser.parse_args()
    if args.cmd == "tick":
        adapter = get_adapter(args.harness)
        return cmd_tick(
            apply=args.apply,
            adapter=adapter,
            role=args.role,
            fr_filter=parse_fr_selector(args.fr),
        )
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
        return cmd_oneshot(args.fr_id, "bkf", apply=args.apply, adapter=adapter)
    if args.cmd == "maintain":
        adapter = get_adapter(args.harness)
        return cmd_oneshot(args.fr_id, "mnt", apply=args.apply, adapter=adapter)
    if args.cmd == "wave":
        adapter = get_adapter(args.harness)
        return cmd_wave(
            apply=args.apply,
            adapter=adapter,
            draft=not args.ready,
            timeout=args.timeout,
            phase_timeout=args.phase_timeout,
            poll_interval=args.poll_interval,
            fr_filter=parse_fr_selector(args.fr),
            rounds=args.rounds,
        )
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
