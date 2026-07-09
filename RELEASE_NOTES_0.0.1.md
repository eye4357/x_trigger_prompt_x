# Release Notes 0.0.1

Date: 2026-07-09
Release type: Initial productionized baseline

## Summary

`0.0.1` establishes the first release-ready baseline for `x_trigger_prompt_x`, including runtime automation behavior, calibration tooling, strict development gates, CI enforcement, and repository governance artifacts.

## Highlights

- Added production packaging metadata and tool configuration in `pyproject.toml`.
- Added strict local quality gate: `pytest`, `ruff`, `black --check`, and strict `mypy`.
- Added GitHub Actions CI workflow that mirrors local quality gates.
- Added strict VS Code workspace Python settings.
- Added/expanded public docs: README, changelog, security policy, change-control packet, and agent guidance.
- Added mock-driven tests for argument validation, profile parsing, deterministic helper behavior, and window-selection logic.
- Added runtime-safe dependency loading paths for better headless/static-check robustness.

## Runtime Feature Set In 0.0.1

- Continuous monitor loop for Copilot Chat idle/active detection.
- Repeat prompt submission with bounded loop count (`1` to `512`).
- Early-stop keyword support (`HALT NOW` by default).
- UI Automation plus template matching support for stop-button detection.
- Multi-template and scale-sweep template matching.
- Calibration helper for template capture and click-target profile generation.
- Window-relative ratio click coordinates for improved cross-resolution portability.

## Safety And Operational Notes

- GUI automation remains environment-sensitive; no guarantee of perfect layout portability across all DPI/theme/layout combinations.
- Use `--dry-run` when validating new machines, themes, or monitor setups.
- Treat captured templates as potentially sensitive if private UI text is visible.

## Validation Status For This Release

The release baseline passed:

- `python -m pytest`
- `python -m ruff check .`
- `black --check .`
- `mypy`

## Upgrade Guidance

For future versions:

1. Update version in `pyproject.toml` and script version outputs.
2. Add a new section to `CHANGELOG.md`.
3. Create `RELEASE_NOTES_<version>.md`.
4. Re-run full local quality gate and confirm CI success on pushed SHA.
