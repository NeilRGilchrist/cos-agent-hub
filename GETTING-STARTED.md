# Getting Started with Agent Team Template

This guide walks you through setting up the hub and bootstrapping your first project with role-based AI agent coordination.

## Prerequisites

| Tool | Required | Notes |
|------|----------|-------|
| **Python 3.11+** | Yes | `python --version` or `python3 --version` to check |
| **pyyaml** | Yes | `pip install pyyaml` (used by spec indexer, deploy gate, hub scripts) |
| **git** | Yes | For project initialization and version control |
| **gh** (GitHub CLI) | Recommended | Used by Developer/Reviewer roles for PR creation |
| **Claude Code** or **Cursor** | Yes | The IDE where agents execute slash commands |

### Platform notes

- **macOS / Linux**: Everything works natively.
- **Windows**: `bootstrap.py` and all hub Python scripts (`parking.py`, `patterns.py`, `hub-index.py`) work natively — no WSL or Git Bash required. If you use bash-specific tooling elsewhere in your workflow, Git Bash or WSL2 will cover it.

## First-time setup

1. **Clone the repository:**

   ```bash
   git clone <your-hub-repo-url>
   cd agent-team-template
   ```

2. **Install Python dependencies:**

   ```bash
   pip install pyyaml
   ```

   Or, if you prefer an isolated environment:

   ```bash
   python -m venv .venv
   # macOS/Linux:
   source .venv/bin/activate
   # Windows (PowerShell):
   .venv\Scripts\Activate.ps1

   pip install pyyaml
   ```

3. **Verify your setup:**

   ```bash
   python scripts/bootstrap.py --help
   ```

   You should see the full usage output with create and upgrade modes.

## Creating your first project

```bash
python scripts/bootstrap.py my-project \
  "Brief description of what this project does." \
  --stack python
```

This does several things:
- Copies the template into `projects/my-project/`
- Fills in your description in `AGENTS.md`
- Injects Python conventions (pytest, ruff, Conventional Commits)
- Enables the Python CI job in `.github/workflows/agent-gates.yml`
- Generates `.claude/commands/` and `.cursor/commands/` from canonical sources
- Creates `_dispatch/` and `.claude/worktrees/` directories
- Registers the project in `hub/projects.yaml`
- Initializes a git repo with an initial commit

### Finishing setup

After bootstrap, open `projects/my-project/AGENTS.md` and fill in the remaining markers:

- `<<REPLACE: PROJECT_GLOSSARY>>` — domain terms your agents need to know
- `<<REPLACE: PROJECT_CONVENTIONS>>` — only if you used `--stack none`

Then open the project in Claude Code or Cursor and run:

```
/architect Describe your first feature requirement here
```

The Architect will draft an FR, and from there the Dev → Review cycle begins.

### Stack options

| Flag | What it sets up |
|------|----------------|
| `--stack python` | Python 3.11+, pytest, ruff, Conventional Commits |
| `--stack node` | TypeScript strict, Node 20+, vitest/jest, eslint + prettier |
| `--stack none` | No conventions injected — fill in `PROJECT_CONVENTIONS` manually |

### Other useful flags

- `--private` — exclude from cross-project pattern detection
- `--keep-example` — keep the example FR-0001 spec for reference
- `--no-git` — skip git init (if the project will live inside an existing repo)
- `--no-register` — don't add to `hub/projects.yaml` (for one-off scaffolds)

## Upgrading existing projects

When the template evolves (new role boundaries, updated scripts, new commands), propagate changes to existing projects:

```bash
python scripts/bootstrap.py --upgrade projects/my-project
```

This will:
- Diff infrastructure files (`.agent-team/`, `scripts/`, `.github/workflows/`, `.claude/commands/`, `.cursor/commands/`, `.cursor/rules/`, `specs/_template/`) against the template
- Show each diff and prompt for confirmation
- Never touch project-specific files (`specs/`, `src/`, `tests/`, `AGENTS.md`, `CLAUDE.md`)
- Regenerate command files from `.agent-team/commands/` sources

