# kit/ — agent-facing knowledge

| Path | Role |
|------|------|
| `skills/` | Stage `SKILL.md` |
| `schemas/artifacts/` | JSON Schema exported from `app.contracts.artifacts` only |

`app.contracts.llm` / `app.contracts.db` stay **Python-only** (no parallel JSON under kit).  
Runtime workspaces: `data/run_workspaces/`.  
Regenerate: `PYTHONPATH=. python -m app.contracts.export_json_schemas`

See `docs/stage-artifact-layout.md`.
