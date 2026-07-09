#!/usr/bin/env python3
"""
Continuously monitor VS Code Copilot Chat and auto-send prompts when chat is idle.

How it works:
1) Detect a VS Code window.
2) Determine chat state:
   - Active: stop button is visible.
   - Idle: stop button is not visible.
3) When idle, focus chat input, paste prompt, press Enter.
4) Repeat until the configured count is reached.

Notes:
- UI detection can vary across VS Code and Copilot builds. This script supports:
  a) UI Automation button-name scanning (best effort), and/or
  b) image-template matching for the stop button (recommended for reliability).
"""

from __future__ import annotations

import argparse
import re
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


# Lazy imports for runtime dependency messages
try:
    import pyautogui
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency: pyautogui. Install from requirements.txt."
    ) from exc

try:
    import pygetwindow as gw
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency: pygetwindow. Install from requirements.txt."
    ) from exc

try:
    import pyperclip
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency: pyperclip. Install from requirements.txt."
    ) from exc

# Optional dependency; script works without it if template matching is provided.
try:
    from pywinauto import Desktop
except Exception:  # pragma: no cover
    Desktop = None


DEFAULT_SHORTCUT = "ctrl+alt+i"
ACTIVE_NAME_PATTERNS = (
    re.compile(r"\bstop\b", re.IGNORECASE),
    re.compile(r"\binterrupt\b", re.IGNORECASE),
    re.compile(r"\bcancel\b", re.IGNORECASE),
)


@dataclass
class Config:
    prompt: str
    max_prompts: int = 1
    poll_seconds: float = 1.0
    submit_cooldown_seconds: float = 1.5
    vs_title_regex: str = r".*Visual Studio Code.*"
    chat_focus_hotkey: str = DEFAULT_SHORTCUT
    prompt_file: Optional[Path] = None
    stop_template: Optional[Path] = None
    template_confidence: float = 0.9
    use_uia_scan: bool = True
    input_click_x: Optional[int] = None
    input_click_y: Optional[int] = None
    dry_run: bool = False


class PromptMonitor:
    def __init__(self, config: Config):
        self.config = config
        self._stop_requested = False
        self._submitted = 0

    def request_stop(self, *_args) -> None:
        self._stop_requested = True

    def run(self) -> int:
        self._print_header()
        while not self._stop_requested and self._submitted < self.config.max_prompts:
            window = self._find_vscode_window()
            if not window:
                self._log("No matching VS Code window found. Retrying...")
                time.sleep(self.config.poll_seconds)
                continue

            is_active = self._is_chat_active(window)
            if is_active:
                self._log("Chat active (stop button detected). Waiting...")
                time.sleep(self.config.poll_seconds)
                continue

            self._log("Chat idle (stop button not detected). Submitting prompt...")
            ok = self._submit_prompt(window)
            if ok:
                self._submitted += 1
                self._log(
                    f"Submitted {self._submitted}/{self.config.max_prompts}."
                )
            else:
                self._log("Submit attempt failed. Retrying...")

            time.sleep(self.config.submit_cooldown_seconds)

        self._log("Finished.")
        return 0

    def _find_vscode_window(self):
        pattern = re.compile(self.config.vs_title_regex, re.IGNORECASE)
        try:
            candidates = [w for w in gw.getAllWindows() if w.title and pattern.match(w.title)]
        except Exception:
            return None

        if not candidates:
            return None

        # Prefer currently active window if it matches.
        try:
            active = gw.getActiveWindow()
            if active and active.title and pattern.match(active.title):
                return active
        except Exception:
            pass

        # Otherwise choose the largest visible match.
        visible = [w for w in candidates if w.width > 200 and w.height > 200]
        if visible:
            visible.sort(key=lambda w: w.width * w.height, reverse=True)
            return visible[0]
        return candidates[0]

    def _window_region(self, window) -> Tuple[int, int, int, int]:
        left = max(int(window.left), 0)
        top = max(int(window.top), 0)
        width = max(int(window.width), 1)
        height = max(int(window.height), 1)
        return left, top, width, height

    def _is_chat_active(self, window) -> bool:
        # First try UI automation (if available), then image matching fallback.
        if self.config.use_uia_scan and Desktop is not None:
            try:
                if self._uia_detect_stop_button(window):
                    return True
            except Exception:
                pass

        if self.config.stop_template:
            try:
                if self._template_detect_stop_button(window):
                    return True
            except Exception:
                pass

        return False

    def _uia_detect_stop_button(self, window) -> bool:
        app = Desktop(backend="uia")
        target = app.window(title_re=self.config.vs_title_regex)
        if not target.exists(timeout=0.5):
            return False

        buttons = target.descendants(control_type="Button")
        for btn in buttons:
            try:
                name = (btn.window_text() or "").strip()
            except Exception:
                continue
            if not name:
                continue
            if any(p.search(name) for p in ACTIVE_NAME_PATTERNS):
                return True
        return False

    def _template_detect_stop_button(self, window) -> bool:
        if not self.config.stop_template:
            return False
        region = self._window_region(window)
        match = pyautogui.locateOnScreen(
            str(self.config.stop_template),
            region=region,
            confidence=self.config.template_confidence,
            grayscale=True,
        )
        return match is not None

    def _submit_prompt(self, window) -> bool:
        try:
            window.activate()
        except Exception:
            pass

        time.sleep(0.15)

        if self.config.input_click_x is not None and self.config.input_click_y is not None:
            pyautogui.click(self.config.input_click_x, self.config.input_click_y)
            time.sleep(0.1)
        else:
            # Attempt to focus chat via keyboard shortcut.
            keys = [k.strip() for k in self.config.chat_focus_hotkey.split("+") if k.strip()]
            if keys:
                pyautogui.hotkey(*keys)
                time.sleep(0.15)

        if self.config.dry_run:
            return True

        pyperclip.copy(self.config.prompt)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.05)
        pyautogui.press("enter")
        return True

    @staticmethod
    def _log(message: str) -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] {message}", flush=True)

    def _print_header(self) -> None:
        self._log("Copilot Chat auto-trigger started.")
        self._log(
            f"Configured submissions: {self.config.max_prompts} (limit: 512)."
        )
        self._log("Press Ctrl+C to stop.")


