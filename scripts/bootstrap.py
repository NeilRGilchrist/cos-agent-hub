#!/usr/bin/env python3
"""bootstrap.py — Stand up or upgrade an agent-team project from this hub.

Usage:
  python scripts/bootstrap.py <name-or-path> "<description>" [flags]
  python scripts/bootstrap.py --upgrade <project-path> [flags]

If <name-or-path> contains no path separator, the project lands under
projects/<name> (a sibling of the hub layout). Pass an explicit path to put it
elsewhere — the project still gets registered in hub/projects.yaml.

Flags (create mode):
  --stack python|node|none   Inject stack-specific conventions and CI (default: none)
  --profile <name>           Activate a compliance profile (can be repeated; validated against profiles/)
  --private                  Mark project as private (excluded from cross-project pattern detection)
  --keep-example             Keep specs/FR-0001-example.md (default: delete it)
  --no-git                   Skip git init (default: init and make initial commit)
  --no-register              Don't append to hub/projects.yaml

Flags (upgrade mode):
  --upgrade <project-path>   Propagate template changes to an existing project
  --yes                      Apply all upgrades without interactive confirmation

Common flags:
  -h, --help                 Show this help
"""

import argparse
import datetime
import os
import platform
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")

HUB_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_ROOT = HUB_ROOT / "template"
PROFILES_ROOT = HUB_ROOT / "profiles"

# Infrastructure paths that --upgrade will sync from the template.
# Note: `.claude` (not just `.claude/commands`) so settings.json — which wires
# the PHI/spec hooks — propagates on upgrade. `.claude/worktrees/` is created
# per-project at bootstrap time and does not exist in the template, so it is
# safe to include the full `.claude` subtree.
INFRA_DIRS = [
    ".agent-team",
    "scripts",
    ".github/workflows",
    ".claude",
    ".cursor/commands",
    ".cursor/rules",
]

# Files/dirs that --upgrade must never touch.
UPGRADE_EXCLUDE = {
    "specs",
    "src",
    "tests",
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    ".gitignore",
    "AGENTS.override.md.example",
}

