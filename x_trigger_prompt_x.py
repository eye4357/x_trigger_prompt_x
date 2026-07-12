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
CHAT_INPUT_MARKER_TOKENS = ("chat", "copilot", "prompt", "message", "ask", "composer", "assistant")
DISALLOWED_INPUT_MARKER_TOKENS = (
    "terminal",
    "xterm",
    "pty",
    "powershell",
    "pwsh",
    "cmd",
    "bash",
    "zsh",
    "console",
    "debug console",
    "output",
)
ALLOWED_CHAT_INPUT_CONTROL_TYPES = (
    "Edit",
    "Document",
    "Pane",
    "Custom",
)


@dataclass
class Config:
    prompt: str
    max_prompts: int = 1
    poll_seconds: float = 1.0
    idle_stable_cycles: int = 2
    submit_cooldown_seconds: float = 1.5
    no_activity_backoff_seconds: float = 8.0
    single_flight_timeout_seconds: float = 45.0
    output_stable_cycles: int = 2
    post_submit_activity_wait_seconds: float = 2.5
    vs_title_regex: str = r".*Visual Studio Code.*"
    chat_focus_hotkey: str = DEFAULT_SHORTCUT
    reuse_chat_focus_hotkey: bool = False
    allow_unsafe_hotkey_focus: bool = False
    allow_verified_hotkey_fallback: bool = False
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
    default_safe_click_x_ratio: float = 0.82
    default_safe_click_y_ratio: float = 0.92
    hard_lock_vertical_offset_ratio: float = 0.08
    allow_force_submit_in_hard_lock_zone: bool = False
    log_centroid_debug: bool = False
    submit_enter_delay_seconds: float = 0.15
    dry_run: bool = False


