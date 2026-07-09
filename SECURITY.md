# Security Policy

## Supported Versions

The current supported development version is `0.0.1`.

## Safety Boundary

`x_trigger_prompt_x` is a local desktop automation utility. It is intended to interact only with user-visible VS Code windows under explicit operator control.

This repository may contain:

- Source code.
- Public-safe docs.
- Tests with synthetic fixtures and mocks.

This repository must not contain:

- API tokens, session cookies, credentials, or local secret material.
- Real private chat transcripts.
- Sensitive screenshots or captures from private windows.
- Machine-specific private monitoring artifacts.

## Runtime Safety Guidance

- Keep PyAutoGUI fail-safe enabled.
- Run with `--dry-run` before live automation in new environments.
- Restrict automation scope to an intended VS Code window title pattern.
- Treat stop-template images as potentially sensitive if they include private text; store and share carefully.

## CI And Tests

CI is mock-driven and does not rely on GUI interaction, credentials, or private data.

## Reporting Issues

If you discover automation safety bugs or potential data exposure:

1. Stop automation immediately.
2. Remove or rotate exposed secret material if applicable.
3. Open a security issue with repro details and impact scope.