# Command metadata for generating .claude/commands/ and .cursor/commands/.
COMMAND_META = {
    "architect": {
        "description": "Operate as the Architect. Own the spec graph — write FRs, arbitrate ambiguity, respond to escalations.",
        "argument-hint": "FR-XXXX or free-text description",
    },
    "developer": {
        "description": "Operate as the Developer for a given FR. Implement code that satisfies its acceptance criteria — nothing more.",
        "argument-hint": "FR-XXXX",
    },
    "reviewer": {
        "description": "Operate as the Reviewer for a given FR. Write tests adversarially against every AC, then approve or kick back.",
        "argument-hint": "FR-XXXX",
    },
    "reviewer-backfill": {
        "description": "Backfill missing reviewer tests for an FR whose dev cycle is already complete (PR merged). One-shot follow-up.",
        "argument-hint": "FR-XXXX",
    },
    "escalate": {
        "description": "Escalate to the human supervisor. Stop current work and surface a structured escalation block.",
        "argument-hint": "one-line reason",
    },
    "gate": {
        "description": "Run the agent gate locally. Use before declaring any work complete.",
        "argument-hint": "--stage full (or dev)",
    },
    "status": {
        "description": "Show the current state of the spec graph — ready, in-flight, blocked, drafting.",
        "argument-hint": "FR-XXXX (optional)",
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def warn(msg: str) -> None:
    print(f"WARN: {msg}", file=sys.stderr)


def info(msg: str) -> None:
    print(f"-> {msg}")


def validate_slug(name: str) -> None:
    if not SLUG_RE.match(name):
        die(
            f"Invalid project name: '{name}'. "
            "Must match [a-z0-9]([a-z0-9-]*[a-z0-9])? "
            "(lowercase alphanumeric with optional hyphens, no leading/trailing hyphen)."
        )


def find_python() -> str:
    """Return the path to a Python 3 interpreter, or die trying."""
    for candidate in ("python3", "python"):
        path = shutil.which(candidate)
        if path:
            return path
    die("python is required (for the spec indexer)")
    return ""  # unreachable


def check_pyyaml(python: str) -> None:
    try:
        subprocess.run(
            [python, "-c", "import yaml"],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        die("pyyaml is required. Install with: pip install pyyaml")


def copy_template(src: Path, dst: Path) -> None:
    """Copy the template directory tree, excluding .git and __pycache__."""
    for item in src.iterdir():
        if item.name in (".git", "__pycache__"):
            continue
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "*.pyc",
            ))
        else:
            shutil.copy2(item, target)


def substitute_description(agents_md: Path, description: str) -> None:
    """Replace the description marker in AGENTS.md."""
    text = agents_md.read_text(encoding="utf-8")
    marker = "<<REPLACE: ONE_PARAGRAPH_PROJECT_DESCRIPTION>>"
    if marker not in text:
        warn(f"Marker '{marker}' not found in AGENTS.md -- already filled?")
    else:
        text = text.replace(marker, description)

    text = "\n".join(
        line for line in text.splitlines()
        if not line.startswith("Example (replace, then delete this line):")
    )
    if not text.endswith("\n"):
        text += "\n"
    agents_md.write_text(text, encoding="utf-8")


def inject_stack(agents_md: Path, stack: str) -> None:
    """Inject stack-specific conventions into AGENTS.md."""
    conventions = {
        "python": textwrap.dedent("""\
            - Language: Python 3.11+, type hints required
            - Test runner: `pytest`
            - Lint/format: `ruff check .` and `ruff format --check .`
            - Commit style: Conventional Commits, scope = FR ID (e.g., `feat(FR-0013): add canonical visit mapper`)
            - PRs must reference all FR IDs they touch in the title"""),
        "node": textwrap.dedent("""\
            - Language: TypeScript (strict mode), Node 20+
            - Test runner: `vitest` (or `jest` — pick one and document)
            - Lint/format: `eslint .` and `prettier --check .`
            - Commit style: Conventional Commits, scope = FR ID (e.g., `feat(FR-0013): add canonical visit mapper`)
            - PRs must reference all FR IDs they touch in the title"""),
    }

    text = agents_md.read_text(encoding="utf-8")
    marker = "<<REPLACE: PROJECT_CONVENTIONS>>"
    if marker in text:
        text = text.replace(marker, conventions[stack])
        out_lines: list[str] = []
        skipping = False
        for line in text.splitlines():
            if line.startswith("Examples (replace with real values, then delete this block):"):
                skipping = True
                continue
            if skipping:
                if line.startswith("- "):
                    continue
                if line.strip() == "":
                    skipping = False
            out_lines.append(line)
        text = "\n".join(out_lines)

    if not text.endswith("\n"):
        text += "\n"
    agents_md.write_text(text, encoding="utf-8")


def enable_cursor_rule(rule_path: Path) -> None:
    """Remove the DISABLED_BY_DEFAULT wrapper from a Cursor rule file."""
    if not rule_path.exists():
        warn(f"Cursor rule file not found: {rule_path}")
        return
    text = rule_path.read_text(encoding="utf-8")
    text = text.replace(
        "# DISABLED_BY_DEFAULT: bootstrap.sh enables this for --stack python\n", ""
    )
    text = text.replace(
        "# DISABLED_BY_DEFAULT: bootstrap.sh enables this for --stack node\n", ""
    )
    text = text.replace("<!--\n", "", 1)
    text = text.replace("\n-->", "", 1)
    rule_path.write_text(text, encoding="utf-8")


def uncomment_ci_job(workflow_path: Path, stack: str) -> None:
    """Uncomment the matching CI job block in agent-gates.yml."""
    job_markers = {"python": "python-tests:", "node": "node-tests:"}
    marker = job_markers[stack]

    text = workflow_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list[str] = []
    in_block = False
    found = False

    for line in lines:
        stripped = re.sub(r"^\s*#\s*", "", line)
        if not in_block and stripped.startswith(marker):
            in_block = True
            found = True
        if in_block:
            if line.strip() == "":
                in_block = False
                out.append(line)
                continue
            if line.lstrip().startswith("#"):
                out.append(re.sub(r"^(\s*)#\s?", r"\1", line, count=1))
            else:
                out.append(line)
        else:
            out.append(line)

    if not found:
        warn(f"'{marker}' block not found in agent-gates.yml -- skipping CI uncomment")

    workflow_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def generate_commands(target: Path) -> int:
    """Generate self-contained .claude/commands/ and .cursor/commands/
    from the canonical .agent-team/commands/ sources.

    Returns the number of commands generated.
    """
    canonical_dir = target / ".agent-team" / "commands"
    claude_dir = target / ".claude" / "commands"
    cursor_dir = target / ".cursor" / "commands"
    claude_dir.mkdir(parents=True, exist_ok=True)
    cursor_dir.mkdir(parents=True, exist_ok=True)

    if not canonical_dir.exists():
        warn(f"{canonical_dir} not found; skipping command generation")
        return 0

    generated = 0
    for src in sorted(canonical_dir.glob("*.md")):
        name = src.stem
        body = src.read_text(encoding="utf-8").strip()
        meta = COMMAND_META.get(name, {})
        desc = meta.get("description", f"Run the {name} command.")
        hint = meta.get("argument-hint", "")

        fm_lines = ["---", f'description: "{desc}"']
        if hint:
            fm_lines.append(f'argument-hint: "{hint}"')
        fm_lines.append("---")
        frontmatter = "\n".join(fm_lines)

        claude_content = (
            f"<!-- Generated from .agent-team/commands/{src.name} — do not edit by hand. -->\n"
            f"{frontmatter}\n\n"
            f"{body}\n"
        )
        (claude_dir / src.name).write_text(claude_content, encoding="utf-8")

        cursor_body = body.replace("$ARGUMENTS", "{{args}}")
        cursor_content = (
            f"<!-- Generated from .agent-team/commands/{src.name} — do not edit by hand. -->\n"
            f"{frontmatter}\n\n"
            f"{cursor_body}\n"
        )
        (cursor_dir / src.name).write_text(cursor_content, encoding="utf-8")
        generated += 1

    return generated


def validate_profiles(profile_names: list[str]) -> list[str]:
    """Validate that each profile name corresponds to a directory in profiles/."""
    valid = []
    for name in profile_names:
        profile_dir = PROFILES_ROOT / name
        if not profile_dir.is_dir():
            available = [
                d.name for d in PROFILES_ROOT.iterdir()
                if d.is_dir() and not d.name.startswith(".")
            ] if PROFILES_ROOT.is_dir() else []
            die(
                f"Unknown profile: '{name}'. "
                f"Available profiles: {', '.join(available) or '(none)'}"
            )
        valid.append(name)
    return valid


def apply_profiles(target: Path, profile_names: list[str]) -> None:
    """Overlay profile-specific files onto a bootstrapped project."""
    import json

    for name in profile_names:
        profile_dir = PROFILES_ROOT / name
        info(f"Applying profile: {name}")

        if name == "phipa":
            hook_src = profile_dir / "phi_regex_check.py"
            if hook_src.exists():
                hooks_dir = target / ".agent-team" / "hooks"
                hooks_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(hook_src, hooks_dir / "phi_regex_check.py")
                info("  Copied phi_regex_check.py → .agent-team/hooks/")

            hygiene_src = profile_dir / "phi_hygiene_block.md"
            if hygiene_src.exists():
                agents_md = target / "AGENTS.md"
                if agents_md.exists():
                    agents_text = agents_md.read_text(encoding="utf-8")
                    hygiene_block = hygiene_src.read_text(encoding="utf-8").strip()
                    marker = "## Universal rules"
                    if marker in agents_text:
                        agents_text = agents_text.replace(
                            marker,
                            hygiene_block + "\n\n" + marker,
                        )
                    else:
                        agents_text = agents_text.rstrip() + "\n\n" + hygiene_block + "\n"
                    agents_md.write_text(agents_text, encoding="utf-8")
                    info("  Appended PHI hygiene block to AGENTS.md")

            overlay_src = profile_dir / "settings_overlay.json"
            if overlay_src.exists():
                settings_path = target / ".claude" / "settings.json"
                settings_path.parent.mkdir(parents=True, exist_ok=True)
                if settings_path.exists():
                    base = json.loads(settings_path.read_text(encoding="utf-8"))
                else:
                    base = {}
                overlay = json.loads(overlay_src.read_text(encoding="utf-8"))
                base = _merge_json(base, overlay)
                settings_path.write_text(
                    json.dumps(base, indent=2) + "\n", encoding="utf-8"
                )
                info("  Merged settings_overlay.json → .claude/settings.json")
        else:
            readme = profile_dir / "README.md"
            if readme.exists():
                info(f"  Profile '{name}' recognized (see {readme})")
            else:
                warn(f"  Profile '{name}' has no apply logic yet — skipped")

    if profile_names:
        print(f"\n  Profiles applied: {', '.join(profile_names)}")


def _merge_json(base: dict, overlay: dict) -> dict:
    """Recursively merge overlay into base, concatenating lists."""
    for key, value in overlay.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            base[key] = _merge_json(base[key], value)
        elif key in base and isinstance(base[key], list) and isinstance(value, list):
            base[key] = base[key] + value
        else:
            base[key] = value
    return base


def register_project(
    hub_root: Path,
    project_name: str,
    target: Path,
    stack: str,
    private: bool,
    python: str,
) -> None:
    """Append a project entry to hub/projects.yaml."""
    try:
        import yaml  # noqa: F811
    except ImportError:
        die("pyyaml is required for project registration. Install with: pip install pyyaml")
        return

    yaml_path = hub_root / "hub" / "projects.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    projects: list = list(data.get("projects") or [])

    for p in projects:
        if p.get("name") == project_name:
            warn(
                f"Project '{project_name}' already registered at {p.get('path')}; "
                "leaving entry unchanged."
            )
            break
    else:
        try:
            rel = target.resolve().relative_to(hub_root.resolve())
            path_field = str(rel)
        except ValueError:
            path_field = str(target.resolve())

        projects.append({
            "name": project_name,
            "path": path_field,
            "created": datetime.date.today().isoformat(),
            "stack": stack,
            "private": private,
        })

    data["projects"] = projects
    header = (
        "# Project registry — maintained by scripts/bootstrap.sh\n"
        "#\n"
        "# Each entry records a project bootstrapped from this hub. The hub indexer\n"
        "# (scripts/hub-index.py) walks this list to build hub/FR-INDEX.json.\n"
        "#\n"
        "# Schema:\n"
        "#   - name: <slug>\n"
        "#     path: <absolute or relative-to-hub-root path>\n"
        "#     created: YYYY-MM-DD\n"
        "#     stack: python | node | none\n"
        "#     private: false  # if true, excluded from cross-project pattern detection\n\n"
    )
    yaml_path.write_text(
        header + yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )


def git_init(target: Path) -> None:
    """Initialize a git repo with an initial commit."""
    if not shutil.which("git"):
        warn("git not found; skipping git init")
        return

    info("Initializing git repo")
    try:
        subprocess.run(
            ["git", "init", "-q", "-b", "main"],
            cwd=target, check=True, capture_output=True,
        )
    except subprocess.CalledProcessError:
        subprocess.run(
            ["git", "init", "-q"],
            cwd=target, check=True, capture_output=True,
        )

    subprocess.run(["git", "add", "-A"], cwd=target, check=True, capture_output=True)

    try:
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "commit", "-q",
             "-m", "Initial commit from agent-team-template"],
            cwd=target, check=True, capture_output=True,
        )
    except subprocess.CalledProcessError:
        warn(
            "Initial commit skipped -- configure git user.name/user.email "
            "and commit manually."
        )


