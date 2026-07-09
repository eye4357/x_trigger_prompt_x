# x_trigger_prompt_x

Auto-submit one or more prompts to VS Code Copilot Chat when chat is idle.

## What It Does

- Monitors a VS Code window continuously.
- Detects whether Copilot Chat appears active (stop button visible) or idle.
- When idle, focuses chat input, pastes your prompt, and presses Enter.
- Repeats until the configured submit count is reached (`1` to `512`).

## Install

From this folder:

```powershell
python -m pip install -r requirements.txt
```

## Quick Start

```powershell
python auto_trigger_copilot_chat.py --prompt "Continue the glidepath. Use the same plan format." --max-prompts 32
```

Default behavior:

- `--max-prompts` defaults to `1`.
- Polling loop runs until it submits the requested count.
- Chat focus hotkey defaults to `ctrl+alt+i`.

## Recommended Reliable Mode (Template + Optional Click Point)

UI labels can vary by version. For best reliability:

1. Capture a small screenshot of the Copilot stop button and save it as `stop_button.png`.
2. Optionally identify chat input click coordinates if hotkey focus is inconsistent.

Example:

```powershell
python auto_trigger_copilot_chat.py `
  --prompt-file .\prompt.txt `
  --max-prompts 128 `
  --stop-template .\stop_button.png `
  --template-confidence 0.90 `
  --input-click-x 1450 `
  --input-click-y 990
```

## Useful Flags

- `--prompt "..."`: Prompt text inline.
- `--prompt-file path.txt`: Prompt from file.
- `--max-prompts N`: Number of submissions (`1-512`).
- `--poll-seconds 1.0`: Monitor interval.
- `--submit-cooldown-seconds 1.5`: Delay after sending each prompt.
- `--chat-focus-hotkey ctrl+alt+i`: Shortcut used to focus chat input.
- `--stop-template stop_button.png`: Enable image matching for stop button.
- `--disable-uia-scan`: Skip UI Automation button-name detection.
- `--input-click-x/--input-click-y`: Click input box before paste/send.
- `--dry-run`: Logs actions without sending prompt text.

## Safety Notes

- Keep `pyautogui` fail-safe enabled (default in script): move mouse to top-left corner to interrupt quickly.
- Run with `--dry-run` first to verify state detection before live sending.
- Keep VS Code visible on the target display while running.

## Known Limitations

- Copilot UI/accessibility names can change by VS Code or extension version.
- Template matching is sensitive to scaling/theme changes; recapture template if detection drifts.
- Multi-monitor and DPI scaling can impact coordinate-based clicking.
