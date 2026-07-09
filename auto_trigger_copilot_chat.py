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
import glob
import json
import re
import signal
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Runtime dependencies are loaded on-demand to keep import-time behavior CI-safe.
pyautogui: Any = None
gw: Any = None
pyperclip: Any = None
Desktop: Any = None
cv2: Any = None
np: Any = None


DEFAULT_SHORTCUT = "ctrl+alt+i"
VERSION = "0.0.1"
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
    prompt_file: Path | None = None
    stop_templates: tuple[Path, ...] = ()
    template_confidence: float = 0.9
    template_scales: tuple[float, ...] = (0.85, 0.92, 1.0, 1.08, 1.15)
    use_uia_scan: bool = True
    halt_keyword: str = "HALT NOW"
    disable_halt_keyword_scan: bool = False
    input_click_x: int | None = None
    input_click_y: int | None = None
    input_click_x_ratio: float | None = None
    input_click_y_ratio: float | None = None
    submit_enter_delay_seconds: float = 0.15
    dry_run: bool = False


class PromptMonitor:
    def __init__(self, config: Config):
        self.config = config
        self._stop_requested = False
        self._submitted = 0

    def request_stop(self, *_args: object) -> None:
        self._stop_requested = True

    def run(self) -> int:
        self._print_header()
        while not self._stop_requested and self._submitted < self.config.max_prompts:
            window = self._find_vscode_window()
            if not window:
                self._log("No matching VS Code window found. Retrying...")
                time.sleep(self.config.poll_seconds)
                continue

            if self._should_halt(window):
                self._log("Halt keyword detected in chat output. Ending monitor early.")
                break

            is_active = self._is_chat_active(window)
            if is_active:
                self._log("Chat active (stop button detected). Waiting...")
                time.sleep(self.config.poll_seconds)
                continue

            self._log("Chat idle (stop button not detected). Submitting prompt...")
            ok = self._submit_prompt(window)
            if ok:
                self._submitted += 1
                self._log(f"Submitted {self._submitted}/{self.config.max_prompts}.")
            else:
                self._log("Submit attempt failed. Retrying...")

            time.sleep(self.config.submit_cooldown_seconds)

        self._log("Finished.")
        return 0

    def _should_halt(self, window: Any) -> bool:
        # Ignore halt-keyword scan until at least one prompt has been submitted.
        # This avoids immediate exits when the keyword is visible in prompt sources.
        if self._submitted == 0:
            return False
        if self.config.disable_halt_keyword_scan:
            return False
        if not self.config.halt_keyword.strip():
            return False
        if Desktop is None:
            return False
        try:
            return self._uia_detect_halt_keyword(window)
        except Exception:
            return False

    def _find_vscode_window(self) -> Any | None:
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

    def _window_region(self, window: Any) -> tuple[int, int, int, int]:
        left = max(int(window.left), 0)
        top = max(int(window.top), 0)
        width = max(int(window.width), 1)
        height = max(int(window.height), 1)
        return left, top, width, height

    def _is_chat_active(self, window: Any) -> bool:
        # First try UI automation (if available), then image matching fallback.
        if self.config.use_uia_scan and Desktop is not None:
            try:
                if self._uia_detect_stop_button(window):
                    return True
            except Exception:
                pass

        if self.config.stop_templates:
            try:
                if self._template_detect_stop_button(window):
                    return True
            except Exception:
                pass

        return False

    def _uia_detect_stop_button(self, window: Any) -> bool:
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

    def _uia_detect_halt_keyword(self, window: Any) -> bool:
        app = Desktop(backend="uia")
        target = app.window(title_re=self.config.vs_title_regex)
        if not target.exists(timeout=0.5):
            return False

        keyword = self.config.halt_keyword.strip().lower()
        controls = target.descendants()
        for ctrl in controls:
            try:
                text = (ctrl.window_text() or "").strip()
            except Exception:
                continue
            if text and keyword in text.lower():
                return True
        return False

    def _template_detect_stop_button(self, window: Any) -> bool:
        if not self.config.stop_templates:
            return False
        region = self._window_region(window)
        if cv2 is not None and np is not None:
            return self._template_detect_with_cv2(region)
        return self._template_detect_with_pyautogui(region)

    def _template_detect_with_pyautogui(self, region: tuple[int, int, int, int]) -> bool:
        # Fallback matcher when OpenCV is unavailable: no scale sweep.
        for template_path in self.config.stop_templates:
            match = pyautogui.locateOnScreen(
                str(template_path),
                region=region,
                confidence=self.config.template_confidence,
                grayscale=True,
            )
            if match is not None:
                return True
        return False

    def _template_detect_with_cv2(self, region: tuple[int, int, int, int]) -> bool:
        shot = pyautogui.screenshot(region=region).convert("L")
        screen_gray = np.array(shot)
        screen_h, screen_w = screen_gray.shape[:2]

        for template_path in self.config.stop_templates:
            template = cv2.imread(str(template_path), cv2.IMREAD_GRAYSCALE)
            if template is None:
                continue

            base_h, base_w = template.shape[:2]
            for scale in self.config.template_scales:
                scaled_w = max(1, int(round(base_w * scale)))
                scaled_h = max(1, int(round(base_h * scale)))

                if scaled_w < 4 or scaled_h < 4:
                    continue
                if scaled_w > screen_w or scaled_h > screen_h:
                    continue

                if scale == 1.0:
                    candidate = template
                else:
                    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
                    candidate = cv2.resize(template, (scaled_w, scaled_h), interpolation=interp)

                result = cv2.matchTemplate(screen_gray, candidate, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, _ = cv2.minMaxLoc(result)
                if max_val >= self.config.template_confidence:
                    return True

        return False

    def _submit_prompt(self, window: Any) -> bool:
        with suppress(Exception):
            window.activate()

        time.sleep(0.15)

        click_xy = self._resolve_input_click(window)
        if click_xy is not None:
            pyautogui.click(click_xy[0], click_xy[1])
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
        time.sleep(self.config.submit_enter_delay_seconds)
        pyautogui.press("enter")

        # Some chat surfaces occasionally swallow the first Enter after paste.
        # If chat still appears idle shortly after submit, send one fallback Enter.
        with suppress(Exception):
            time.sleep(0.2)
            if not self._is_chat_active(window):
                pyautogui.press("enter")
        return True

    def _resolve_input_click(self, window: Any) -> tuple[int, int] | None:
        if self.config.input_click_x is not None and self.config.input_click_y is not None:
            return int(self.config.input_click_x), int(self.config.input_click_y)

        if self.config.input_click_x_ratio is not None and self.config.input_click_y_ratio is not None:
            left, top, width, height = self._window_region(window)
            abs_x = left + int(round(width * self.config.input_click_x_ratio))
            abs_y = top + int(round(height * self.config.input_click_y_ratio))
            return abs_x, abs_y

        return None

    @staticmethod
    def _log(message: str) -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] {message}", flush=True)

    def _print_header(self) -> None:
        self._log("Copilot Chat auto-trigger started.")
        self._log(f"Configured submissions: {self.config.max_prompts} (limit: 512).")
        if self.config.stop_templates:
            self._log(
                f"Template variants: {len(self.config.stop_templates)}; "
                f"scale variants: {len(self.config.template_scales)}."
            )
        if not self.config.disable_halt_keyword_scan:
            self._log(f"Early-stop keyword: {self.config.halt_keyword!r}.")
        self._log("Press Ctrl+C to stop.")


