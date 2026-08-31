# `docs/`

Documentation for this project. The client-facing layer is a **projection of the
spec graph**, not hand-written prose.

| Path | Owner | Hand-edited? |
|------|-------|--------------|
| `docs/_spec/` | Architect / Docs role | Yes — but IDs only, no prose (see `_spec/README.md`) |
| `docs/client/` | `scripts/render-docs.py` | **No** — generated; edit the spec graph instead |
| `docs/_render.json` | `scripts/render-docs.py` | No — render stamp |
| `docs/_gaps.md` | `scripts/render-docs.py` | No — warn-only gap report |
| everything else (`docs/architecture/`, `docs/sources/`, …) | humans | Yes — ordinary project docs |

## Regenerating client docs

Documentation maintenance is **regenerate-and-lint**, run through the `docs` role
(`/docs`), never by hand-editing `docs/client/`:

```
python scripts/render-docs.py          # render every doc spec
python scripts/render-docs.py overview # render one
```

The `docs` role's deny list makes editing `specs/**` from a docs session a
process violation; discrepancies it finds are surfaced as a `## FINDINGS` block,
not fixed in place. See `.agent-team/roles/docs.md`.