# ---------------------------------------------------------------------------
# Upgrade mode
# ---------------------------------------------------------------------------

def files_in_dir(base: Path, rel_dir: str) -> dict[str, Path]:
    """Return {relative-path: absolute-path} for all files under base/rel_dir."""
    root = base / rel_dir
    if not root.exists():
        return {}
    result: dict[str, Path] = {}
    for p in root.rglob("*"):
        if p.is_file():
            result[str(p.relative_to(base))] = p
    return result


def diff_content(old_content: str, new_content: str, label: str) -> str:
    """Return a unified-diff-style summary of changes."""
    import difflib
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff = difflib.unified_diff(old_lines, new_lines, fromfile=f"project/{label}", tofile=f"template/{label}")
    return "".join(diff)


def run_upgrade(project_path: Path, auto_yes: bool) -> None:
    """Propagate template infrastructure changes to an existing project."""
    project = project_path.resolve()
    if not project.is_dir():
        die(f"Project path does not exist: {project}")
    if not (project / "AGENTS.md").exists():
        die(f"'{project}' does not look like an agent-team project (no AGENTS.md)")

    info(f"Upgrading project at: {project}")

    updated: list[str] = []
    added: list[str] = []
    skipped: list[str] = []

    # Collect infrastructure files from template
    template_files: dict[str, Path] = {}
    for infra_dir in INFRA_DIRS:
        template_files.update(files_in_dir(TEMPLATE_ROOT, infra_dir))

    # Also include specs/_template/
    template_files.update(files_in_dir(TEMPLATE_ROOT, "specs/_template"))

    for rel_path, template_file in sorted(template_files.items()):
        project_file = project / rel_path
        template_content = template_file.read_text(encoding="utf-8")

        if not project_file.exists():
            if auto_yes:
                project_file.parent.mkdir(parents=True, exist_ok=True)
                project_file.write_text(template_content, encoding="utf-8")
                added.append(rel_path)
                print(f"  + {rel_path} (new file)")
            else:
                print(f"\n  NEW: {rel_path}")
                print("  This file exists in the template but not in your project.")
                answer = input("  Add it? [y/N] ").strip().lower()
                if answer == "y":
                    project_file.parent.mkdir(parents=True, exist_ok=True)
                    project_file.write_text(template_content, encoding="utf-8")
                    added.append(rel_path)
                else:
                    skipped.append(rel_path)
            continue

        project_content = project_file.read_text(encoding="utf-8")
        if project_content == template_content:
            continue

        diff_text = diff_content(project_content, template_content, rel_path)
        if not diff_text:
            continue

        if auto_yes:
            project_file.write_text(template_content, encoding="utf-8")
            updated.append(rel_path)
            print(f"  ~ {rel_path} (updated)")
        else:
            print(f"\n  CHANGED: {rel_path}")
            print("  " + "-" * 60)
            for line in diff_text.splitlines():
                print(f"  {line}")
            print("  " + "-" * 60)
            answer = input("  Apply this change? [y/N] ").strip().lower()
            if answer == "y":
                project_file.write_text(template_content, encoding="utf-8")
                updated.append(rel_path)
            else:
                skipped.append(rel_path)

    # Regenerate commands from .agent-team/commands/ sources
    info("Regenerating commands from .agent-team/commands/ sources")
    cmd_count = generate_commands(project)
    print(f"  Generated {cmd_count} command(s) for .claude/ and .cursor/")

    # Summary
    print("\n" + "=" * 60)
    print("Upgrade summary:")
    if updated:
        print(f"  Updated:  {len(updated)} file(s)")
        for f in updated:
            print(f"    ~ {f}")
    if added:
        print(f"  Added:    {len(added)} file(s)")
        for f in added:
            print(f"    + {f}")
    if skipped:
        print(f"  Skipped:  {len(skipped)} file(s)")
        for f in skipped:
            print(f"    - {f}")
    if not updated and not added:
        print("  Everything is already up to date.")
    if cmd_count:
        print(f"  Commands: {cmd_count} regenerated")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Create mode (main bootstrap)