def _parse_scales_csv(scales_csv: str, parser: argparse.ArgumentParser) -> tuple[float, ...]:
    raw_parts = [p.strip() for p in scales_csv.split(",") if p.strip()]
    if not raw_parts:
        parser.error("--template-scales must include at least one numeric value.")

    parsed: list[float] = []
    for part in raw_parts:
        try:
            value = float(part)
        except ValueError:
            parser.error(f"Invalid scale value in --template-scales: {part}")
        if value <= 0.0:
            parser.error("Template scale values must be > 0.")
        parsed.append(value)

    # Preserve order while removing duplicates from minor float formatting differences.
    unique = list(dict.fromkeys(round(v, 6) for v in parsed))
    return tuple(unique)


def _resolve_profile_path(raw_path: str, profile_file: Path | None) -> Path:
    candidate = Path(raw_path)
    if not candidate.is_absolute() and profile_file:
        candidate = profile_file.parent / candidate
    return candidate


def _extract_prompt_segment(
    prompt_text: str,
    start_marker: str | None,
    end_marker: str | None,
    parser: argparse.ArgumentParser,
) -> str:
    if not start_marker and not end_marker:
        return prompt_text.strip()

    start_index = 0
    if start_marker:
        start_index = prompt_text.find(start_marker)
        if start_index < 0:
            parser.error("--prompt-start-marker was not found in --prompt-file content.")

    end_index = len(prompt_text)
    if end_marker:
        search_from = start_index
        end_pos = prompt_text.find(end_marker, search_from)
        if end_pos < 0:
            parser.error("--prompt-end-marker was not found in --prompt-file content.")
        end_index = end_pos + len(end_marker)

    segment = prompt_text[start_index:end_index].strip()
    if not segment:
        parser.error("Extracted prompt segment is empty after applying prompt markers.")
    return segment


