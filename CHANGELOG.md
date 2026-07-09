# Changelog

## Release Notes Index

- [0.0.1](RELEASE_NOTES_0.0.1.md)

## 0.0.1 - 2026-07-09

### Added

- Initial productionized release of `x_trigger_prompt_x`.
- Continuous monitor loop for VS Code Copilot Chat idle/active detection.
- Early-stop keyword support (`HALT NOW` by default) for automated orchestration.
- Multi-template and scale-sweep stop-button matching for better cross-display reliability.
- Calibration helper script for template capture and profile generation.
- Profile-driven configuration support for template, click target, and behavior defaults.
- Strict dev quality gate with Ruff, Black, strict mypy, and pytest.
- GitHub Actions CI workflow running the same local quality gate.
- VS Code strict Python settings and pytest discovery defaults.
- Public-safe unit tests with mocked UI/runtime boundaries.
- Security and change-control baseline documentation.
