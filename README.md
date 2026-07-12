# x_trigger_prompt_x

Productionized local automation utility for continuous VS Code Copilot Chat prompting.

Version: `0.0.1`

## One Command

Run this from the `x_trigger_prompt_x` folder:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_trigger_prompt.ps1
```

That script is now the normal operator interface. It embeds Prompt a1, asks a short Q&A, calibrates if needed, and then launches `x_trigger_prompt_x.py` with the selected options.

## What The Launcher Asks

The Q&A covers the runtime choices that used to be scattered across README examples:

- max prompt submissions, default `128`
- profile path, default `.\trigger_profile.json`
- whether to run calibration if the profile is missing
- whether to disable UI Automation scan when false active-state detection is stuck
- whether to enable centroid debug logging
- whether to run in `--dry-run` mode
- whether to open a separate visible PowerShell window

The embedded prompt is the full Prompt a1 deterministic 5.3 Codex payload. You do not need to copy prompt text into `$prompt` anymore.

## Recommended Flow

1. Open VS Code with Copilot Chat visible.
2. Open PowerShell in this repo folder.
3. Run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_trigger_prompt.ps1
```

4. Accept the defaults for normal deterministic glidepath execution.
5. If the profile is missing, answer yes to calibration and follow the on-screen calibration prompts.

For recurring use, the usual defaults are enough: `128` prompts, `.\trigger_profile.json`, calibration if missing, UIA scan enabled, centroid debug off, dry run off, same window.

## What The Tool Does

`run_trigger_prompt.ps1` calls `x_trigger_prompt_x.py`, which:

1. Finds a matching VS Code window.
2. Detects whether Copilot Chat is active or idle.
3. When idle, focuses the chat composer, verifies the target is not terminal/output/debug-console, clears the composer, pastes Prompt a1, and submits it.
4. Repeats until `--max-prompts` is reached, the halt keyword appears, or the operator interrupts with `Ctrl+C`.

Calibration is handled by `calibrate_trigger_profile.py`. The generated profile can contain both absolute and ratio click coordinates; runtime normalizes that profile to ratio coordinates for portability.

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

## Safety Contract

Default submit behavior is fail-closed:

1. The click target must be in the right-lower chat composer band.
2. The focused control and its UIA ancestry must not contain terminal, output, debug-console, shell, or console markers.
3. The tool verifies focus before clearing text and again before pasting.
4. If verification fails, it logs `submit_decision=...` with the target and reason, skips the cycle, and does not paste.
5. Hotkey focus fallbacks are disabled by default to avoid closing or toggling the chat pane.

`pyautogui` fail-safe is enabled; move the mouse to the top-left corner to interrupt.

## Troubleshooting

`No matching VS Code window found`:

- Confirm VS Code is open and visible.
- Restrict or adjust `--vs-title-regex` only when running the Python CLI directly for development.

False active detection, where the launcher keeps waiting even though chat is idle:

- Re-run the launcher and answer yes to disabling UIA scan.
- Recalibrate if the stop-button template is stale.

Misaligned click target:

- Re-run calibration from the launcher when prompted.
- Recalibrate after DPI, monitor, theme, or VS Code layout changes.
- Enable centroid debug logging in the launcher Q&A if the target still drifts.

Blocked submit with `submit_decision=paste_blocked`:

- Read the logged reason; terminal/output/debug-console ancestry is intentionally blocked.
- Recalibrate so the profile points at the chat composer.

## Development Checks

Run the full local quality gate:

```powershell
python -m pytest
ruff check .
black --check .
mypy
```

Validate the PowerShell launcher without starting the trigger loop:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_trigger_prompt.ps1 -UseDefaults -DryRunDefault -DefaultMaxPrompts 1 -ValidateOnly
```

CI is configured in `.github/workflows/ci.yml` to run the Python checks on push and pull request.

## Advanced CLI Reference

Normal operation should use `run_trigger_prompt.ps1`. The Python flags remain available for development and testing:

- `--version`
- `--prompt "..."`
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
- `--allow-verified-hotkey-fallback`
- `--input-click-x/--input-click-y`
- `--input-click-x-ratio/--input-click-y-ratio`
- `--log-centroid-debug`
- `--dry-run`