def _ensure_runtime_dependencies() -> None:
    global pyautogui, gw, pyperclip, Desktop, cv2, np

    if pyautogui is None:
        try:
            import pyautogui as _pyautogui  # type: ignore[import-untyped]
        except Exception as exc:  # pragma: no cover
            raise SystemExit("Missing dependency: pyautogui. Install runtime dependencies first.") from exc
        pyautogui = _pyautogui

    if gw is None:
        try:
            import pygetwindow as _gw  # type: ignore[import-untyped]
        except Exception as exc:  # pragma: no cover
            raise SystemExit("Missing dependency: pygetwindow. Install runtime dependencies first.") from exc
        gw = _gw

    if pyperclip is None:
        try:
            import pyperclip as _pyperclip  # type: ignore[import-untyped]
        except Exception as exc:  # pragma: no cover
            raise SystemExit("Missing dependency: pyperclip. Install runtime dependencies first.") from exc
        pyperclip = _pyperclip

    if Desktop is None:
        try:
            from pywinauto import Desktop as _Desktop  # type: ignore[import-untyped]

            Desktop = _Desktop
        except Exception:  # pragma: no cover
            Desktop = None

    if cv2 is None:
        try:
            import cv2 as _cv2

            cv2 = _cv2
        except Exception:  # pragma: no cover
            cv2 = None

    if np is None:
        try:
            import numpy as _np

            np = _np
        except Exception:  # pragma: no cover
            np = None


