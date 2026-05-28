# PHIPA Profile

This profile adds PHI (Protected Health Information) hygiene controls for projects operating under Canada's PHIPA or similar healthcare privacy regulations.

## What it adds

When you bootstrap a project with `--profile phipa`, the following overlays are applied on top of the generic template:

1. **PHI hygiene block in AGENTS.md** — non-negotiable rules forbidding real patient data in code, tests, specs, chat, or commits.
2. **`phi_regex_check.py` hook** — a PreToolUse hook that pattern-matches proposed writes against common PHI formats (Canadian SIN, Ontario Health Card Number, US SSN) and blocks the write if a match is found.
3. **Settings overlay** — wires the PHI regex hook into `.claude/settings.json` so it fires automatically on every Write/Edit/MultiEdit.

## Usage

```bash
python scripts/bootstrap.py my-healthcare-project "EMR integration layer" --stack python --profile phipa
```

## False positives

The regex hook is intentionally coarse. Synthetic test data that happens to match PHI patterns (e.g., a 9-digit number formatted like a SIN) will trigger a block. When this happens, the human supervisor decides whether to add an exemption to `.agent-team/hooks/phi_exemptions.txt` or rework the input. The hook should never be silently bypassed.

## Customization

After bootstrap, the PHI controls live in the project's own files and can be tuned per-project:
- Edit `.agent-team/hooks/phi_regex_check.py` to adjust patterns
- Edit `AGENTS.md` to refine the hygiene rules for your jurisdiction
- Edit `.claude/settings.json` to change hook wiring