def parse_args(argv: list[str]) -> Config:
    parser = argparse.ArgumentParser(
        description="Auto-submit prompt(s) to VS Code Copilot Chat when chat is idle."
    )
    parser.add_argument(
        "--prompt",
        default="",
        help="Prompt text to submit. If omitted, use --prompt-file.",
    )
    parser.add_argument(
        "--prompt-file",
        type=Path,
        help="Path to a UTF-8 text file containing prompt text.",
    )
    parser.add_argument(
        "--max-prompts",
        type=int,
        default=1,
        help="Number of prompt submissions to perform (1-512).",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=1.0,
        help="Polling interval while monitoring chat state.",
    )
    parser.add_argument(
        "--submit-cooldown-seconds",
        type=float,
        default=1.5,
        help="Delay after each submit before next state check.",
    )
    parser.add_argument(
        "--vs-title-regex",
        default=r".*Visual Studio Code.*",
        help="Regex used to find the VS Code window title.",
    )
    parser.add_argument(
        "--chat-focus-hotkey",
        default=DEFAULT_SHORTCUT,
        help=(
            "Hotkey to focus Copilot Chat input, e.g. ctrl+alt+i. "
            "Ignored if --input-click-x/y are provided."
        ),
    )
    parser.add_argument(
        "--stop-template",
        type=Path,
        help="Image file path for stop button template matching (png recommended).",
    )
    parser.add_argument(
        "--template-confidence",
        type=float,
        default=0.9,
        help="Template match confidence in [0.1, 1.0].",
    )
    parser.add_argument(
        "--disable-uia-scan",
        action="store_true",
        help="Disable UI Automation scan for stop button names.",
    )
    parser.add_argument(
        "--input-click-x",
        type=int,
        help="Absolute screen X coordinate to click before paste/enter.",
    )
    parser.add_argument(
        "--input-click-y",
        type=int,
        help="Absolute screen Y coordinate to click before paste/enter.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not paste/send; only log when a submit would happen.",
    )

    args = parser.parse_args(argv)

    if not (1 <= args.max_prompts <= 512):
        parser.error("--max-prompts must be between 1 and 512.")

    if args.template_confidence < 0.1 or args.template_confidence > 1.0:
        parser.error("--template-confidence must be between 0.1 and 1.0.")

    prompt_text = args.prompt
    prompt_file = args.prompt_file

    if prompt_file:
        if not prompt_file.exists():
            parser.error(f"Prompt file does not exist: {prompt_file}")
        prompt_text = prompt_file.read_text(encoding="utf-8").strip()

    if not prompt_text.strip():
        parser.error("Provide --prompt or --prompt-file with non-empty content.")

    stop_template = args.stop_template
    if stop_template and not stop_template.exists():
        parser.error(f"Stop template file does not exist: {stop_template}")

    if (args.input_click_x is None) != (args.input_click_y is None):
        parser.error("Provide both --input-click-x and --input-click-y, or neither.")

    return Config(
        prompt=prompt_text,
        max_prompts=args.max_prompts,
        poll_seconds=args.poll_seconds,
        submit_cooldown_seconds=args.submit_cooldown_seconds,
        vs_title_regex=args.vs_title_regex,
        chat_focus_hotkey=args.chat_focus_hotkey,
        prompt_file=prompt_file,
        stop_template=stop_template,
        template_confidence=args.template_confidence,
        use_uia_scan=not args.disable_uia_scan,
        input_click_x=args.input_click_x,
        input_click_y=args.input_click_y,
        dry_run=args.dry_run,
    )


def main(argv: list[str]) -> int:
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05

    config = parse_args(argv)
    monitor = PromptMonitor(config)

    signal.signal(signal.SIGINT, monitor.request_stop)
    signal.signal(signal.SIGTERM, monitor.request_stop)

    return monitor.run()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
