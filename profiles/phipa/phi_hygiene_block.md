## PHI hygiene (non-negotiable — applies to all roles)

This project handles data regulated under PHIPA (or equivalent healthcare privacy legislation). The hooks under `.agent-team/hooks/` and the wired `.claude/settings.json` enforce this defensively. Do not disable or bypass these controls without explicit human approval.

While this block remains, every role enforces these:

- **No real client/patient names, IDs, addresses, DOBs, health card numbers, or SINs in chat, prompts, specs, code, tests, or commit messages.** Use ticket IDs, FR IDs (FR-XXXX:AC-Y), and synthetic identifiers ("Patient A", "Provider 1") instead.
- **Test fixtures use synthetic data only.** Generated, not derived from real records. Fixtures live in `tests/fixtures/` and are reviewed for hygiene before commit.
- **Real-data testing happens in staging environments**, not via agent sessions. If you need to verify against real records, exit the agent session, run the verification manually in the staging tool, and bring back only the *outcome* (pass/fail/observation) — not the data.
- **Escalations cite IDs, not details.** "FR-0007:AC-2 ambiguous on retention behavior" — not "the issue with the record for [name]".
- **If you encounter real PHI in a file you're asked to edit**, stop, do not commit, flag to the human supervisor. The `phi_regex_check.py` hook will also catch obvious patterns and block the write — treat any block as a hard stop, not a thing to work around.
