# x_trigger_prompt_x

Productionized local automation utility for continuous VS Code Copilot Chat prompting.

Version: `0.0.1`

## Use Case

`x_trigger_prompt_x` is for unattended or low-touch glidepath execution where the same deterministic prompt must be re-sent whenever Copilot Chat becomes idle.

Typical scenario:

- You run a deterministic 5.3 Codex execution prompt.
- You want repeated submission while chat is idle.
- You want the loop to halt early when a sentinel keyword (default `HALT NOW`) appears, signaling escalation or stop conditions.

## How The Tool Works

Main runtime: `auto_trigger_copilot_chat.py`

1. Finds a matching VS Code window.
2. Detects chat state.
   - Active: stop button detected (UI Automation and/or image templates).
   - Idle: stop button not detected.
3. If idle, focuses chat input, pastes prompt text, sends Enter.
4. Repeats until one of these is true:
   - `--max-prompts` reached (`1` to `512`).
   - Halt keyword detected in chat output (unless disabled).
   - Operator interrupts with `Ctrl+C`.

Calibration helper: `calibrate_trigger_profile.py`

- Captures a stop-button template image.
- Captures chat-input click point.
- Saves absolute and ratio coordinates in `trigger_profile.json`.
- Enables more portable runs across different display sizes.

## Install

Runtime only:

```powershell
python -m pip install -r requirements.txt
```

Development toolchain:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

## Quick Start

Inline prompt:

```powershell
python auto_trigger_copilot_chat.py --prompt "Continue deterministic glidepath execution." --max-prompts 32
```

Profile-based run (recommended):

```powershell
python calibrate_trigger_profile.py
python auto_trigger_copilot_chat.py --prompt-file .\prompt.txt --max-prompts 128 --profile-file .\trigger_profile.json
```

## Early Stop Keyword

Default halt keyword is `HALT NOW`.

If that keyword appears in chat output, the monitor exits early.

```powershell
python auto_trigger_copilot_chat.py --prompt-file .\prompt.txt --halt-keyword "HALT NOW"
```

Disable halt keyword scan:

```powershell
python auto_trigger_copilot_chat.py --prompt-file .\prompt.txt --disable-halt-keyword-scan
```

## Reliability And Resolution Portability

Best reliability stack:

1. Keep UI Automation scan enabled.
2. Provide multiple stop-button templates.
3. Use template scale sweep.
4. Prefer ratio-based click coordinates (`--input-click-x-ratio`, `--input-click-y-ratio`).

Example:

```powershell
python auto_trigger_copilot_chat.py `
  --prompt-file .\prompt.txt `
  --max-prompts 256 `
  --stop-template .\templates\stop_dark.png `
  --stop-template .\templates\stop_light.png `
  --stop-template-glob .\templates\stop_scale_*.png `
  --template-scales 0.85,0.92,1.0,1.08,1.15 `
  --input-click-x-ratio 0.80 `
  --input-click-y-ratio 0.92
```

Important limits:

- No GUI automation can be perfectly resolution-agnostic across all themes/scales/layouts.
- Template matching may require recapture after major VS Code theme or zoom changes.
- Window-relative ratios reduce, but do not eliminate, layout drift issues.

## CLI Flags (Core)

- `--version`
- `--prompt "..."`
- `--prompt-file path.txt`
- `--profile-file trigger_profile.json`
- `--max-prompts N` (`1-512`)
- `--poll-seconds 1.0`
- `--submit-cooldown-seconds 1.5`
- `--stop-template path.png` (repeatable)
- `--stop-template-glob .\templates\stop_*.png` (repeatable)
- `--template-confidence 0.90`
- `--template-scales 0.85,0.92,1.0,1.08,1.15`
- `--halt-keyword "HALT NOW"`
- `--disable-halt-keyword-scan`
- `--disable-uia-scan`
- `--chat-focus-hotkey ctrl+alt+i`
- `--input-click-x/--input-click-y`
- `--input-click-x-ratio/--input-click-y-ratio`
- `--dry-run`

## Development And Release Checks

Run the full local quality gate:

```powershell
python -m pytest
ruff check .
black --check .
mypy
```

CI is configured in `.github/workflows/ci.yml` to run the same checks on push and pull request.

## Release Checklist

Use this checklist before cutting or publishing a release:

1. Confirm version consistency across `pyproject.toml`, script `--version` outputs, `CHANGELOG.md`, and `CHANGE_CONTROL_PACKET.md`.
2. Run full local quality gate:
   - `python -m pytest`
   - `ruff check .`
   - `black --check .`
   - `mypy`
3. Verify `README.md` examples and flags match the current CLI behavior.
4. Verify calibration/profile flow still works end-to-end in `--dry-run` mode.
5. Commit and push to `main`.
6. Confirm GitHub Actions `Quality Gates` succeeds for the pushed SHA.
7. Publish or update versioned release notes.

Current versioned notes: `RELEASE_NOTES_0.0.1.md`.

## Testing Strategy

- Tests are mock-driven and public-safe.
- No GUI session is required for CI test execution.
- Argument validation, profile loading, deterministic helper behavior, and window selection logic are covered.

## Safety Notes

- `pyautogui` fail-safe is enabled; move mouse to top-left to interrupt.
- Start with `--dry-run` in new environments.
- Restrict `--vs-title-regex` if multiple VS Code windows are open.
- Only run this against user-approved local workflows.

## Troubleshooting

`No matching VS Code window found`:

- Confirm VS Code is open and visible.
- Adjust `--vs-title-regex` for your title format.

False active/idle detection:

- Recalibrate templates using `calibrate_trigger_profile.py`.
- Add theme-specific templates and keep UI Automation scan enabled.

Misaligned click target:

- Prefer ratio coordinates.
- Recalibrate after DPI or monitor changes.
