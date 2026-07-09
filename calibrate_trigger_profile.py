#!/usr/bin/env python3
"""
Calibration helper for auto_trigger_copilot_chat.py.

This script captures:
- A stop-button image template cropped from the VS Code window.
- A chat-input click point in both absolute and window-relative form.
- A JSON profile that the main script can load with --profile-file.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Tuple

try:
    import pyautogui
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: pyautogui") from exc

try:
    import pygetwindow as gw
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: pygetwindow") from exc


def find_vscode_window(vs_title_regex: str):
    pattern = re.compile(vs_title_regex, re.IGNORECASE)
    windows = [w for w in gw.getAllWindows() if w.title and pattern.match(w.title)]
    if not windows:
        return None

    active = gw.getActiveWindow()
    if active and active.title and pattern.match(active.title):
        return active

    windows.sort(key=lambda w: w.width * w.height, reverse=True)
    return windows[0]


def window_region(window) -> Tuple[int, int, int, int]:
    left = max(int(window.left), 0)
    top = max(int(window.top), 0)
    width = max(int(window.width), 1)
    height = max(int(window.height), 1)
    return left, top, width, height


def countdown(seconds: int) -> None:
    for i in range(seconds, 0, -1):
        print(f"Capturing in {i}...", flush=True)
        time.sleep(1.0)


def ask_for_point(message: str, delay_seconds: int) -> Tuple[int, int]:
    print(message, flush=True)
    input("Press Enter when ready. ")
    countdown(delay_seconds)
    pos = pyautogui.position()
    print(f"Captured point: ({pos.x}, {pos.y})", flush=True)
    return int(pos.x), int(pos.y)


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Calibrate stop template and input coordinates for Copilot trigger script."
    )
    parser.add_argument(
        "--vs-title-regex",
        default=r".*Visual Studio Code.*",
        help="Regex used to locate the VS Code window.",
    )
    parser.add_argument(
        "--output-profile",
        type=Path,
        default=Path("trigger_profile.json"),
        help="Output JSON profile path.",
    )
    parser.add_argument(
        "--output-template",
        type=Path,
        default=Path("stop_button_template.png"),
        help="Output PNG template path.",
    )
    parser.add_argument(
        "--template-size",
        type=int,
        default=48,
        help="Square template size in pixels.",
    )
    parser.add_argument(
        "--capture-delay-seconds",
        type=int,
        default=3,
        help="Countdown delay before each point capture.",
    )
    parser.add_argument(
        "--template-confidence",
        type=float,
        default=0.9,
        help="Default confidence to store in profile.",
    )
    parser.add_argument(
        "--template-scales",
        default="0.85,0.92,1.0,1.08,1.15",
        help="Comma-separated template scales to store in profile.",
    )
    parser.add_argument(
        "--halt-keyword",
        default="HALT NOW",
        help="Halt keyword to store in profile.",
    )
    args = parser.parse_args()

    if args.template_size < 16:
        parser.error("--template-size must be >= 16")
    if args.capture_delay_seconds < 1:
        parser.error("--capture-delay-seconds must be >= 1")
    if not (0.1 <= args.template_confidence <= 1.0):
        parser.error("--template-confidence must be between 0.1 and 1.0")

    try:
        template_scales = [float(x.strip()) for x in args.template_scales.split(",") if x.strip()]
    except ValueError:
        parser.error("--template-scales must contain numeric values")
    if not template_scales or any(x <= 0.0 for x in template_scales):
        parser.error("--template-scales values must all be > 0")

    window = find_vscode_window(args.vs_title_regex)
    if not window:
        raise SystemExit("No VS Code window matched --vs-title-regex")

    try:
        window.activate()
    except Exception:
        pass

    left, top, width, height = window_region(window)
    print(f"Using VS Code window: left={left}, top={top}, width={width}, height={height}")

    stop_x, stop_y = ask_for_point(
        "Hover your mouse over the center of the Copilot stop button.",
        args.capture_delay_seconds,
    )

    shot = pyautogui.screenshot(region=(left, top, width, height))
    rel_x = stop_x - left
    rel_y = stop_y - top
    half = args.template_size // 2

    crop_left = clamp(rel_x - half, 0, width - 1)
    crop_top = clamp(rel_y - half, 0, height - 1)
    crop_right = clamp(crop_left + args.template_size, 1, width)
    crop_bottom = clamp(crop_top + args.template_size, 1, height)

    template = shot.crop((crop_left, crop_top, crop_right, crop_bottom))

    template_path = args.output_template
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template.save(template_path)
    print(f"Saved stop-button template: {template_path}")

    input_x, input_y = ask_for_point(
        "Hover your mouse over the center of the chat input area.",
        args.capture_delay_seconds,
    )

    input_x_ratio = (input_x - left) / float(width)
    input_y_ratio = (input_y - top) / float(height)
    input_x_ratio = max(0.0, min(1.0, input_x_ratio))
    input_y_ratio = max(0.0, min(1.0, input_y_ratio))

    profile = {
        "vs_title_regex": args.vs_title_regex,
        "stop_template": str(template_path),
        "stop_templates": [str(template_path)],
        "template_confidence": args.template_confidence,
        "template_scales": template_scales,
        "input_click_x": int(input_x),
        "input_click_y": int(input_y),
        "input_click_x_ratio": round(input_x_ratio, 6),
        "input_click_y_ratio": round(input_y_ratio, 6),
        "halt_keyword": args.halt_keyword,
        "chat_focus_hotkey": "ctrl+alt+i",
        "notes": [
            "Use ratio coordinates for better resolution/DPI portability.",
            "Template matching may need recapture when theme/zoom/scaling changes.",
        ],
    }

    profile_path = args.output_profile
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    print(f"Saved profile JSON: {profile_path}")

    print("\nSuggested run command:")
    print(
        "python auto_trigger_copilot_chat.py "
        f"--prompt-file .\\prompt.txt --max-prompts 128 --profile-file {profile_path}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
