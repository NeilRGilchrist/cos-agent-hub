# Contributing

Thanks for your interest in contributing to the Agent Team Workspace Hub! This document covers the basics of getting set up, making changes, and submitting them.

## Quick setup

1. Fork and clone the repo
2. Follow [GETTING-STARTED.md](GETTING-STARTED.md) for prerequisites (Python 3.11+, pyyaml)
3. Verify your setup: `python scripts/bootstrap.py --help`

## Branch naming

- `feature/<short-desc>` — new functionality
- `fix/<short-desc>` — bug fixes

## PR workflow

1. Fork the repository
2. Create a branch from `main` (`git checkout -b feature/my-change`)
3. Make your changes
4. Open a pull request against `main`

## Three levels of change

Changes in this repo fall into one of three scopes. Please indicate in your PR which level(s) you're touching:

### Hub-level

Files at the root and under `scripts/`, `hub/`, `parking-lot/`, `patterns/`, `.claude/commands/`. These affect the cross-project coordination layer — parking lot, pattern catalog, project registry, and hub slash commands.

### Template-level

Files under `template/`. These define the default project shape that `bootstrap.py` copies when creating new projects. Changes here affect all *future* bootstrapped projects (existing projects can pull updates via `--upgrade`).

### Project-level

Files under `projects/<name>/`. These are self-contained and only affect a specific project. Most project-level changes happen *within* the project using the agent team roles, not via hub PRs.

## Adding a new compliance profile

Profiles live under `profiles/<name>/`. To add one:

1. Create `profiles/<name>/` with at minimum a `README.md` explaining what the profile does, who it's for, and what it adds to bootstrapped projects.
2. Add any hook scripts, settings overlays, or documentation blocks that should be copied into projects.
3. Update `apply_profiles()` in `scripts/bootstrap.py` to handle the new profile name.
4. Test it: `python scripts/bootstrap.py test-proj "test" --stack python --profile <name>` and verify the output.
5. Clean up: remove the test project after verifying.

See `profiles/phipa/` for a reference implementation.

## Testing your changes

```bash
# Verify hub scripts run cleanly
python scripts/hub-index.py
python scripts/parking.py reindex
python scripts/patterns.py reindex

# Test project bootstrap
python scripts/bootstrap.py test-proj "test" --stack python --no-register --no-git
# Inspect projects/test-proj/ to verify output, then remove it
```

## Code style

- Python scripts target 3.11+ with type hints
- No external dependencies beyond `pyyaml`
- Cross-platform: all scripts must work on Windows, macOS, and Linux
