# Agent Guidance For x_trigger_prompt_x

Use this file for repository-specific execution guidance.

## Default Execution Path

- Use deterministic no-design slices for script hardening, docs updates, and test additions.
- Stop and ask for explicit decision before changing automation safety boundaries or escalation policy semantics.

## Local Development Rules

- Keep GUI-affecting operations behind explicit runtime invocations.
- Keep tests mock-driven and CI-safe; do not rely on an interactive desktop in tests.
- Preserve strict validation boundaries for prompt count, profile loading, and click-coordinate normalization.

## Local Validation

Use the workspace interpreter explicitly:

```powershell
c:/Users/primu/OneDrive/Desktop/ppnw_2026_07/.venv/Scripts/python.exe -m pytest -q
c:/Users/primu/OneDrive/Desktop/ppnw_2026_07/.venv/Scripts/python.exe -m ruff check .
c:/Users/primu/OneDrive/Desktop/ppnw_2026_07/.venv/Scripts/python.exe -m black --check .
c:/Users/primu/OneDrive/Desktop/ppnw_2026_07/.venv/Scripts/python.exe -m mypy
```

## CI Closure

- Close slices only when GitHub Actions `Quality Gates` completes with success for the pushed SHA.
- Do not treat partial gate runs as closure.

## Security Rules

- Never commit secrets, private prompts, or sensitive captures.
- Keep automation constrained to user-approved windows and flows.