def parse_args(argv: list[str]) -> Config:
    parser = argparse.ArgumentParser(description="Auto-submit prompt(s) to VS Code Copilot Chat when chat is idle.")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {VERSION}",
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
        "--prompt-start-marker",
        default="",
        help=(
            "Optional marker string for extracting a prompt subsection from --prompt-file. "
            "Extraction starts at the first marker occurrence (inclusive)."
        ),
    )
    parser.add_argument(
        "--prompt-end-marker",
        default="",
        help=(
            "Optional marker string for extracting a prompt subsection from --prompt-file. "
            "Extraction ends at the first marker occurrence after start (inclusive)."
        ),
    )
    parser.add_argument(
        "--profile-file",
        type=Path,
        help="Optional JSON profile generated by calibrate_trigger_profile.py.",
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
        "--submit-enter-delay-seconds",
        type=float,
        default=0.15,
        help="Delay between Ctrl+V paste and Enter submit.",
    )
    parser.add_argument(
        "--vs-title-regex",
        default=r".*Visual Studio Code.*",
        help="Regex used to find the VS Code window title.",
    )
    parser.add_argument(
        "--chat-focus-hotkey",
        default=DEFAULT_SHORTCUT,
        help=("Hotkey to focus Copilot Chat input, e.g. ctrl+alt+i. " "Ignored if --input-click-x/y are provided."),
    )
    parser.add_argument(
        "--stop-template",
        type=Path,
        action="append",
        help="Image file path for stop button template matching (png recommended).",
    )
    parser.add_argument(
        "--stop-template-glob",
        action="append",
        help=(
            "Glob pattern for stop templates (example: .\\templates\\stop_*.png). " "Can be provided multiple times."
        ),
    )
    parser.add_argument(
        "--template-confidence",
        type=float,
        default=0.9,
        help="Template match confidence in [0.1, 1.0].",
    )
    parser.add_argument(
        "--template-scales",
        default="0.85,0.92,1.0,1.08,1.15",
        help=("Comma-separated scale sweep for template matching " "(example: 0.85,0.92,1.0,1.08,1.15)."),
    )
    parser.add_argument(
        "--disable-uia-scan",
        action="store_true",
        help="Disable UI Automation scan for stop button names.",
    )
    parser.add_argument(
        "--halt-keyword",
        default="HALT NOW",
        help=(
            "Stop monitoring early if this text appears in VS Code chat output. "
            "Set a unique marker phrase your agent emits when done."
        ),
    )
    parser.add_argument(
        "--disable-halt-keyword-scan",
        action="store_true",
        help="Disable early-stop keyword detection.",
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
        "--input-click-x-ratio",
        type=float,
        help=("Window-relative X coordinate in [0.0, 1.0]. " "More resolution-agnostic than absolute X."),
    )
    parser.add_argument(
        "--input-click-y-ratio",
        type=float,
        help=("Window-relative Y coordinate in [0.0, 1.0]. " "More resolution-agnostic than absolute Y."),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not paste/send; only log when a submit would happen.",
    )

    args = parser.parse_args(argv)

    profile: dict[str, Any] = {}
    if args.profile_file:
        if not args.profile_file.exists():
            parser.error(f"Profile file does not exist: {args.profile_file}")
        try:
            profile = json.loads(args.profile_file.read_text(encoding="utf-8"))
        except Exception as exc:
            parser.error(f"Failed to parse profile JSON: {exc}")

    if not (1 <= args.max_prompts <= 512):
        parser.error("--max-prompts must be between 1 and 512.")

    if args.template_confidence < 0.1 or args.template_confidence > 1.0:
        parser.error("--template-confidence must be between 0.1 and 1.0.")

    prompt_text = args.prompt
    prompt_file = args.prompt_file
    prompt_start_marker = (args.prompt_start_marker or "").strip()
    prompt_end_marker = (args.prompt_end_marker or "").strip()

    if (prompt_start_marker or prompt_end_marker) and not prompt_file:
        parser.error("--prompt-start-marker/--prompt-end-marker require --prompt-file.")

    if prompt_file:
        if not prompt_file.exists():
            parser.error(f"Prompt file does not exist: {prompt_file}")
        prompt_text = prompt_file.read_text(encoding="utf-8")
        prompt_text = _extract_prompt_segment(prompt_text, prompt_start_marker, prompt_end_marker, parser)
    else:
        prompt_text = prompt_text.strip()

    if not prompt_text.strip():
        parser.error("Provide --prompt or --prompt-file with non-empty content.")

    stop_templates: list[Path] = []
    if args.stop_template:
        stop_templates.extend(args.stop_template)

    profile_templates = profile.get("stop_templates")
    if not stop_templates and isinstance(profile_templates, list):
        for item in profile_templates:
            if isinstance(item, str):
                stop_templates.append(_resolve_profile_path(item, args.profile_file))

    if not stop_templates and isinstance(profile.get("stop_template"), str):
        stop_templates.append(_resolve_profile_path(str(profile["stop_template"]), args.profile_file))

    if args.stop_template_glob:
        for pattern in args.stop_template_glob:
            for matched in sorted(glob.glob(pattern)):
                stop_templates.append(Path(matched))

    stop_templates_dedup = list(dict.fromkeys(str(p) for p in stop_templates))
    stop_templates = [Path(p) for p in stop_templates_dedup]

    for template in stop_templates:
        if not template.exists():
            parser.error(f"Stop template file does not exist: {template}")

    template_scales = _parse_scales_csv(args.template_scales, parser)
    if args.template_scales == "0.85,0.92,1.0,1.08,1.15" and isinstance(profile.get("template_scales"), list):
        profile_scale_items = ",".join(str(s) for s in profile["template_scales"])
        template_scales = _parse_scales_csv(profile_scale_items, parser)

    input_click_x = args.input_click_x
    input_click_y = args.input_click_y
    input_click_x_ratio = args.input_click_x_ratio
    input_click_y_ratio = args.input_click_y_ratio

    if input_click_x is None and isinstance(profile.get("input_click_x"), int):
        input_click_x = int(profile["input_click_x"])
    if input_click_y is None and isinstance(profile.get("input_click_y"), int):
        input_click_y = int(profile["input_click_y"])
    if input_click_x_ratio is None and isinstance(profile.get("input_click_x_ratio"), int | float):
        input_click_x_ratio = float(profile["input_click_x_ratio"])
    if input_click_y_ratio is None and isinstance(profile.get("input_click_y_ratio"), int | float):
        input_click_y_ratio = float(profile["input_click_y_ratio"])

    if (input_click_x is None) != (input_click_y is None):
        parser.error("Provide both --input-click-x and --input-click-y, or neither.")
    if (input_click_x_ratio is None) != (input_click_y_ratio is None):
        parser.error("Provide both --input-click-x-ratio and --input-click-y-ratio, or neither.")
    if input_click_x is not None and input_click_x_ratio is not None:
        parser.error("Use absolute input click coordinates OR ratio coordinates, not both.")
    if input_click_x_ratio is not None and not (0.0 <= input_click_x_ratio <= 1.0):
        parser.error("--input-click-x-ratio must be between 0.0 and 1.0.")
    if input_click_y_ratio is not None and not (0.0 <= input_click_y_ratio <= 1.0):
        parser.error("--input-click-y-ratio must be between 0.0 and 1.0.")

    vs_title_regex = args.vs_title_regex
    if vs_title_regex == r".*Visual Studio Code.*" and isinstance(profile.get("vs_title_regex"), str):
        vs_title_regex = str(profile["vs_title_regex"])

    chat_focus_hotkey = args.chat_focus_hotkey
    if chat_focus_hotkey == DEFAULT_SHORTCUT and isinstance(profile.get("chat_focus_hotkey"), str):
        chat_focus_hotkey = str(profile["chat_focus_hotkey"])

    template_confidence = args.template_confidence
    if template_confidence == 0.9 and isinstance(profile.get("template_confidence"), int | float):
        template_confidence = float(profile["template_confidence"])

    halt_keyword = args.halt_keyword
    if halt_keyword == "HALT NOW" and isinstance(profile.get("halt_keyword"), str):
        halt_keyword = str(profile["halt_keyword"])

    return Config(
        prompt=prompt_text,
        max_prompts=args.max_prompts,
        poll_seconds=args.poll_seconds,
        submit_cooldown_seconds=args.submit_cooldown_seconds,
        submit_enter_delay_seconds=args.submit_enter_delay_seconds,
        vs_title_regex=vs_title_regex,
        chat_focus_hotkey=chat_focus_hotkey,
        prompt_file=prompt_file,
        stop_templates=tuple(stop_templates),
        template_confidence=template_confidence,
        template_scales=template_scales,
        use_uia_scan=not args.disable_uia_scan,
        halt_keyword=halt_keyword,
        disable_halt_keyword_scan=args.disable_halt_keyword_scan,
        input_click_x=input_click_x,
        input_click_y=input_click_y,
        input_click_x_ratio=input_click_x_ratio,
        input_click_y_ratio=input_click_y_ratio,
        dry_run=args.dry_run,
    )


def main(argv: list[str]) -> int:
    _ensure_runtime_dependencies()

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05

    config = parse_args(argv)
    monitor = PromptMonitor(config)

    signal.signal(signal.SIGINT, monitor.request_stop)
    signal.signal(signal.SIGTERM, monitor.request_stop)

    return monitor.run()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