class PromptMonitor:
    def __init__(self, config: Config):
        self.config = config
        self._stop_requested = False
        self._submitted = 0
        self._halt_keyword_baseline: int | None = None
        self._idle_streak = 0
        self._chat_focus_hotkey_used = False
        self._last_submit_saw_activity = False
        self._awaiting_post_submit_activity = False
        self._awaiting_post_submit_started_at = 0.0
        self._awaiting_post_submit_activity_seen = False
        self._awaiting_post_submit_timeout_logged = False
        self._last_completion_fingerprint: tuple[str, ...] | None = None
        self._completion_stable_streak = 0
        self._single_flight_activity_edges = 0
        self._single_flight_timeout_fallbacks = 0

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

            now = time.monotonic()
            active_source = self._chat_active_source(window)
            if active_source:
                self._idle_streak = 0
                if self._awaiting_post_submit_activity and not self._awaiting_post_submit_activity_seen:
                    self._single_flight_activity_edges += 1
                    self._awaiting_post_submit_activity_seen = True
                    self._awaiting_post_submit_timeout_logged = False
                    self._last_completion_fingerprint = None
                    self._completion_stable_streak = 0
                self._log(f"Chat active (stop button detected via {active_source}). Waiting...")
                time.sleep(self.config.poll_seconds)
                continue

            self._idle_streak += 1

            if self._awaiting_post_submit_activity and not self._awaiting_post_submit_activity_seen:
                elapsed = now - self._awaiting_post_submit_started_at
                if elapsed < self.config.single_flight_timeout_seconds:
                    self._log(
                        "Single-flight guard active: waiting for post-submit activity edge "
                        f"before next submit ({elapsed:.1f}/{self.config.single_flight_timeout_seconds:.1f}s)."
                    )
                    time.sleep(self.config.poll_seconds)
                    continue

                if not self._awaiting_post_submit_timeout_logged:
                    self._log(
                        "Single-flight activity edge timeout reached; still waiting for activity evidence "
                        "before next submit."
                    )
                    self._single_flight_timeout_fallbacks += 1
                    self._awaiting_post_submit_timeout_logged = True
                time.sleep(self.config.poll_seconds)
                continue

            if self._awaiting_post_submit_activity and self._awaiting_post_submit_activity_seen:
                if self._idle_streak < self.config.idle_stable_cycles:
                    self._log(
                        "Single-flight guard: waiting for stable idle after activity "
                        f"({self._idle_streak}/{self.config.idle_stable_cycles})..."
                    )
                    time.sleep(self.config.poll_seconds)
                    continue

                if self.config.dry_run:
                    self._awaiting_post_submit_activity = False
                    self._awaiting_post_submit_activity_seen = False
                    self._awaiting_post_submit_timeout_logged = False
                    self._awaiting_post_submit_started_at = 0.0
                    self._last_completion_fingerprint = None
                    self._completion_stable_streak = 0
                    self._idle_streak = 0
                    self._log("Single-flight transition complete (dry-run -> stable idle).")
                    time.sleep(self.config.poll_seconds)
                    continue

                fingerprint = self._chat_output_fingerprint(window)
                if fingerprint is None:
                    self._log("Single-flight guard: waiting for UIA chat output snapshot before next submit.")
                    time.sleep(self.config.poll_seconds)
                    continue

                if fingerprint != self._last_completion_fingerprint:
                    self._last_completion_fingerprint = fingerprint
                    self._completion_stable_streak = 1
                else:
                    self._completion_stable_streak += 1

                if self._completion_stable_streak < self.config.output_stable_cycles:
                    self._log(
                        "Single-flight guard: waiting for stable UIA output "
                        f"({self._completion_stable_streak}/{self.config.output_stable_cycles})..."
                    )
                    time.sleep(self.config.poll_seconds)
                    continue

                self._awaiting_post_submit_activity = False
                self._awaiting_post_submit_activity_seen = False
                self._awaiting_post_submit_timeout_logged = False
                self._awaiting_post_submit_started_at = 0.0
                self._last_completion_fingerprint = None
                self._completion_stable_streak = 0
                self._idle_streak = 0
                self._log("Single-flight transition complete (activity -> stable idle + stable UIA output).")
                time.sleep(self.config.poll_seconds)
                continue

            if self._idle_streak < self.config.idle_stable_cycles:
                self._log(
                    "Chat appears idle but waiting for stable idle cycles "
                    f"({self._idle_streak}/{self.config.idle_stable_cycles})..."
                )
                time.sleep(self.config.poll_seconds)
                continue

            self._log("Chat idle (stop button not detected). Submitting prompt...")
            try:
                ok = self._submit_prompt(window)
            except Exception as exc:
                if self._is_pyautogui_failsafe_exception(exc):
                    self._log(
                        "PyAutoGUI fail-safe triggered (mouse at screen corner). "
                        "Skipping this submit cycle. Move mouse away from corners and retry."
                    )
                    ok = False
                else:
                    raise
            if ok:
                self._submitted += 1
                self._idle_streak = 0
                self._awaiting_post_submit_activity = True
                self._awaiting_post_submit_started_at = time.monotonic()
                self._awaiting_post_submit_activity_seen = self.config.dry_run
                self._awaiting_post_submit_timeout_logged = False
                self._last_completion_fingerprint = None
                self._completion_stable_streak = 0
                self._log(f"Submitted {self._submitted}/{self.config.max_prompts}.")
            else:
                self._log("Submit attempt failed. Retrying...")

            sleep_seconds = self.config.submit_cooldown_seconds
            if not self._last_submit_saw_activity:
                sleep_seconds = max(sleep_seconds, self.config.no_activity_backoff_seconds)
                self._log(
                    "No post-submit activity detected; applying extended backoff "
                    f"({sleep_seconds:.1f}s) to avoid rapid re-submission."
                )

            time.sleep(sleep_seconds)

        self._log(
            "Single-flight summary: "
            f"activity_edges={self._single_flight_activity_edges}, "
            f"timeout_fallbacks={self._single_flight_timeout_fallbacks}."
        )
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
            occurrences = self._uia_count_halt_keyword_occurrences(window)
        except Exception:
            return False

        # If no baseline is available, establish one and continue.
        if self._halt_keyword_baseline is None:
            self._halt_keyword_baseline = occurrences
            return False

        return occurrences > self._halt_keyword_baseline

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
        return self._chat_active_source(window) is not None

    def _chat_active_source(self, window: Any) -> str | None:
        # When UIA is available and gives a clean negative, trust it over image matching.
        if self.config.use_uia_scan and Desktop is not None:
            try:
                if self._uia_detect_stop_button(window):
                    return "uia"
                return None
            except Exception:
                pass

        if self.config.stop_templates:
            try:
                if self._template_detect_stop_button(window):
                    return "template"
            except Exception:
                pass

        return None

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
            if any(p.search(name) for p in ACTIVE_NAME_PATTERNS) and self._uia_stop_button_candidate_is_active(btn):
                return True
        return False

    @staticmethod
    def _uia_stop_button_candidate_is_active(btn: Any) -> bool:
        try:
            is_visible = getattr(btn, "is_visible", None)
            if callable(is_visible) and not bool(is_visible()):
                return False
        except Exception:
            return False

        try:
            is_enabled = getattr(btn, "is_enabled", None)
            if callable(is_enabled) and not bool(is_enabled()):
                return False
        except Exception:
            return False

        try:
            rect = btn.rectangle()
        except Exception:
            return True

        rect_width = int(getattr(rect, "right", 0)) - int(getattr(rect, "left", 0))
        rect_height = int(getattr(rect, "bottom", 0)) - int(getattr(rect, "top", 0))
        return rect_width > 0 and rect_height > 0

    def _uia_count_halt_keyword_occurrences(self, window: Any) -> int:
        app = Desktop(backend="uia")
        target = app.window(title_re=self.config.vs_title_regex)
        if not target.exists(timeout=0.5):
            return 0

        keyword = self.config.halt_keyword.strip().lower()
        if not keyword:
            return 0

        occurrences = 0
        controls = target.descendants()
        for ctrl in controls:
            try:
                text = (ctrl.window_text() or "").strip()
            except Exception:
                continue
            if text:
                occurrences += text.lower().count(keyword)
        return occurrences

    def _chat_output_fingerprint(self, window: Any) -> tuple[str, ...] | None:
        if Desktop is None:
            return None

        app = Desktop(backend="uia")
        target = app.window(title_re=self.config.vs_title_regex)
        if not target.exists(timeout=0.5):
            return None

        try:
            controls = target.descendants()
        except Exception:
            return None

        markers: list[str] = []
        for ctrl in controls:
            try:
                text = re.sub(r"\s+", " ", (ctrl.window_text() or "").strip())
            except Exception:
                continue
            if not text:
                continue

            is_visible = getattr(ctrl, "is_visible", None)
            if callable(is_visible):
                with suppress(Exception):
                    if not bool(is_visible()):
                        continue

            try:
                rect = ctrl.rectangle()
            except Exception:
                rect = None
            if rect is not None:
                rect_width = int(getattr(rect, "right", 0)) - int(getattr(rect, "left", 0))
                rect_height = int(getattr(rect, "bottom", 0)) - int(getattr(rect, "top", 0))
                if rect_width <= 0 or rect_height <= 0:
                    continue

            marker_text = self._build_control_marker_text(ctrl)
            if self._is_disallowed_input_target(marker_text):
                continue

            markers.append(f"{marker_text}|{text}")

        if not markers:
            return None
        return tuple(markers[-200:])

    def _template_detect_stop_button(self, window: Any) -> bool:
        if not self.config.stop_templates:
            return False
        region = self._template_search_region(window)
        if cv2 is not None and np is not None:
            return self._template_detect_with_cv2(region)
        return self._template_detect_with_pyautogui(region)

    def _template_search_region(self, window: Any) -> tuple[int, int, int, int]:
        left, top, width, height = self._window_region(window)
        click_xy = self._resolve_input_click(window)
        if click_xy is None:
            return left, top, width, height

        click_x, click_y = click_xy
        left_pad = max(160, int(round(width * 0.06)))
        right_pad = max(220, int(round(width * 0.10)))
        top_pad = max(140, int(round(height * 0.14)))
        bottom_pad = max(80, int(round(height * 0.08)))

        region_left = max(left, click_x - left_pad)
        region_top = max(top, click_y - top_pad)
        region_right = min(left + width, click_x + right_pad)
        region_bottom = min(top + height, click_y + bottom_pad)

        if region_right <= region_left or region_bottom <= region_top:
            return left, top, width, height
        return region_left, region_top, region_right - region_left, region_bottom - region_top

    def _log_template_match(
        self,
        backend: str,
        template_path: Path,
        region: tuple[int, int, int, int],
        match_left: int,
        match_top: int,
        score: float | None = None,
        scale: float | None = None,
    ) -> None:
        score_text = "" if score is None else f" score={score:.3f}"
        scale_text = "" if scale is None else f" scale={scale:.2f}"
        self._log_centroid_debug(
            "template_match "
            f"backend={backend} template={template_path} x={match_left} y={match_top} "
            f"region={region[0]},{region[1]},{region[2]},{region[3]}{score_text}{scale_text}"
        )

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
                self._log_template_match(
                    "pyautogui",
                    template_path,
                    region,
                    int(getattr(match, "left", region[0])),
                    int(getattr(match, "top", region[1])),
                )
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
                _, max_val, _, max_loc = cv2.minMaxLoc(result)
                if max_val >= self.config.template_confidence:
                    self._log_template_match(
                        "cv2",
                        template_path,
                        region,
                        region[0] + int(max_loc[0]),
                        region[1] + int(max_loc[1]),
                        float(max_val),
                        scale,
                    )
                    return True

        return False

    def _submit_prompt(self, window: Any) -> bool:
        self._last_submit_saw_activity = False
        target_source = "unresolved"

        # Snapshot current halt keyword occurrence count before submitting.
        # Early-stop should only trigger if new occurrences appear afterward.
        self._halt_keyword_baseline = None
        if not self.config.disable_halt_keyword_scan and self.config.halt_keyword.strip() and Desktop is not None:
            with suppress(Exception):
                self._halt_keyword_baseline = self._uia_count_halt_keyword_occurrences(window)

        with suppress(Exception):
            window.activate()

        time.sleep(0.15)

        click_xy = self._resolve_input_click(window)
        if click_xy is not None:
            target_source = "configured_click"
        if click_xy is None:
            click_xy = self._autodetect_chat_input_click(window)
            if click_xy is not None:
                target_source = "uia_autodetect"
        if click_xy is None:
            click_xy = self._uia_chat_input_centroid_click(window)
            if click_xy is not None:
                target_source = "uia_centroid"
        if click_xy is None:
            click_xy = self._probe_click_for_chat_input(window)
            if click_xy is not None:
                target_source = "probe_click"
        if click_xy is None and not self.config.allow_unsafe_hotkey_focus:
            # Deterministic fallback for layouts where UIA metadata is sparse.
            # Safety is still enforced by focused-control verification.
            click_xy = self._default_safe_input_click(window)
            target_source = "default_safe_click"

        if click_xy is not None:
            self._log(f"target_selection source={target_source} xy={click_xy[0]},{click_xy[1]}")

        if click_xy is None and not self.config.allow_unsafe_hotkey_focus:
            self._log(
                "Refusing submit: no verified chat input target found for safe paste targeting. "
                "Set --input-click-x/y or --input-click-x-ratio/y-ratio, or ensure UIA can locate chat input."
            )
            return False

        if click_xy is not None and not self._is_hard_lock_chat_zone(window, click_xy):
            # UIA-proven points are allowed even when pane geometry drifts
            # outside conservative hard-lock bounds.
            if target_source in ("uia_autodetect", "uia_centroid") and self._uia_point_is_chat_input(window, click_xy):
                self._log_submit_decision("target_override", click_xy, "outside_hard_lock_but_uia_verified")
            else:
                self._log_submit_decision("target_rejected", click_xy, "outside_hard_lock_chat_zone")
                return False

        if click_xy is not None and not self._focus_verified_chat_input(window, click_xy):
            if not self._try_verified_hotkey_focus(window):
                self._log_submit_decision("target_rejected", click_xy, "unable_to_verify_chat_input_focus")
                return False
        elif self.config.allow_unsafe_hotkey_focus:
            # Unsafe escape hatch for legacy environments.
            keys = [k.strip() for k in self.config.chat_focus_hotkey.split("+") if k.strip()]
            should_send_hotkey = bool(keys) and (
                self.config.reuse_chat_focus_hotkey or not self._chat_focus_hotkey_used
            )
            if should_send_hotkey:
                pyautogui.hotkey(*keys)
                self._chat_focus_hotkey_used = True
                time.sleep(0.15)

        if self.config.dry_run:
            self._last_submit_saw_activity = True
            return True

        if not self.config.allow_unsafe_hotkey_focus and not self._pre_paste_guard(window, click_xy, "before_clear"):
            return False

        # Belt-and-suspenders: clear any stale draft text before pasting.
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.03)
        pyautogui.press("delete")
        time.sleep(0.05)

        if not self.config.allow_unsafe_hotkey_focus and not self._pre_paste_guard(window, click_xy, "after_clear"):
            return False

        pyperclip.copy(self.config.prompt)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(self.config.submit_enter_delay_seconds)
        self._log("Sending Enter submit key.")
        pyautogui.press("enter")

        # Some chat surfaces occasionally swallow Enter after paste.
        # Retry Enter once, then Return, if chat still appears idle.
        time.sleep(0.2)
        is_active_after_enter = False
        with suppress(Exception):
            is_active_after_enter = self._is_chat_active(window)

        if not is_active_after_enter:
            self._log("No activity after Enter; sending second Enter fallback.")
            pyautogui.press("enter")
            time.sleep(0.15)

            is_active_after_second_enter = False
            with suppress(Exception):
                is_active_after_second_enter = self._is_chat_active(window)

            if not is_active_after_second_enter:
                self._log("No activity after second Enter; sending Return fallback.")
                pyautogui.press("return")

        if self.config.post_submit_activity_wait_seconds > 0:
            deadline = time.monotonic() + self.config.post_submit_activity_wait_seconds
            while time.monotonic() < deadline:
                if self._is_chat_active(window):
                    self._last_submit_saw_activity = True
                    break
                time.sleep(0.2)
        return True

    def _pre_paste_guard(self, window: Any, click_xy: tuple[int, int] | None, phase: str) -> bool:
        verdict, reason = self._focused_target_is_safe_chat_input(window, click_xy)
        if not verdict:
            self._log_submit_decision("paste_blocked", click_xy, f"{phase}:{reason}")
            return False
        self._log_submit_decision("paste_allowed", click_xy, f"{phase}:{reason}")
        return True

    def _log_submit_decision(self, decision: str, click_xy: tuple[int, int] | None, reason: str) -> None:
        target = "none" if click_xy is None else f"{click_xy[0]},{click_xy[1]}"
        self._log(f"submit_decision={decision} target={target} reason={reason}")

    def _try_verified_hotkey_focus(self, window: Any) -> bool:
        if not self.config.allow_verified_hotkey_fallback:
            return False

        keys = [k.strip() for k in self.config.chat_focus_hotkey.split("+") if k.strip()]
        if not keys:
            return False

        with suppress(Exception):
            pyautogui.hotkey(*keys)
            self._chat_focus_hotkey_used = True
            time.sleep(0.15)

        return self._uia_focused_edit_looks_like_chat_input(window)

    @staticmethod
    def _is_pyautogui_failsafe_exception(exc: Exception) -> bool:
        return exc.__class__.__name__ == "FailSafeException"

    def _autodetect_chat_input_click(self, window: Any) -> tuple[int, int] | None:
        if Desktop is None:
            return None

        left, top, width, height = self._window_region(window)
        lower_guard_y = top + int(height * 0.55)

        app = Desktop(backend="uia")
        target = app.window(title_re=self.config.vs_title_regex)
        if not target.exists(timeout=0.5):
            return None

        scored: list[tuple[int, int, int, int, int]] = []
        for ctrl in target.descendants(control_type="Edit"):
            try:
                rect = ctrl.rectangle()
                if rect.bottom < lower_guard_y:
                    continue

                marker_text = self._build_control_marker_text(ctrl)
                if self._is_disallowed_input_target(marker_text):
                    continue

                has_chat_marker = self._has_chat_input_marker(marker_text)
                rect_height = max(0, int(rect.bottom - rect.top))
                rect_width = max(0, int(rect.right - rect.left))
                is_lower_geometry_candidate = (
                    int((rect.top + rect.bottom) / 2) >= lower_guard_y
                    and rect_height <= 320
                    and rect_width >= int(width * 0.18)
                )

                # Prefer explicit chat-marked controls, but keep a geometry fallback
                # for UI builds where marker text is sparse or renamed.
                if not has_chat_marker and not is_lower_geometry_candidate:
                    continue

                marker_score = 2 if has_chat_marker else 1

                center_x = max(left, min(left + width - 1, int((rect.left + rect.right) / 2)))
                center_y = max(top, min(top + height - 1, int((rect.top + rect.bottom) / 2)))

                right_bias = center_x

                # Prefer explicit chat markers and lower controls nearest the input area.
                scored.append((marker_score, center_y, right_bias, center_x, center_y))
            except Exception:
                continue

        if not scored:
            return None

        scored.sort(reverse=True)
        _, _, _, x, y = scored[0]
        return x, y

    def _uia_chat_input_centroid_click(self, window: Any) -> tuple[int, int] | None:
        if Desktop is None:
            self._log_centroid_debug("centroid_unavailable reason=uia_unavailable")
            return None

        left, top, width, height = self._window_region(window)
        lower_guard_y = top + int(height * 0.55)

        app = Desktop(backend="uia")
        target = app.window(title_re=self.config.vs_title_regex)
        if not target.exists(timeout=0.5):
            self._log_centroid_debug("centroid_unavailable reason=vscode_uia_window_missing")
            return None

        weight_total = 0.0
        x_sum = 0.0
        y_sum = 0.0
        scanned_controls = 0
        accepted_controls = 0
        rejected_disallowed = 0
        rejected_type = 0
        rejected_geometry = 0
        rejected_position = 0

        for ctrl in target.descendants():
            try:
                scanned_controls += 1
                rect = ctrl.rectangle()
                rect_left = int(rect.left)
                rect_top = int(rect.top)
                rect_right = int(rect.right)
                rect_bottom = int(rect.bottom)

                rect_height = max(0, rect_bottom - rect_top)
                rect_width = max(0, rect_right - rect_left)
                if rect_height <= 0 or rect_width <= 0:
                    continue

                center_x = int((rect_left + rect_right) / 2)
                center_y = int((rect_top + rect_bottom) / 2)
                if center_y < lower_guard_y:
                    continue

                marker_text = self._build_control_marker_text(ctrl)
                if self._is_disallowed_input_target(marker_text):
                    rejected_disallowed += 1
                    continue

                element_info = getattr(ctrl, "element_info", None)
                control_type = str(getattr(element_info, "control_type", "") or "")
                if control_type and control_type not in ALLOWED_CHAT_INPUT_CONTROL_TYPES:
                    rejected_type += 1
                    continue

                # Weight explicit markers highest; otherwise accept lower-pane,
                # chat-shaped controls as a screen-reader-backed geometry fallback.
                if self._has_chat_input_marker(marker_text):
                    weight = 3.0
                else:
                    if rect_height > 360 or rect_width < int(width * 0.12):
                        rejected_geometry += 1
                        continue
                    if center_x < left + int(width * 0.42):
                        rejected_position += 1
                        continue
                    weight = 1.0

                x_sum += float(center_x) * weight
                y_sum += float(center_y) * weight
                weight_total += weight
                accepted_controls += 1
            except Exception:
                continue

        if weight_total <= 0.0:
            self._log_centroid_debug(
                "centroid_unavailable "
                f"reason=no_safe_candidates scanned={scanned_controls} accepted={accepted_controls} "
                f"reject_disallowed={rejected_disallowed} reject_type={rejected_type} "
                f"reject_geometry={rejected_geometry} reject_position={rejected_position}"
            )
            return None

        centroid_x = int(round(x_sum / weight_total))
        centroid_y = int(round(y_sum / weight_total))

        centroid_x = max(left, min(left + width - 1, centroid_x))
        centroid_y = max(top, min(top + height - 1, centroid_y))

        snapped = False
        if not self._is_hard_lock_chat_zone(window, (centroid_x, centroid_y)):
            # Snap to the nearest safe zone coordinate when centroid drifts due to
            # mixed lower-pane controls in accessibility trees.
            min_x_ratio = 0.68 if width >= 900 else 0.52
            min_y_ratio = 0.72 if height >= 650 else 0.60
            centroid_x = max(centroid_x, left + int(round(width * min_x_ratio)))
            centroid_y = max(centroid_y, top + int(round(height * min_y_ratio)))
            centroid_y = min(centroid_y, top + int(round(height * 0.98)))
            snapped = True

        self._log_centroid_debug(
            "centroid_selected "
            f"x={centroid_x} y={centroid_y} snapped={str(snapped).lower()} "
            f"scanned={scanned_controls} accepted={accepted_controls} "
            f"reject_disallowed={rejected_disallowed} reject_type={rejected_type} "
            f"reject_geometry={rejected_geometry} reject_position={rejected_position}"
        )

        return centroid_x, centroid_y

    def _log_centroid_debug(self, message: str) -> None:
        if self.config.log_centroid_debug:
            self._log(f"centroid_debug {message}")

    def _default_probe_anchors(self, window: Any) -> tuple[tuple[int, int], ...]:
        left, top, width, height = self._window_region(window)

        ratio_points: list[tuple[float, float]] = [
            (self.config.default_safe_click_x_ratio, self.config.default_safe_click_y_ratio),
            (0.74, 0.92),
            (0.88, 0.92),
            (0.82, 0.88),
        ]

        # Compact layouts often shift composer position up/left.
        if width < 900:
            ratio_points.extend([(0.66, 0.90), (0.58, 0.90)])
        if height < 650:
            ratio_points.extend([(0.82, 0.84), (0.70, 0.84)])

        anchors: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for x_ratio, y_ratio in ratio_points:
            abs_x = left + int(round(width * x_ratio))
            abs_y = top + int(round(height * y_ratio))
            anchor = (
                max(left, min(left + width - 1, abs_x)),
                max(top, min(top + height - 1, abs_y)),
            )
            if anchor in seen:
                continue
            seen.add(anchor)
            anchors.append(anchor)

        return tuple(anchors)

    def _probe_click_for_chat_input(self, window: Any) -> tuple[int, int] | None:
        # Try a small, deterministic set of lower-composer anchors so dynamic
        # pane shapes still converge without roaming broadly across the window.
        for anchor_x, anchor_y in self._default_probe_anchors(window):
            for abs_x, abs_y in self._focus_click_candidates(window, (anchor_x, anchor_y)):
                with suppress(Exception):
                    pyautogui.click(abs_x, abs_y)
                    time.sleep(0.06)

                if self._uia_focused_edit_looks_like_chat_input(window):
                    return anchor_x, anchor_y

        return None

    def _default_safe_input_click(self, window: Any) -> tuple[int, int]:
        left, top, width, height = self._window_region(window)
        x_ratio = self.config.default_safe_click_x_ratio
        y_ratio = self.config.default_safe_click_y_ratio

        # Narrow/squished layouts shift the composer left/up; bias the default
        # safe anchor so verification gets a better first click in compressed UIs.
        if width < 900:
            x_ratio = max(0.68, x_ratio - 0.14)
        if height < 650:
            y_ratio = max(0.86, y_ratio - 0.06)

        abs_x = left + int(round(width * x_ratio))
        abs_y = top + int(round(height * y_ratio))
        return abs_x, abs_y

    def _uia_focused_edit_looks_like_chat_input(self, window: Any) -> bool:
        if Desktop is None:
            return False

        _left, top, width, height = self._window_region(window)
        lower_guard_y = top + int(height * 0.55)

        app = Desktop(backend="uia")
        target = app.window(title_re=self.config.vs_title_regex)
        if not target.exists(timeout=0.5):
            return False

        try:
            focused = target.descendants()
        except Exception:
            return False

        for ctrl in focused:
            try:
                if not bool(getattr(ctrl, "has_keyboard_focus", lambda: False)()):
                    continue

                element_info = getattr(ctrl, "element_info", None)
                control_type = str(getattr(element_info, "control_type", "") or "")
                if control_type and control_type not in ALLOWED_CHAT_INPUT_CONTROL_TYPES:
                    continue

                marker_text = self._build_control_marker_text(ctrl)
                if self._is_disallowed_input_target(marker_text):
                    return False

                if self._has_chat_input_marker(marker_text):
                    return True

                rect = ctrl.rectangle()
                rect_height = max(0, int(rect.bottom - rect.top))
                rect_width = max(0, int(rect.right - rect.left))
                center_y = int((rect.top + rect.bottom) / 2)
                if center_y >= lower_guard_y and rect_height <= 280 and rect_width >= int(width * 0.2):
                    return True
            except Exception:
                continue

        return False

    def _uia_focused_control_looks_like_safe_lower_input(self, window: Any) -> bool:
        if Desktop is None:
            return False

        _left, top, width, height = self._window_region(window)
        lower_guard_y = top + int(height * 0.55)

        app = Desktop(backend="uia")
        target = app.window(title_re=self.config.vs_title_regex)
        if not target.exists(timeout=0.5):
            return False

        try:
            controls = target.descendants()
        except Exception:
            return False

        for ctrl in controls:
            try:
                if not bool(getattr(ctrl, "has_keyboard_focus", lambda: False)()):
                    continue

                marker_text = self._build_control_marker_text(ctrl)
                if self._is_disallowed_input_target(marker_text):
                    return False

                rect = ctrl.rectangle()
                rect_height = max(0, int(rect.bottom - rect.top))
                rect_width = max(0, int(rect.right - rect.left))
                center_y = int((rect.top + rect.bottom) / 2)
                if center_y >= lower_guard_y and rect_height <= 320 and rect_width >= int(width * 0.18):
                    return True
            except Exception:
                continue

        return False

    def _focus_verified_chat_input(self, window: Any, click_xy: tuple[int, int]) -> bool:
        if Desktop is None:
            return False

        candidates = self._focus_click_candidates(window, click_xy)
        self._log(
            "focus_candidates=" + ";".join(f"{candidate_x},{candidate_y}" for candidate_x, candidate_y in candidates)
        )
        for index, focus_xy in enumerate(candidates):
            try:
                pyautogui.click(focus_xy[0], focus_xy[1])
            except Exception as exc:
                if self._is_pyautogui_failsafe_exception(exc):
                    self._log("PyAutoGUI fail-safe triggered before focus click; submit skipped.")
                    return False
                raise
            # Use single-click targeting to avoid accidental double-click text
            # selection, then give UIA a short settle/recheck window.
            time.sleep(0.1 if index == 0 else 0.08)
            if self._uia_point_is_chat_input(window, focus_xy):
                return True
            time.sleep(0.04)
            if self._uia_point_is_chat_input(window, focus_xy):
                return True

        # Some VS Code/Copilot builds expose focused composer edit controls
        # with non-standard bounds; allow focused-edit verification fallback.
        if self._uia_focused_edit_looks_like_chat_input(window):
            return True

        # Final safe fallback for sparse UIA metadata.
        return self._uia_focused_control_looks_like_safe_lower_input(window)

    def _hard_lock_above_click(self, window: Any, click_xy: tuple[int, int]) -> tuple[int, int]:
        left, top, width, height = self._window_region(window)
        offset = max(20, int(round(height * self.config.hard_lock_vertical_offset_ratio)))
        x = max(left, min(left + width - 1, int(click_xy[0])))
        y = max(top, min(top + height - 1, int(click_xy[1]) - offset))
        return x, y

    def _focus_click_candidates(self, window: Any, click_xy: tuple[int, int]) -> tuple[tuple[int, int], ...]:
        left, top, width, height = self._window_region(window)
        x = max(left, min(left + width - 1, int(click_xy[0])))
        y = max(top, min(top + height - 1, int(click_xy[1])))

        if not self._is_hard_lock_chat_zone(window, (x, y)):
            return ((x, y),)

        offset = max(20, int(round(height * self.config.hard_lock_vertical_offset_ratio)))
        if width >= 900 and height >= 650:
            y_up = max(top, min(top + height - 1, y - offset))
            y_mid = max(top, min(top + height - 1, y - int(round(offset * 0.5))))
            large_candidates: list[tuple[int, int]] = []
            for candidate in ((x, y), (x, y_mid), (x, y_up)):
                if candidate not in large_candidates:
                    large_candidates.append(candidate)
            return tuple(large_candidates)

        raw_x_candidates = (
            x,
            x - int(round(width * 0.12)),
            x + int(round(width * 0.10)),
            x - int(round(width * 0.06)),
            x + int(round(width * 0.06)),
        )
        raw_y_candidates = (
            y - offset,
            y - int(round(offset * 0.5)),
            y,
            y - int(round(offset * 1.5)),
        )

        candidates: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for candidate_y in raw_y_candidates:
            for candidate_x in raw_x_candidates:
                candidate = (
                    max(left, min(left + width - 1, int(candidate_x))),
                    max(top, min(top + height - 1, int(candidate_y))),
                )
                if candidate in seen:
                    continue
                if not self._is_hard_lock_chat_zone(window, candidate):
                    continue
                seen.add(candidate)
                candidates.append(candidate)
        return tuple(candidates)

    def _is_hard_lock_chat_zone(self, window: Any, click_xy: tuple[int, int]) -> bool:
        left, top, width, height = self._window_region(window)
        if width <= 0 or height <= 0:
            return False

        rel_x = (int(click_xy[0]) - left) / float(width)
        rel_y = (int(click_xy[1]) - top) / float(height)

        min_x = 0.68
        min_y = 0.72
        if width < 900:
            min_x = 0.52
        if height < 650:
            min_y = 0.60

        return rel_x >= min_x and min_y <= rel_y <= 0.98

    def _focused_target_is_safe_chat_input(
        self,
        window: Any,
        click_xy: tuple[int, int] | None = None,
    ) -> tuple[bool, str]:
        if Desktop is None:
            return False, "uia_unavailable"

        _left, top, width, height = self._window_region(window)
        lower_guard_y = top + int(height * 0.55)

        app = Desktop(backend="uia")
        target = app.window(title_re=self.config.vs_title_regex)
        if not target.exists(timeout=0.5):
            return False, "vscode_uia_window_missing"

        try:
            controls = target.descendants()
        except Exception:
            return False, "uia_descendants_unavailable"

        focused_seen = False
        for ctrl in controls:
            try:
                if not bool(getattr(ctrl, "has_keyboard_focus", lambda: False)()):
                    continue
                focused_seen = True

                marker_text = self._build_control_marker_text(ctrl)
                ancestry_text = self._build_control_ancestry_marker_text(ctrl)
                combined_marker = f"{marker_text} {ancestry_text}"
                if self._is_disallowed_input_target(combined_marker):
                    return False, self._summarize_marker_reason("terminal_ancestor_or_focus", combined_marker)

                rect = ctrl.rectangle()
                rect_height = max(0, int(rect.bottom - rect.top))
                rect_width = max(0, int(rect.right - rect.left))
                center_y = int((rect.top + rect.bottom) / 2)
                if center_y < lower_guard_y:
                    return False, "focused_control_above_chat_zone"
                if click_xy is not None and self._uia_point_is_chat_input(window, click_xy):
                    return True, self._summarize_marker_reason("focused_safe_verified_click", combined_marker)
                if rect_height > 360 or rect_width < int(width * 0.12):
                    return False, "focused_control_geometry_rejected"
                return True, self._summarize_marker_reason("focused_safe_lower_input", combined_marker)
            except Exception:
                continue

        return False, "no_focused_control" if not focused_seen else "focused_control_unreadable"

    def _is_active_window_match(self, window: Any) -> bool:
        try:
            active = gw.getActiveWindow()
            if active is None:
                return False

            # Prefer title match when window managers shift coordinates slightly.
            active_title = str(getattr(active, "title", "") or "")
            if active_title and re.match(self.config.vs_title_regex, active_title, re.IGNORECASE):
                return True

            left_delta = abs(int(getattr(active, "left", 0)) - int(getattr(window, "left", 0)))
            top_delta = abs(int(getattr(active, "top", 0)) - int(getattr(window, "top", 0)))
            width_delta = abs(int(getattr(active, "width", 0)) - int(getattr(window, "width", 0)))
            height_delta = abs(int(getattr(active, "height", 0)) - int(getattr(window, "height", 0)))

            return left_delta <= 24 and top_delta <= 24 and width_delta <= 64 and height_delta <= 64
        except Exception:
            return False

    def _uia_point_is_chat_input(self, window: Any, click_xy: tuple[int, int]) -> bool:
        if Desktop is None:
            return False

        _left, top, width, height = self._window_region(window)
        abs_x, abs_y = click_xy
        lower_guard_y = top + int(height * 0.55)

        app = Desktop(backend="uia")
        target = app.window(title_re=self.config.vs_title_regex)
        if not target.exists(timeout=0.5):
            return False

        for ctrl in target.descendants():
            try:
                rect = ctrl.rectangle()
                if not (rect.left <= abs_x <= rect.right and rect.top <= abs_y <= rect.bottom):
                    continue

                element_info = getattr(ctrl, "element_info", None)
                control_type = str(getattr(element_info, "control_type", "") or "")
                if control_type and control_type not in ALLOWED_CHAT_INPUT_CONTROL_TYPES:
                    continue

                marker_text = self._build_control_marker_text(ctrl)
                if self._is_disallowed_input_target(marker_text):
                    continue

                if self._has_chat_input_marker(marker_text):
                    return True

                # Conservative fallback for custom UIA trees.
                rect_height = max(0, int(rect.bottom - rect.top))
                rect_width = max(0, int(rect.right - rect.left))
                if abs_y >= lower_guard_y and rect_height <= 280 and rect_width >= int(width * 0.2):
                    return True
            except Exception:
                continue

        return False

    def _build_control_marker_text(self, ctrl: Any) -> str:
        name = (ctrl.window_text() or "").strip().lower()
        element_info = getattr(ctrl, "element_info", None)
        automation_id = ""
        class_name = ""
        control_type = ""
        if element_info is not None:
            automation_id = str(getattr(element_info, "automation_id", "") or "").lower()
            class_name = str(getattr(element_info, "class_name", "") or "").lower()
            control_type = str(getattr(element_info, "control_type", "") or "").lower()
        return f"{name} {automation_id} {class_name} {control_type}"

    def _build_control_ancestry_marker_text(self, ctrl: Any) -> str:
        markers: list[str] = []
        current = ctrl
        for _ in range(8):
            try:
                parent = current.parent()
            except Exception:
                break
            if parent is None:
                break
            markers.append(self._build_control_marker_text(parent))
            current = parent
        return " ".join(markers)

    @staticmethod
    def _summarize_marker_reason(prefix: str, marker_text: str) -> str:
        compact = " ".join(marker_text.split())[:160]
        return f"{prefix}:{compact}"

    def _has_chat_input_marker(self, marker_text: str) -> bool:
        return any(token in marker_text for token in CHAT_INPUT_MARKER_TOKENS)

    def _is_disallowed_input_target(self, marker_text: str) -> bool:
        return any(token in marker_text for token in DISALLOWED_INPUT_MARKER_TOKENS)

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
        self._log(f"Single-flight timeout: {self.config.single_flight_timeout_seconds:.1f}s.")
        self._log(f"Output stable cycles: {self.config.output_stable_cycles}.")
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
        required=True,
        help=(
            "Prompt text to submit. In PowerShell, pass multiline content via a here-string "
            "or quote the full value so it is treated as one argument."
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
        "--idle-stable-cycles",
        type=int,
        default=2,
        help="Require this many consecutive idle checks before submit (debounce).",
    )
    parser.add_argument(
        "--submit-cooldown-seconds",
        type=float,
        default=1.5,
        help="Delay after each submit before next state check.",
    )
    parser.add_argument(
        "--no-activity-backoff-seconds",
        type=float,
        default=8.0,
        help="Extended cooldown when no post-submit activity is detected.",
    )
    parser.add_argument(
        "--single-flight-timeout-seconds",
        type=float,
        default=45.0,
        help="How long to wait before logging that the post-submit activity edge has not appeared.",
    )
    parser.add_argument(
        "--output-stable-cycles",
        type=int,
        default=2,
        help="Require this many unchanged UIA output snapshots after activity stops before next submit.",
    )
    parser.add_argument(
        "--post-submit-activity-wait-seconds",
        type=float,
        default=2.5,
        help="How long to wait for active-state evidence after submit.",
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
        "--reuse-chat-focus-hotkey",
        action="store_true",
        help="Re-send chat focus hotkey before every submit (default sends once).",
    )
    parser.add_argument(
        "--allow-unsafe-hotkey-focus",
        action="store_true",
        help=(
            "Allow fallback chat focus hotkey when click-target coordinates are absent. "
            "Unsafe: may focus non-chat UI and may collapse/toggle chat pane."
        ),
    )
    parser.add_argument(
        "--allow-verified-hotkey-fallback",
        action="store_true",
        help=(
            "Allow verified hotkey fallback after click-focus verification fails. "
            "Off by default to avoid toggling/collapsing chat panels."
        ),
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
            "Set a unique marker phrase your agent emits when done. Quote values with spaces "
            "in PowerShell."
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
        "--log-centroid-debug",
        action="store_true",
        help="Log centroid candidate counts, rejection reasons, and final snap decisions each cycle.",
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

    if args.idle_stable_cycles < 1:
        parser.error("--idle-stable-cycles must be >= 1.")

    if args.no_activity_backoff_seconds < 0:
        parser.error("--no-activity-backoff-seconds must be >= 0.")

    if args.single_flight_timeout_seconds < 0:
        parser.error("--single-flight-timeout-seconds must be >= 0.")

    if args.output_stable_cycles < 1:
        parser.error("--output-stable-cycles must be >= 1.")

    if args.post_submit_activity_wait_seconds < 0:
        parser.error("--post-submit-activity-wait-seconds must be >= 0.")

    if args.template_confidence < 0.1 or args.template_confidence > 1.0:
        parser.error("--template-confidence must be between 0.1 and 1.0.")

    prompt_text = str(args.prompt).strip()

    if not prompt_text:
        parser.error("Provide --prompt with non-empty content.")

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
    cli_has_abs_coords = input_click_x is not None or input_click_y is not None
    cli_has_ratio_coords = input_click_x_ratio is not None or input_click_y_ratio is not None

    if input_click_x is None and isinstance(profile.get("input_click_x"), int):
        input_click_x = int(profile["input_click_x"])
    if input_click_y is None and isinstance(profile.get("input_click_y"), int):
        input_click_y = int(profile["input_click_y"])
    if input_click_x_ratio is None and isinstance(profile.get("input_click_x_ratio"), int | float):
        input_click_x_ratio = float(profile["input_click_x_ratio"])
    if input_click_y_ratio is None and isinstance(profile.get("input_click_y_ratio"), int | float):
        input_click_y_ratio = float(profile["input_click_y_ratio"])

    # Compatibility normalization: older calibrator profiles can include both absolute
    # and ratio coordinates. Prefer explicit CLI mode; otherwise prefer ratios.
    if input_click_x is not None and input_click_x_ratio is not None:
        if cli_has_abs_coords and cli_has_ratio_coords:
            parser.error("Use absolute input click coordinates OR ratio coordinates, not both.")
        if cli_has_abs_coords:
            input_click_x_ratio = None
            input_click_y_ratio = None
        else:
            input_click_x = None
            input_click_y = None

    if (input_click_x is None) != (input_click_y is None):
        parser.error("Provide both --input-click-x and --input-click-y, or neither.")
    if (input_click_x_ratio is None) != (input_click_y_ratio is None):
        parser.error("Provide both --input-click-x-ratio and --input-click-y-ratio, or neither.")
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
        idle_stable_cycles=args.idle_stable_cycles,
        submit_cooldown_seconds=args.submit_cooldown_seconds,
        no_activity_backoff_seconds=args.no_activity_backoff_seconds,
        single_flight_timeout_seconds=args.single_flight_timeout_seconds,
        output_stable_cycles=args.output_stable_cycles,
        post_submit_activity_wait_seconds=args.post_submit_activity_wait_seconds,
        submit_enter_delay_seconds=args.submit_enter_delay_seconds,
        vs_title_regex=vs_title_regex,
        chat_focus_hotkey=chat_focus_hotkey,
        reuse_chat_focus_hotkey=args.reuse_chat_focus_hotkey,
        allow_unsafe_hotkey_focus=args.allow_unsafe_hotkey_focus,
        allow_verified_hotkey_fallback=args.allow_verified_hotkey_fallback,
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
        log_centroid_debug=args.log_centroid_debug,
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