For non-interactive use (CI or scripting):

```bash
python scripts/bootstrap.py --upgrade projects/my-project --yes
```

## Mental model

### Hub vs. project

```
agent-team-template/          ← THE HUB
├── parking-lot/               Ideas not yet ready for action
├── patterns/                  Reusable shapes across projects
├── hub/                       Cross-project registry and FR index
├── scripts/                   Hub-level tools (bootstrap, parking, patterns)
├── .claude/commands/          Hub slash commands (/cos, /park, /promote, etc.)
└── projects/
    └── my-project/            ← A PROJECT
        ├── .agent-team/       Role definitions and command sources
        ├── specs/             FRs (one per capability)
        ├── src/               Implementation code
        ├── tests/             Test code with @covers traceability
        └── scripts/           Project-level tools (deploy-gate, index-specs)
```

The **hub** is the cross-project layer. It manages ideas, patterns, and the project registry. You work here when triaging, reflecting, or looking for patterns across projects.

A **project** is a self-contained scaffold with roles, specs, and gates. You work here when building features.

### Roles

Each project has three agent roles, invoked via slash commands:

| Role | Command | Writes to | Cannot touch |
|------|---------|-----------|-------------|
| **Architect** | `/architect` | `specs/` | `src/`, `tests/`, `.agent-team/` |
| **Developer** | `/developer FR-XXXX` | `src/` | `tests/`, `specs/` (content), `.agent-team/` |
| **Reviewer** | `/reviewer FR-XXXX` | `tests/` | `src/`, `specs/` (content), `.agent-team/` |

Roles communicate through artifacts (spec files, PR descriptions, review comments) — never through shared chat context. This is by design: each role invocation starts fresh with only the files as context.

### Spec graph

Every change traces to a **Functional Requirement** (FR) with **Acceptance Criteria** (ACs):

```
FR-0013: Add canonical visit mapper
  AC-1: Maps external visit record to canonical Visit model
  AC-2: Handles missing optional fields gracefully
  AC-3: Rejects visits with no patient identifier
```

- Source code tags: `@implements FR-0013`
- Test tags: `@covers FR-0013:AC-1`
- The deploy gate (`python scripts/deploy-gate.py`) validates that every AC in a `deployed` FR has a corresponding `@covers` test.

### Hub commands

These work at the hub level (not inside projects):

| Command | Purpose |
|---------|---------|
| `/cos <input>` | Chief of Staff — triages into chat / park / promote / pattern |
| `/park <idea>` | Fast-capture an idea to the parking lot |
| `/promote IDEA-NNNN` | Move a parked idea to an active FR in a project |
| `/patterns [tag]` | Detect and propose reusable patterns across projects |
| `/hub-status` | Dashboard of all projects, ideas, and patterns |

## Quick reference

```bash
# Create a project
python scripts/bootstrap.py my-project "Description" --stack python

# Upgrade a project to latest template
python scripts/bootstrap.py --upgrade projects/my-project

# Rebuild the cross-project FR index
python scripts/hub-index.py

# Manage the parking lot
python scripts/parking.py add "Quick idea about caching"
python scripts/parking.py list
python scripts/parking.py show IDEA-0001

# Work with patterns
python scripts/patterns.py list
python scripts/patterns.py propose

# Inside a project — run the deploy gate
cd projects/my-project
python scripts/deploy-gate.py --stage full

# Inside a project — check spec graph status
python scripts/agent-status.py --all
```

## Further reading

- `CLAUDE.md` — Hub-level working agreement and universal rules
- `template/AGENTS.md` — Project-level working agreement template
- `.agent-team/roles/architect.md` — Architect role definition
- `.agent-team/roles/developer.md` — Developer role definition
- `.agent-team/roles/reviewer.md` — Reviewer role definition
- `.agent-team/escalation-matrix.md` — When and how to escalate
- `CONTRIBUTING.md` — How to contribute to this hub
