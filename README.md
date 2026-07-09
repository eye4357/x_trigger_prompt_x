# x_trigger_prompt_x

Auto-submit one or more prompts to VS Code Copilot Chat when chat is idle.

## What It Does

- Monitors a VS Code window continuously.
- Detects whether Copilot Chat appears active (stop button visible) or idle.
- When idle, focuses chat input, pastes your prompt, and presses Enter.
- Repeats until the configured submit count is reached (`1` to `512`).
- Exits early if a configured halt keyword appears in chat output (default: `HALT NOW`).

## Install

From this folder:

```powershell
python -m pip install -r requirements.txt
```

## Quick Start

```powershell
python auto_trigger_copilot_chat.py --prompt "Continue the glidepath. Use the same plan format." --max-prompts 32
```

Profile-based quick start (recommended):

```powershell
python calibrate_trigger_profile.py
python auto_trigger_copilot_chat.py --prompt-file .\prompt.txt --max-prompts 128 --profile-file .\trigger_profile.json
```

Default behavior:

- `--max-prompts` defaults to `1`.
- Polling loop runs until it submits the requested count.
- Chat focus hotkey defaults to `ctrl+alt+i`.
- Early-stop keyword defaults to `HALT NOW`.

## Early Stop Keyword

If the monitored agent ends with a phrase like `HALT NOW`, the script can stop immediately.

Example:

```powershell
python auto_trigger_copilot_chat.py --prompt-file .\prompt.txt --max-prompts 256 --halt-keyword "HALT NOW"
```

If you do not want this behavior:

```powershell
python auto_trigger_copilot_chat.py --prompt-file .\prompt.txt --disable-halt-keyword-scan
```

## Recommended Reliable Mode (Template + Optional Click Point)

UI labels can vary by version. For best reliability:

1. Capture a small screenshot of the Copilot stop button and save it as `stop_button.png`.
2. Optionally identify chat input click coordinates if hotkey focus is inconsistent.
3. Use multi-template matching and scale sweep when you switch themes, zoom levels, or displays.

Example:

```powershell
python auto_trigger_copilot_chat.py `
  --prompt-file .\prompt.txt `
  --max-prompts 128 `
  --stop-template .\stop_button.png `
  --template-confidence 0.90 `
  --template-scales 0.85,0.92,1.0,1.08,1.15 `
  --input-click-x 1450 `
  --input-click-y 990
```

Multi-template example (recommended for theme/display variance):

```powershell
python auto_trigger_copilot_chat.py `
  --prompt-file .\prompt.txt `
  --max-prompts 256 `
  --stop-template .\templates\stop_dark.png `
  --stop-template .\templates\stop_light.png `
  --stop-template-glob .\templates\stop_scale_*.png `
  --template-scales 0.85,0.92,1.0,1.08,1.15
```

## Calibration Helper Script

Use the helper to capture both:

1. A stop-button template image.
2. Chat input click coordinates in absolute and window-relative ratio form.

Run:

```powershell
python calibrate_trigger_profile.py
```

Then run the monitor with the generated profile:

```powershell
python auto_trigger_copilot_chat.py --prompt-file .\prompt.txt --max-prompts 128 --profile-file .\trigger_profile.json
```

You can still override profile values with explicit CLI flags.

## Useful Flags

- `--prompt "..."`: Prompt text inline.
- `--prompt-file path.txt`: Prompt from file.
- `--max-prompts N`: Number of submissions (`1-512`).
- `--poll-seconds 1.0`: Monitor interval.
- `--submit-cooldown-seconds 1.5`: Delay after sending each prompt.
- `--chat-focus-hotkey ctrl+alt+i`: Shortcut used to focus chat input.
- `--stop-template stop_button.png`: Enable image matching for stop button.
- `--stop-template path.png`: Add one stop-button template (repeat flag to add many).
- `--stop-template-glob .\templates\stop_*.png`: Load many templates via glob.
- `--template-scales 0.85,0.92,1.0,1.08,1.15`: Scale sweep used per template.
- `--disable-uia-scan`: Skip UI Automation button-name detection.
- `--halt-keyword "HALT NOW"`: End monitor early when this text appears in chat output.
- `--disable-halt-keyword-scan`: Disable early-stop keyword detection.
- `--input-click-x/--input-click-y`: Click input box before paste/send.
- `--input-click-x-ratio/--input-click-y-ratio`: Window-relative click point in `[0.0, 1.0]`.
- `--profile-file trigger_profile.json`: Load calibration defaults from helper script.
- `--dry-run`: Logs actions without sending prompt text.

## Resolution-Agnostic Considerations

- Prefer `--input-click-x-ratio` and `--input-click-y-ratio` (or profile file values) over absolute pixels. Ratios track the VS Code window size and are more portable across resolutions.
- Keep VS Code layout stable (chat panel placement, sidebars, zoom level) because ratio clicks assume similar UI structure.
- Template matching is the least resolution-agnostic part. If monitor scale, theme, or Copilot icon style changes, recapture the stop template.
- Multi-template + scale sweep reduces recalibration frequency but cannot be fully resolution-agnostic if UI rendering changes drastically.
- If you move between monitors with very different DPI scaling, recalibrate once per setup for best reliability.
- UI Automation text detection is generally more robust than image matching and should stay enabled unless it causes false positives in your environment.

## Safety Notes

- Keep `pyautogui` fail-safe enabled (default in script): move mouse to top-left corner to interrupt quickly.
- Run with `--dry-run` first to verify state detection before live sending.
- Keep VS Code visible on the target display while running.

## Known Limitations

- Copilot UI/accessibility names can change by VS Code or extension version.
- Template matching is sensitive to scaling/theme changes; recapture template if detection drifts.
- Multi-monitor and DPI scaling can impact coordinate-based clicking.