# ---------------------------------------------------------------------------

def run_create(args: argparse.Namespace) -> None:
    """Bootstrap a new project from the template."""
    name_or_path: str = args.name
    description: str = args.description
    stack: str = args.stack
    profiles: list[str] = args.profile
    keep_example: bool = args.keep_example
    do_git: bool = not args.no_git
    private: bool = args.private
    register: bool = not args.no_register

    if profiles:
        profiles = validate_profiles(profiles)

    # Determine target path and project name
    if os.sep not in name_or_path and "/" not in name_or_path and not name_or_path.startswith("."):
        project_name = name_or_path
        target = HUB_ROOT / "projects" / name_or_path
    else:
        target = Path(name_or_path)
        project_name = target.name

    validate_slug(project_name)

    if target.exists() and any(target.iterdir()):
        die(f"Target '{target}' exists and is not empty")

    # Pre-flight
    python = find_python()
    check_pyyaml(python)

    # Copy template
    target.mkdir(parents=True, exist_ok=True)
    target = target.resolve()
    info(f"Copying template into {target}")
    copy_template(TEMPLATE_ROOT, target)

    # Substitute project description
    info("Filling project description in AGENTS.md")
    agents_md = target / "AGENTS.md"
    substitute_description(agents_md, description)

    # Stack-specific injection
    if stack != "none":
        info(f"Injecting {stack} conventions")
        inject_stack(agents_md, stack)
        enable_cursor_rule(target / ".cursor" / "rules" / f"20-{stack}.mdc")
        uncomment_ci_job(target / ".github" / "workflows" / "agent-gates.yml", stack)

    # Apply compliance profiles
    if profiles:
        apply_profiles(target, profiles)

    # Generate commands
    info("Generating .claude/commands/ and .cursor/commands/ from .agent-team/commands/")
    cmd_count = generate_commands(target)
    print(f"  Generated {cmd_count} command(s) for .claude/ and .cursor/", file=sys.stderr)

    # Create dispatch directory
    info("Creating _dispatch/ directory")
    dispatch_dir = target / "_dispatch"
    dispatch_dir.mkdir(parents=True, exist_ok=True)
    (dispatch_dir / ".gitkeep").touch()

    # Create worktrees directory
    worktrees_dir = target / ".claude" / "worktrees"
    worktrees_dir.mkdir(parents=True, exist_ok=True)

    # Delete example FR
    example_fr = target / "specs" / "FR-0001-example.md"
    if not keep_example and example_fr.exists():
        info("Removing example FR-0001")
        example_fr.unlink()

    # Regenerate spec index
    info("Regenerating specs/INDEX.md")
    subprocess.run([python, "scripts/index-specs.py"], cwd=target, check=True)

    # Register in hub
    if register:
        info("Registering project in hub/projects.yaml")
        register_project(HUB_ROOT, project_name, target, stack, private, python)

        info("Refreshing hub/FR-INDEX.json")
        subprocess.run(
            [python, "scripts/hub-index.py"], cwd=HUB_ROOT, check=True
        )

    # Git init
    if do_git:
        git_init(target)

    # Done
    conventions_note = ""
    if stack == "none":
        conventions_note = "\n       - <<REPLACE: PROJECT_CONVENTIONS>>"
    print(f"""
\u2705 Project ready at: {target}

Next steps:
  1. cd "{target}"
  2. Open AGENTS.md and fill in:
       - <<REPLACE: PROJECT_GLOSSARY>>{conventions_note}
  3. Open the project in Claude Code or Cursor, then run:
       /architect <one-line description of your first FR>
  4. Verify the gate: python scripts/deploy-gate.py

Roles available via slash commands:
  /architect [FR-XXXX | description]
  /developer FR-XXXX
  /reviewer FR-XXXX
  /reviewer-backfill FR-XXXX
  /gate [--stage dev|full]
  /status [FR-XXXX]
  /escalate <reason>""")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bootstrap.py",
        description="Stand up or upgrade an agent-team project from the hub template.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              # Create a new project
              python scripts/bootstrap.py helena-emr-mapper \\
                "Bidirectional EMR transformation layer for Helena." --stack python

              # Create a project at a custom path
              python scripts/bootstrap.py ~/code/some-other-project \\
                "External project, lives outside the hub." --stack node

              # Upgrade an existing project with template changes
              python scripts/bootstrap.py --upgrade projects/my-project

              # Upgrade non-interactively (e.g. in CI)
              python scripts/bootstrap.py --upgrade projects/my-project --yes
        """),
    )

    parser.add_argument(
        "--upgrade",
        metavar="PROJECT_PATH",
        help="Propagate template infrastructure changes to an existing project. "
             "Diffs .agent-team/, scripts/, .github/workflows/, .claude/, "
             ".cursor/commands/, .cursor/rules/, and specs/_template/ against the "
             "template and offers to update divergent files.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Apply all upgrades without interactive confirmation (for --upgrade mode).",
    )

    parser.add_argument(
        "name",
        nargs="?",
        help="Project name (slug) or path. If no slash, lands under projects/<name>.",
    )
    parser.add_argument(
        "description",
        nargs="?",
        help="One-paragraph project description (injected into AGENTS.md).",
    )
    parser.add_argument(
        "--stack",
        choices=["python", "node", "none"],
        default="none",
        help="Inject stack-specific conventions and CI (default: none).",
    )
    parser.add_argument(
        "--profile",
        action="append",
        default=[],
        metavar="NAME",
        help="Activate a compliance profile (can be repeated). "
             "Validated against directories in profiles/.",
    )
    parser.add_argument(
        "--private",
        action="store_true",
        help="Mark project as private (excluded from cross-project pattern detection).",
    )
    parser.add_argument(
        "--keep-example",
        action="store_true",
        help="Keep specs/FR-0001-example.md (default: delete it).",
    )
    parser.add_argument(
        "--no-git",
        action="store_true",
        help="Skip git init and initial commit.",
    )
    parser.add_argument(
        "--no-register",
        action="store_true",
        help="Don't append to hub/projects.yaml.",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.upgrade:
        project_path = Path(args.upgrade)
        if not project_path.is_absolute():
            project_path = HUB_ROOT / project_path
        run_upgrade(project_path, auto_yes=args.yes)
    elif args.name and args.description:
        run_create(args)
    elif args.name and not args.description:
        die(
            "Description is required.\n"
            f'Usage: python scripts/bootstrap.py {args.name} "Your project description"'
        )
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
