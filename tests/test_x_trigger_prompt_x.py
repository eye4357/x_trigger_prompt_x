import argparse
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import x_trigger_prompt_x as tool


class ParseArgsTests(unittest.TestCase):
    def test_version_flag_exits_cleanly(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            tool.parse_args(["--version"])
        self.assertEqual(ctx.exception.code, 0)

    def test_prompt_required(self) -> None:
        with self.assertRaises(SystemExit):
            tool.parse_args([])

    def test_max_prompts_range_guard(self) -> None:
        with self.assertRaises(SystemExit):
            tool.parse_args(["--prompt", "hi", "--max-prompts", "0"])
        with self.assertRaises(SystemExit):
            tool.parse_args(["--prompt", "hi", "--max-prompts", "513"])

    def test_prompt_is_trimmed(self) -> None:
        cfg = tool.parse_args(["--prompt", " hello world \n"])
        self.assertEqual(cfg.prompt, "hello world")

    def test_profile_supplies_ratio_and_template(self) -> None:
        root = Path("C:/tmp")
        template = root / "stop.png"
        profile = root / "profile.json"
        profile_data = {
            "stop_template": "stop.png",
            "input_click_x_ratio": 0.5,
            "input_click_y_ratio": 0.75,
        }

        with (
            patch.object(Path, "exists", autospec=True) as exists_mock,
            patch.object(Path, "read_text", autospec=True, return_value=json.dumps(profile_data)),
        ):
            exists_mock.side_effect = lambda p: str(p).endswith("profile.json") or str(p).endswith("stop.png")
            cfg = tool.parse_args(
                [
                    "--prompt",
                    "hello",
                    "--profile-file",
                    str(profile),
                ]
            )

        self.assertEqual(cfg.stop_templates, (template,))
        self.assertEqual(cfg.input_click_x_ratio, 0.5)
        self.assertEqual(cfg.input_click_y_ratio, 0.75)

    def test_profile_with_both_coordinate_modes_prefers_ratio_compatibly(self) -> None:
        root = Path("C:/tmp")
        profile = root / "profile.json"
        profile_data = {
            "input_click_x": 830,
            "input_click_y": 756,
            "input_click_x_ratio": 0.5,
            "input_click_y_ratio": 0.75,
        }

        with (
            patch.object(Path, "exists", autospec=True, return_value=True),
            patch.object(Path, "read_text", autospec=True, return_value=json.dumps(profile_data)),
        ):
            cfg = tool.parse_args(
                [
                    "--prompt",
                    "hello",
                    "--profile-file",
                    str(profile),
                ]
            )

        self.assertIsNone(cfg.input_click_x)
        self.assertIsNone(cfg.input_click_y)
        self.assertEqual(cfg.input_click_x_ratio, 0.5)
        self.assertEqual(cfg.input_click_y_ratio, 0.75)

    def test_log_centroid_debug_flag_sets_config(self) -> None:
        cfg = tool.parse_args(["--prompt", "hello", "--log-centroid-debug"])
        self.assertTrue(cfg.log_centroid_debug)


class HelperFunctionTests(unittest.TestCase):
    def test_parse_scales_csv_deduplicates_and_preserves_order(self) -> None:
        parser = argparse.ArgumentParser()
        scales = tool._parse_scales_csv("1.0,0.9,1.0,0.9,1.1", parser)
        self.assertEqual(scales, (1.0, 0.9, 1.1))

    def test_resolve_profile_path_uses_profile_parent(self) -> None:
        profile_file = Path("C:/tmp/profile.json")
        path = tool._resolve_profile_path("templates/stop.png", profile_file)
        self.assertEqual(path, Path("C:/tmp/templates/stop.png"))


class PromptMonitorBehaviorTests(unittest.TestCase):
    def test_is_pyautogui_failsafe_exception_by_name(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)

        FailSafeException = type("FailSafeException", (Exception,), {})
        self.assertTrue(mon._is_pyautogui_failsafe_exception(FailSafeException("boom")))
        self.assertFalse(mon._is_pyautogui_failsafe_exception(RuntimeError("boom")))

    def test_disallowed_terminal_markers_are_rejected(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)
        self.assertTrue(mon._is_disallowed_input_target("integrated terminal xterm"))
        self.assertFalse(mon._is_disallowed_input_target("copilot chat input"))

    def test_autodetect_requires_chat_markers(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)

        class FakeRect:
            left = 100
            top = 600
            right = 900
            bottom = 660

        class FakeCtrl:
            element_info = SimpleNamespace(automation_id="inputField", class_name="editorPane")

            @staticmethod
            def rectangle() -> FakeRect:
                return FakeRect()

            @staticmethod
            def window_text() -> str:
                return ""

        class FakeTarget:
            @staticmethod
            def exists(timeout: float = 0.0) -> bool:
                return True

            @staticmethod
            def descendants(control_type: str | None = None) -> list[object]:
                if control_type == "Edit":
                    return [FakeCtrl()]
                return []

        class FakeDesktop:
            def __init__(self, backend: str = "uia") -> None:
                self.backend = backend

            @staticmethod
            def window(title_re: str | None = None) -> FakeTarget:
                return FakeTarget()

        window = SimpleNamespace(left=0, top=0, width=1200, height=900)
        with patch.object(tool, "Desktop", FakeDesktop):
            self.assertEqual(mon._autodetect_chat_input_click(window), (500, 630))

    def test_resolve_input_click_prefers_absolute(self) -> None:
        cfg = tool.Config(prompt="x", input_click_x=100, input_click_y=200)
        mon = tool.PromptMonitor(cfg)
        win = SimpleNamespace(left=10, top=10, width=1000, height=600)

        self.assertEqual(mon._resolve_input_click(win), (100, 200))

    def test_resolve_input_click_uses_ratio(self) -> None:
        cfg = tool.Config(prompt="x", input_click_x_ratio=0.5, input_click_y_ratio=0.5)
        mon = tool.PromptMonitor(cfg)
        win = SimpleNamespace(left=10, top=20, width=200, height=100)

        self.assertEqual(mon._resolve_input_click(win), (110, 70))

    def test_template_search_region_anchors_near_configured_click(self) -> None:
        cfg = tool.Config(prompt="x", input_click_x_ratio=0.875, input_click_y_ratio=0.89)
        mon = tool.PromptMonitor(cfg)
        win = SimpleNamespace(left=0, top=0, width=2000, height=1000)

        self.assertEqual(mon._template_search_region(win), (1590, 750, 380, 220))

    def test_template_search_region_uses_full_window_without_click_target(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)
        win = SimpleNamespace(left=10, top=20, width=800, height=600)

        self.assertEqual(mon._template_search_region(win), (10, 20, 800, 600))

    def test_template_detection_fallback_uses_multiple_templates(self) -> None:
        cfg = tool.Config(
            prompt="x",
            stop_templates=(Path("a.png"), Path("b.png")),
            template_confidence=0.9,
        )
        mon = tool.PromptMonitor(cfg)

        calls: list[str] = []

        class FakeAutoGui:
            @staticmethod
            def locateOnScreen(path: str, **_kwargs):  # type: ignore[no-untyped-def]
                calls.append(path)
                return object() if path.endswith("b.png") else None

        with patch.object(tool, "pyautogui", FakeAutoGui()):
            found = mon._template_detect_with_pyautogui((0, 0, 100, 100))

        self.assertTrue(found)
        self.assertEqual(calls, ["a.png", "b.png"])

    def test_chat_active_trusts_clean_uia_negative_over_template_match(self) -> None:
        cfg = tool.Config(prompt="x", stop_templates=(Path("stop.png"),), use_uia_scan=True)
        mon = tool.PromptMonitor(cfg)

        with (
            patch.object(tool, "Desktop", object()),
            patch.object(mon, "_uia_detect_stop_button", return_value=False),
            patch.object(mon, "_template_detect_stop_button", return_value=True),
        ):
            self.assertIsNone(mon._chat_active_source(SimpleNamespace()))
            self.assertFalse(mon._is_chat_active(SimpleNamespace()))

    def test_chat_active_uses_template_when_uia_raises(self) -> None:
        cfg = tool.Config(prompt="x", stop_templates=(Path("stop.png"),), use_uia_scan=True)
        mon = tool.PromptMonitor(cfg)

        with (
            patch.object(tool, "Desktop", object()),
            patch.object(mon, "_uia_detect_stop_button", side_effect=RuntimeError("uia unavailable")),
            patch.object(mon, "_template_detect_stop_button", return_value=True),
        ):
            self.assertEqual(mon._chat_active_source(SimpleNamespace()), "template")
            self.assertTrue(mon._is_chat_active(SimpleNamespace()))

    def test_uia_stop_button_ignores_hidden_stale_controls(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)

        class FakeRect:
            left = 10
            top = 10
            right = 30
            bottom = 30

        class HiddenStopButton:
            @staticmethod
            def window_text() -> str:
                return "Stop"

            @staticmethod
            def is_visible() -> bool:
                return False

            @staticmethod
            def is_enabled() -> bool:
                return True

            @staticmethod
            def rectangle() -> FakeRect:
                return FakeRect()

        class FakeTarget:
            @staticmethod
            def exists(timeout: float = 0.0) -> bool:
                return True

            @staticmethod
            def descendants(control_type: str | None = None) -> list[object]:
                if control_type == "Button":
                    return [HiddenStopButton()]
                return []

        class FakeDesktop:
            def __init__(self, backend: str = "uia") -> None:
                self.backend = backend

            @staticmethod
            def window(title_re: str | None = None) -> FakeTarget:
                return FakeTarget()

        with patch.object(tool, "Desktop", FakeDesktop):
            self.assertFalse(mon._uia_detect_stop_button(SimpleNamespace()))

    def test_uia_stop_button_accepts_visible_enabled_controls(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)

        class FakeRect:
            left = 10
            top = 10
            right = 30
            bottom = 30

        class VisibleStopButton:
            @staticmethod
            def window_text() -> str:
                return "Stop"

            @staticmethod
            def is_visible() -> bool:
                return True

            @staticmethod
            def is_enabled() -> bool:
                return True

            @staticmethod
            def rectangle() -> FakeRect:
                return FakeRect()

        class FakeTarget:
            @staticmethod
            def exists(timeout: float = 0.0) -> bool:
                return True

            @staticmethod
            def descendants(control_type: str | None = None) -> list[object]:
                if control_type == "Button":
                    return [VisibleStopButton()]
                return []

        class FakeDesktop:
            def __init__(self, backend: str = "uia") -> None:
                self.backend = backend

            @staticmethod
            def window(title_re: str | None = None) -> FakeTarget:
                return FakeTarget()

        with patch.object(tool, "Desktop", FakeDesktop):
            self.assertTrue(mon._uia_detect_stop_button(SimpleNamespace()))

    def test_find_vscode_window_prefers_active(self) -> None:
        w1 = SimpleNamespace(title="Visual Studio Code", width=500, height=400)
        w2 = SimpleNamespace(title="Visual Studio Code", width=1200, height=900)

        fake_gw = SimpleNamespace(
            getAllWindows=lambda: [w1, w2],
            getActiveWindow=lambda: w1,
        )

        with patch.object(tool, "gw", fake_gw):
            cfg = tool.Config(prompt="x")
            mon = tool.PromptMonitor(cfg)
            result = mon._find_vscode_window()

        self.assertIs(result, w1)

    def test_should_halt_is_disabled_before_first_submit(self) -> None:
        cfg = tool.Config(prompt="x", halt_keyword="HALT NOW", disable_halt_keyword_scan=False)
        mon = tool.PromptMonitor(cfg)

        with patch.object(mon, "_uia_count_halt_keyword_occurrences", return_value=5):
            self.assertFalse(mon._should_halt(SimpleNamespace()))

    def test_should_halt_establishes_baseline_after_first_submit(self) -> None:
        cfg = tool.Config(prompt="x", halt_keyword="HALT NOW", disable_halt_keyword_scan=False)
        mon = tool.PromptMonitor(cfg)
        mon._submitted = 1

        with (
            patch.object(tool, "Desktop", object()),
            patch.object(mon, "_uia_count_halt_keyword_occurrences", return_value=3),
        ):
            self.assertFalse(mon._should_halt(SimpleNamespace()))
            self.assertEqual(mon._halt_keyword_baseline, 3)

    def test_should_halt_triggers_only_on_new_occurrence(self) -> None:
        cfg = tool.Config(prompt="x", halt_keyword="HALT NOW", disable_halt_keyword_scan=False)
        mon = tool.PromptMonitor(cfg)
        mon._submitted = 1
        mon._halt_keyword_baseline = 3

        with (
            patch.object(tool, "Desktop", object()),
            patch.object(mon, "_uia_count_halt_keyword_occurrences", return_value=3),
        ):
            self.assertFalse(mon._should_halt(SimpleNamespace()))

        with (
            patch.object(tool, "Desktop", object()),
            patch.object(mon, "_uia_count_halt_keyword_occurrences", return_value=4),
        ):
            self.assertTrue(mon._should_halt(SimpleNamespace()))

    def test_submit_refuses_without_verified_target_by_default(self) -> None:
        cfg = tool.Config(
            prompt="x",
            chat_focus_hotkey="ctrl+alt+i",
            post_submit_activity_wait_seconds=0.0,
        )
        mon = tool.PromptMonitor(cfg)

        hotkey_calls: list[tuple[str, ...]] = []

        class FakeAutoGui:
            @staticmethod
            def hotkey(*keys: str) -> None:
                hotkey_calls.append(tuple(keys))

            @staticmethod
            def press(_key: str) -> None:
                return None

        class FakeClip:
            @staticmethod
            def copy(_text: str) -> None:
                return None

        window = SimpleNamespace(activate=lambda: None, left=0, top=0, width=100, height=100)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch.object(tool, "pyperclip", FakeClip()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_is_chat_active", return_value=False),
            patch.object(mon, "_autodetect_chat_input_click", return_value=None),
            patch.object(mon, "_try_verified_hotkey_focus", return_value=False),
        ):
            self.assertFalse(mon._submit_prompt(window))
            self.assertFalse(mon._submit_prompt(window))

        focus_calls = [c for c in hotkey_calls if c == ("ctrl", "alt", "i")]
        self.assertEqual(focus_calls, [])

    def test_submit_can_use_unsafe_hotkey_fallback_when_explicitly_enabled(self) -> None:
        cfg = tool.Config(
            prompt="x",
            chat_focus_hotkey="ctrl+alt+i",
            allow_unsafe_hotkey_focus=True,
            reuse_chat_focus_hotkey=True,
            post_submit_activity_wait_seconds=0.0,
        )
        mon = tool.PromptMonitor(cfg)

        hotkey_calls: list[tuple[str, ...]] = []

        class FakeAutoGui:
            @staticmethod
            def hotkey(*keys: str) -> None:
                hotkey_calls.append(tuple(keys))

            @staticmethod
            def press(_key: str) -> None:
                return None

        class FakeClip:
            @staticmethod
            def copy(_text: str) -> None:
                return None

        window = SimpleNamespace(activate=lambda: None, left=0, top=0, width=100, height=100)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch.object(tool, "pyperclip", FakeClip()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_is_chat_active", return_value=False),
        ):
            self.assertTrue(mon._submit_prompt(window))
            self.assertTrue(mon._submit_prompt(window))

        focus_calls = [c for c in hotkey_calls if c == ("ctrl", "alt", "i")]
        self.assertEqual(focus_calls, [("ctrl", "alt", "i"), ("ctrl", "alt", "i")])

    def test_submit_uses_verified_click_target_when_configured(self) -> None:
        cfg = tool.Config(
            prompt="x",
            input_click_x=830,
            input_click_y=756,
            post_submit_activity_wait_seconds=0.0,
        )
        mon = tool.PromptMonitor(cfg)

        class FakeAutoGui:
            @staticmethod
            def hotkey(*_keys: str) -> None:
                return None

            @staticmethod
            def press(_key: str) -> None:
                return None

        class FakeClip:
            @staticmethod
            def copy(_text: str) -> None:
                return None

        window = SimpleNamespace(activate=lambda: None, left=10, top=20, width=1000, height=800)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch.object(tool, "pyperclip", FakeClip()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_is_chat_active", return_value=False),
            patch.object(mon, "_focus_verified_chat_input", return_value=True),
            patch.object(mon, "_pre_paste_guard", return_value=True),
        ):
            self.assertTrue(mon._submit_prompt(window))

    def test_submit_clears_composer_before_paste(self) -> None:
        cfg = tool.Config(
            prompt="x",
            input_click_x=830,
            input_click_y=756,
            post_submit_activity_wait_seconds=0.0,
        )
        mon = tool.PromptMonitor(cfg)

        hotkey_calls: list[tuple[str, ...]] = []
        key_presses: list[str] = []

        class FakeAutoGui:
            @staticmethod
            def hotkey(*keys: str) -> None:
                hotkey_calls.append(tuple(keys))

            @staticmethod
            def press(key: str) -> None:
                key_presses.append(key)

        class FakeClip:
            @staticmethod
            def copy(_text: str) -> None:
                return None

        window = SimpleNamespace(activate=lambda: None, left=10, top=20, width=1000, height=800)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch.object(tool, "pyperclip", FakeClip()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_is_chat_active", return_value=False),
            patch.object(mon, "_focus_verified_chat_input", return_value=True),
            patch.object(mon, "_pre_paste_guard", return_value=True),
        ):
            self.assertTrue(mon._submit_prompt(window))

        self.assertIn(("ctrl", "a"), hotkey_calls)
        self.assertIn(("ctrl", "v"), hotkey_calls)
        self.assertIn("delete", key_presses)

    def test_submit_sends_return_fallback_when_enter_shows_no_activity(self) -> None:
        cfg = tool.Config(
            prompt="x",
            input_click_x=830,
            input_click_y=756,
            post_submit_activity_wait_seconds=0.0,
        )
        mon = tool.PromptMonitor(cfg)

        key_presses: list[str] = []

        class FakeAutoGui:
            @staticmethod
            def hotkey(*_keys: str) -> None:
                return None

            @staticmethod
            def press(key: str) -> None:
                key_presses.append(key)

        class FakeClip:
            @staticmethod
            def copy(_text: str) -> None:
                return None

        window = SimpleNamespace(activate=lambda: None, left=10, top=20, width=1000, height=800)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch.object(tool, "pyperclip", FakeClip()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_is_chat_active", return_value=False),
            patch.object(mon, "_focus_verified_chat_input", return_value=True),
            patch.object(mon, "_pre_paste_guard", return_value=True),
        ):
            self.assertTrue(mon._submit_prompt(window))

        self.assertIn("enter", key_presses)
        self.assertIn("return", key_presses)
        self.assertGreaterEqual(key_presses.count("enter"), 2)

    def test_submit_retries_when_activity_probe_raises(self) -> None:
        cfg = tool.Config(
            prompt="x",
            input_click_x=830,
            input_click_y=756,
            post_submit_activity_wait_seconds=0.0,
        )
        mon = tool.PromptMonitor(cfg)

        key_presses: list[str] = []

        class FakeAutoGui:
            @staticmethod
            def hotkey(*_keys: str) -> None:
                return None

            @staticmethod
            def press(key: str) -> None:
                key_presses.append(key)

        class FakeClip:
            @staticmethod
            def copy(_text: str) -> None:
                return None

        window = SimpleNamespace(activate=lambda: None, left=10, top=20, width=1000, height=800)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch.object(tool, "pyperclip", FakeClip()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_is_chat_active", side_effect=RuntimeError("transient activity probe failure")),
            patch.object(mon, "_focus_verified_chat_input", return_value=True),
            patch.object(mon, "_pre_paste_guard", return_value=True),
        ):
            self.assertTrue(mon._submit_prompt(window))

        self.assertGreaterEqual(key_presses.count("enter"), 2)
        self.assertIn("return", key_presses)

    def test_hard_lock_above_click_offsets_upward(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)
        window = SimpleNamespace(left=10, top=20, width=1000, height=800)

        self.assertEqual(mon._hard_lock_above_click(window, (830, 756)), (830, 692))

    def test_submit_uses_autodetected_target_when_available(self) -> None:
        cfg = tool.Config(
            prompt="x",
            post_submit_activity_wait_seconds=0.0,
        )
        mon = tool.PromptMonitor(cfg)

        class FakeAutoGui:
            @staticmethod
            def hotkey(*_keys: str) -> None:
                return None

            @staticmethod
            def press(_key: str) -> None:
                return None

        class FakeClip:
            @staticmethod
            def copy(_text: str) -> None:
                return None

        window = SimpleNamespace(activate=lambda: None, left=0, top=0, width=1000, height=800)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch.object(tool, "pyperclip", FakeClip()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_is_chat_active", return_value=False),
            patch.object(mon, "_autodetect_chat_input_click", return_value=(820, 736)),
            patch.object(mon, "_focus_verified_chat_input", return_value=True),
            patch.object(mon, "_pre_paste_guard", return_value=True),
        ):
            self.assertTrue(mon._submit_prompt(window))

    def test_submit_allows_uia_autodetect_point_outside_hard_lock_when_uia_verified(self) -> None:
        cfg = tool.Config(
            prompt="x",
            post_submit_activity_wait_seconds=0.0,
        )
        mon = tool.PromptMonitor(cfg)

        class FakeAutoGui:
            @staticmethod
            def hotkey(*_keys: str) -> None:
                return None

            @staticmethod
            def press(_key: str) -> None:
                return None

        class FakeClip:
            @staticmethod
            def copy(_text: str) -> None:
                return None

        window = SimpleNamespace(activate=lambda: None, left=0, top=0, width=1000, height=800)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch.object(tool, "pyperclip", FakeClip()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_is_chat_active", return_value=False),
            patch.object(mon, "_autodetect_chat_input_click", return_value=(430, 700)),
            patch.object(mon, "_is_hard_lock_chat_zone", return_value=False),
            patch.object(mon, "_uia_point_is_chat_input", return_value=True),
            patch.object(mon, "_focus_verified_chat_input", return_value=True),
            patch.object(mon, "_pre_paste_guard", return_value=True),
        ):
            self.assertTrue(mon._submit_prompt(window))

    def test_submit_rejects_probe_point_outside_hard_lock_without_uia_override(self) -> None:
        cfg = tool.Config(
            prompt="x",
            post_submit_activity_wait_seconds=0.0,
        )
        mon = tool.PromptMonitor(cfg)

        class FakeAutoGui:
            @staticmethod
            def hotkey(*_keys: str) -> None:
                return None

            @staticmethod
            def press(_key: str) -> None:
                return None

        class FakeClip:
            @staticmethod
            def copy(_text: str) -> None:
                raise AssertionError("paste should not occur")

        window = SimpleNamespace(activate=lambda: None, left=0, top=0, width=1000, height=800)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch.object(tool, "pyperclip", FakeClip()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_is_chat_active", return_value=False),
            patch.object(mon, "_autodetect_chat_input_click", return_value=None),
            patch.object(mon, "_uia_chat_input_centroid_click", return_value=None),
            patch.object(mon, "_probe_click_for_chat_input", return_value=(430, 700)),
            patch.object(mon, "_is_hard_lock_chat_zone", return_value=False),
        ):
            self.assertFalse(mon._submit_prompt(window))

    def test_submit_uses_probe_target_when_autodetect_absent(self) -> None:
        cfg = tool.Config(
            prompt="x",
            post_submit_activity_wait_seconds=0.0,
        )
        mon = tool.PromptMonitor(cfg)

        class FakeAutoGui:
            @staticmethod
            def hotkey(*_keys: str) -> None:
                return None

            @staticmethod
            def press(_key: str) -> None:
                return None

        class FakeClip:
            @staticmethod
            def copy(_text: str) -> None:
                return None

        window = SimpleNamespace(activate=lambda: None, left=0, top=0, width=1000, height=800)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch.object(tool, "pyperclip", FakeClip()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_is_chat_active", return_value=False),
            patch.object(mon, "_autodetect_chat_input_click", return_value=None),
            patch.object(mon, "_probe_click_for_chat_input", return_value=(820, 736)),
            patch.object(mon, "_focus_verified_chat_input", return_value=True),
            patch.object(mon, "_pre_paste_guard", return_value=True),
        ):
            self.assertTrue(mon._submit_prompt(window))

    def test_probe_click_uses_multiple_anchors_when_unverified(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)

        clicks: list[tuple[int, int]] = []

        class FakeAutoGui:
            @staticmethod
            def click(x: int, y: int) -> None:
                clicks.append((x, y))

        window = SimpleNamespace(left=100, top=200, width=1000, height=800)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_uia_focused_edit_looks_like_chat_input", return_value=False),
        ):
            self.assertIsNone(mon._probe_click_for_chat_input(window))

        self.assertGreaterEqual(len(clicks), 4)
        self.assertEqual(clicks[0], (920, 936))

    def test_verified_probe_returns_anchor_to_avoid_double_hard_lock_offset(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)

        clicks: list[tuple[int, int]] = []

        class FakeAutoGui:
            @staticmethod
            def click(x: int, y: int) -> None:
                clicks.append((x, y))

        window = SimpleNamespace(left=100, top=200, width=1000, height=800)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_uia_focused_edit_looks_like_chat_input", return_value=True),
        ):
            self.assertEqual(mon._probe_click_for_chat_input(window), (920, 936))

        self.assertEqual(clicks, [(920, 936)])

    def test_default_probe_anchors_expand_for_squished_window(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)
        window = SimpleNamespace(left=100, top=200, width=800, height=600)

        anchors = mon._default_probe_anchors(window)

        self.assertIn((756, 752), anchors)
        self.assertIn((628, 740), anchors)
        self.assertIn((564, 740), anchors)

    def test_centroid_debug_logs_when_enabled(self) -> None:
        cfg = tool.Config(prompt="x", log_centroid_debug=True)
        mon = tool.PromptMonitor(cfg)

        with patch.object(mon, "_log") as log_mock:
            mon._log_centroid_debug("selected x=1 y=2")

        log_mock.assert_called_once_with("centroid_debug selected x=1 y=2")

    def test_centroid_debug_is_silent_when_disabled(self) -> None:
        cfg = tool.Config(prompt="x", log_centroid_debug=False)
        mon = tool.PromptMonitor(cfg)

        with patch.object(mon, "_log") as log_mock:
            mon._log_centroid_debug("selected x=1 y=2")

        log_mock.assert_not_called()

    def test_uia_chat_input_centroid_prefers_chat_marked_controls(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)

        class ChatRect:
            left = 800
            top = 730
            right = 980
            bottom = 770

        class GenericRect:
            left = 700
            top = 700
            right = 940
            bottom = 760

        class ChatCtrl:
            element_info = SimpleNamespace(automation_id="copilotChatInput", class_name="editor", control_type="Edit")

            @staticmethod
            def rectangle() -> ChatRect:
                return ChatRect()

            @staticmethod
            def window_text() -> str:
                return "Ask Copilot"

        class GenericCtrl:
            element_info = SimpleNamespace(automation_id="inputArea", class_name="pane", control_type="Edit")

            @staticmethod
            def rectangle() -> GenericRect:
                return GenericRect()

            @staticmethod
            def window_text() -> str:
                return ""

        class FakeTarget:
            @staticmethod
            def exists(timeout: float = 0.0) -> bool:
                return True

            @staticmethod
            def descendants(control_type: str | None = None) -> list[object]:
                return [ChatCtrl(), GenericCtrl()]

        class FakeDesktop:
            def __init__(self, backend: str = "uia") -> None:
                self.backend = backend

            @staticmethod
            def window(title_re: str | None = None) -> FakeTarget:
                return FakeTarget()

        window = SimpleNamespace(left=0, top=0, width=1000, height=800)
        with patch.object(tool, "Desktop", FakeDesktop):
            self.assertEqual(mon._uia_chat_input_centroid_click(window), (872, 745))

    def test_uia_chat_input_centroid_snaps_to_safe_zone(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)

        class LeftHeavyRect:
            left = 520
            top = 710
            right = 660
            bottom = 750

        class LeftHeavyCtrl:
            element_info = SimpleNamespace(automation_id="inputArea", class_name="pane", control_type="Edit")

            @staticmethod
            def rectangle() -> LeftHeavyRect:
                return LeftHeavyRect()

            @staticmethod
            def window_text() -> str:
                return ""

        class FakeTarget:
            @staticmethod
            def exists(timeout: float = 0.0) -> bool:
                return True

            @staticmethod
            def descendants(control_type: str | None = None) -> list[object]:
                return [LeftHeavyCtrl()]

        class FakeDesktop:
            def __init__(self, backend: str = "uia") -> None:
                self.backend = backend

            @staticmethod
            def window(title_re: str | None = None) -> FakeTarget:
                return FakeTarget()

        window = SimpleNamespace(left=0, top=0, width=1000, height=800)
        with patch.object(tool, "Desktop", FakeDesktop):
            self.assertEqual(mon._uia_chat_input_centroid_click(window), (680, 730))

    def test_focus_verified_accepts_focused_edit_fallback(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)

        class FakeAutoGui:
            @staticmethod
            def click(_x: int, _y: int) -> None:
                return None

        window = SimpleNamespace(left=0, top=0, width=100, height=100)
        active = SimpleNamespace(left=0, top=0)
        fake_gw = SimpleNamespace(getActiveWindow=lambda: active)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch.object(tool, "gw", fake_gw),
            patch.object(tool, "Desktop", object()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_uia_point_is_chat_input", return_value=False),
            patch.object(mon, "_uia_focused_edit_looks_like_chat_input", return_value=True),
        ):
            self.assertTrue(mon._focus_verified_chat_input(window, (10, 10)))

    def test_focused_chat_input_accepts_document_control_type(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)

        class FakeRect:
            left = 100
            top = 700
            right = 900
            bottom = 760

        class FakeCtrl:
            element_info = SimpleNamespace(
                automation_id="",
                class_name="",
                control_type="Document",
            )

            @staticmethod
            def has_keyboard_focus() -> bool:
                return True

            @staticmethod
            def rectangle() -> FakeRect:
                return FakeRect()

            @staticmethod
            def window_text() -> str:
                return ""

        class FakeTarget:
            @staticmethod
            def exists(timeout: float = 0.0) -> bool:
                return True

            @staticmethod
            def descendants(control_type: str | None = None) -> list[object]:
                return [FakeCtrl()]

        class FakeDesktop:
            def __init__(self, backend: str = "uia") -> None:
                self.backend = backend

            @staticmethod
            def window(title_re: str | None = None) -> FakeTarget:
                return FakeTarget()

        window = SimpleNamespace(left=0, top=0, width=1200, height=900)
        with patch.object(tool, "Desktop", FakeDesktop):
            self.assertTrue(mon._uia_focused_edit_looks_like_chat_input(window))

    def test_point_chat_input_accepts_document_control_type(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)

        class FakeRect:
            left = 100
            top = 700
            right = 900
            bottom = 760

        class FakeCtrl:
            element_info = SimpleNamespace(
                automation_id="",
                class_name="",
                control_type="Document",
            )

            @staticmethod
            def rectangle() -> FakeRect:
                return FakeRect()

            @staticmethod
            def window_text() -> str:
                return ""

        class FakeTarget:
            @staticmethod
            def exists(timeout: float = 0.0) -> bool:
                return True

            @staticmethod
            def descendants(control_type: str | None = None) -> list[object]:
                return [FakeCtrl()]

        class FakeDesktop:
            def __init__(self, backend: str = "uia") -> None:
                self.backend = backend

            @staticmethod
            def window(title_re: str | None = None) -> FakeTarget:
                return FakeTarget()

        window = SimpleNamespace(left=0, top=0, width=1200, height=900)
        with patch.object(tool, "Desktop", FakeDesktop):
            self.assertTrue(mon._uia_point_is_chat_input(window, (500, 730)))

    def test_submit_uses_default_safe_click_when_no_target_found(self) -> None:
        cfg = tool.Config(
            prompt="x",
            post_submit_activity_wait_seconds=0.0,
        )
        mon = tool.PromptMonitor(cfg)

        class FakeAutoGui:
            @staticmethod
            def hotkey(*_keys: str) -> None:
                return None

            @staticmethod
            def press(_key: str) -> None:
                return None

        class FakeClip:
            @staticmethod
            def copy(_text: str) -> None:
                return None

        window = SimpleNamespace(activate=lambda: None, left=10, top=20, width=1000, height=800)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch.object(tool, "pyperclip", FakeClip()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_is_chat_active", return_value=False),
            patch.object(mon, "_autodetect_chat_input_click", return_value=None),
            patch.object(mon, "_probe_click_for_chat_input", return_value=None),
            patch.object(mon, "_focus_verified_chat_input", return_value=True) as focus_mock,
            patch.object(mon, "_pre_paste_guard", return_value=True),
        ):
            self.assertTrue(mon._submit_prompt(window))

        focus_mock.assert_called_once_with(window, (830, 756))

    def test_focused_chat_input_accepts_lower_pane_sparse_markers_geometry(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)

        class FakeRect:
            left = 100
            top = 700
            right = 500
            bottom = 920

        class FakeCtrl:
            element_info = SimpleNamespace(
                automation_id="",
                class_name="",
                control_type="Pane",
            )

            @staticmethod
            def has_keyboard_focus() -> bool:
                return True

            @staticmethod
            def rectangle() -> FakeRect:
                return FakeRect()

            @staticmethod
            def window_text() -> str:
                return ""

        class FakeTarget:
            @staticmethod
            def exists(timeout: float = 0.0) -> bool:
                return True

            @staticmethod
            def descendants(control_type: str | None = None) -> list[object]:
                return [FakeCtrl()]

        class FakeDesktop:
            def __init__(self, backend: str = "uia") -> None:
                self.backend = backend

            @staticmethod
            def window(title_re: str | None = None) -> FakeTarget:
                return FakeTarget()

        window = SimpleNamespace(left=0, top=0, width=1200, height=1000)
        with patch.object(tool, "Desktop", FakeDesktop):
            self.assertTrue(mon._uia_focused_edit_looks_like_chat_input(window))

    def test_active_window_match_accepts_title_regex(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)

        window = SimpleNamespace(left=0, top=0, width=1000, height=800)
        active = SimpleNamespace(left=300, top=200, width=500, height=400, title="Visual Studio Code")
        fake_gw = SimpleNamespace(getActiveWindow=lambda: active)

        with patch.object(tool, "gw", fake_gw):
            self.assertTrue(mon._is_active_window_match(window))

    def test_focus_verified_succeeds_when_point_uia_matches_even_if_active_mismatch(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)

        class FakeAutoGui:
            @staticmethod
            def click(_x: int, _y: int) -> None:
                return None

        window = SimpleNamespace(left=0, top=0, width=100, height=100)
        active = SimpleNamespace(left=999, top=999, width=100, height=100, title="Some Other App")
        fake_gw = SimpleNamespace(getActiveWindow=lambda: active)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch.object(tool, "gw", fake_gw),
            patch.object(tool, "Desktop", object()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_uia_point_is_chat_input", return_value=True),
        ):
            self.assertTrue(mon._focus_verified_chat_input(window, (10, 10)))

    def test_focus_verified_returns_false_on_failsafe_click(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)

        FailSafeException = type("FailSafeException", (Exception,), {})

        class FakeAutoGui:
            @staticmethod
            def click(_x: int, _y: int) -> None:
                raise FailSafeException("corner")

        window = SimpleNamespace(left=0, top=0, width=100, height=100)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch.object(tool, "Desktop", object()),
        ):
            self.assertFalse(mon._focus_verified_chat_input(window, (10, 10)))

    def test_focus_verified_accepts_safe_lower_focused_control_fallback(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)

        class FakeAutoGui:
            @staticmethod
            def click(_x: int, _y: int) -> None:
                return None

        window = SimpleNamespace(left=0, top=0, width=100, height=100)
        active = SimpleNamespace(left=0, top=0)
        fake_gw = SimpleNamespace(getActiveWindow=lambda: active)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch.object(tool, "gw", fake_gw),
            patch.object(tool, "Desktop", object()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_uia_point_is_chat_input", return_value=False),
            patch.object(mon, "_uia_focused_edit_looks_like_chat_input", return_value=False),
            patch.object(mon, "_uia_focused_control_looks_like_safe_lower_input", return_value=True),
        ):
            self.assertTrue(mon._focus_verified_chat_input(window, (10, 10)))

    def test_submit_uses_verified_hotkey_fallback_when_click_verification_fails(self) -> None:
        cfg = tool.Config(
            prompt="x",
            chat_focus_hotkey="ctrl+alt+i",
            allow_verified_hotkey_fallback=True,
            post_submit_activity_wait_seconds=0.0,
        )
        mon = tool.PromptMonitor(cfg)

        hotkey_calls: list[tuple[str, ...]] = []

        class FakeAutoGui:
            @staticmethod
            def hotkey(*keys: str) -> None:
                hotkey_calls.append(tuple(keys))

            @staticmethod
            def press(_key: str) -> None:
                return None

        class FakeClip:
            @staticmethod
            def copy(_text: str) -> None:
                return None

        window = SimpleNamespace(activate=lambda: None, left=0, top=0, width=100, height=100)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch.object(tool, "pyperclip", FakeClip()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_is_chat_active", return_value=False),
            patch.object(mon, "_focus_verified_chat_input", return_value=False),
            patch.object(mon, "_try_verified_hotkey_focus", return_value=True) as fallback_mock,
            patch.object(mon, "_pre_paste_guard", return_value=True),
        ):
            self.assertTrue(mon._submit_prompt(window))

        fallback_mock.assert_called_once()

    def test_submit_refuses_when_verified_hotkey_fallback_fails(self) -> None:
        cfg = tool.Config(
            prompt="x",
            post_submit_activity_wait_seconds=0.0,
        )
        mon = tool.PromptMonitor(cfg)

        class FakeAutoGui:
            @staticmethod
            def hotkey(*_keys: str) -> None:
                return None

            @staticmethod
            def press(_key: str) -> None:
                return None

        class FakeClip:
            @staticmethod
            def copy(_text: str) -> None:
                return None

        window = SimpleNamespace(activate=lambda: None, left=0, top=0, width=100, height=100)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch.object(tool, "pyperclip", FakeClip()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_is_chat_active", return_value=False),
            patch.object(mon, "_focus_verified_chat_input", return_value=False),
            patch.object(mon, "_try_verified_hotkey_focus", return_value=False),
        ):
            self.assertFalse(mon._submit_prompt(window))

    def test_focused_terminal_ancestor_blocks_pre_paste_guard(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)

        class FakeRect:
            left = 700
            top = 700
            right = 980
            bottom = 760

        class FakeParent:
            element_info = SimpleNamespace(automation_id="terminal", class_name="xterm", control_type="Pane")

            @staticmethod
            def window_text() -> str:
                return "Integrated Terminal"

            @staticmethod
            def parent() -> object | None:
                return None

        class FakeCtrl:
            element_info = SimpleNamespace(automation_id="chatInput", class_name="editor", control_type="Edit")

            @staticmethod
            def has_keyboard_focus() -> bool:
                return True

            @staticmethod
            def rectangle() -> FakeRect:
                return FakeRect()

            @staticmethod
            def window_text() -> str:
                return "Ask Copilot"

            @staticmethod
            def parent() -> FakeParent:
                return FakeParent()

        class FakeTarget:
            @staticmethod
            def exists(timeout: float = 0.0) -> bool:
                return True

            @staticmethod
            def descendants(control_type: str | None = None) -> list[object]:
                return [FakeCtrl()]

        class FakeDesktop:
            def __init__(self, backend: str = "uia") -> None:
                self.backend = backend

            @staticmethod
            def window(title_re: str | None = None) -> FakeTarget:
                return FakeTarget()

        window = SimpleNamespace(left=0, top=0, width=1000, height=800)
        with patch.object(tool, "Desktop", FakeDesktop):
            verdict, reason = mon._focused_target_is_safe_chat_input(window)

        self.assertFalse(verdict)
        self.assertIn("terminal_ancestor_or_focus", reason)

    def test_verified_click_allows_narrow_focused_chat_child_geometry(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)

        class FakeRect:
            left = 900
            top = 700
            right = 940
            bottom = 760

        class FakeCtrl:
            element_info = SimpleNamespace(automation_id="", class_name="editor", control_type="Edit")

            @staticmethod
            def has_keyboard_focus() -> bool:
                return True

            @staticmethod
            def rectangle() -> FakeRect:
                return FakeRect()

            @staticmethod
            def window_text() -> str:
                return ""

            @staticmethod
            def parent() -> object | None:
                return None

        class FakeTarget:
            @staticmethod
            def exists(timeout: float = 0.0) -> bool:
                return True

            @staticmethod
            def descendants(control_type: str | None = None) -> list[object]:
                return [FakeCtrl()]

        class FakeDesktop:
            def __init__(self, backend: str = "uia") -> None:
                self.backend = backend

            @staticmethod
            def window(title_re: str | None = None) -> FakeTarget:
                return FakeTarget()

        window = SimpleNamespace(left=0, top=0, width=1000, height=800)
        with (
            patch.object(tool, "Desktop", FakeDesktop),
            patch.object(mon, "_uia_point_is_chat_input", return_value=True),
        ):
            verdict, reason = mon._focused_target_is_safe_chat_input(window, (830, 756))

        self.assertTrue(verdict)
        self.assertIn("focused_safe_verified_click", reason)

    def test_submit_blocks_when_focus_changes_after_clear(self) -> None:
        cfg = tool.Config(
            prompt="x",
            input_click_x=830,
            input_click_y=756,
            post_submit_activity_wait_seconds=0.0,
        )
        mon = tool.PromptMonitor(cfg)

        hotkey_calls: list[tuple[str, ...]] = []

        class FakeAutoGui:
            @staticmethod
            def hotkey(*keys: str) -> None:
                hotkey_calls.append(tuple(keys))

            @staticmethod
            def press(_key: str) -> None:
                return None

        class FakeClip:
            @staticmethod
            def copy(_text: str) -> None:
                raise AssertionError("paste should not occur after focus jump")

        window = SimpleNamespace(activate=lambda: None, left=10, top=20, width=1000, height=800)
        guard_results = [True, False]

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch.object(tool, "pyperclip", FakeClip()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_is_chat_active", return_value=False),
            patch.object(mon, "_focus_verified_chat_input", return_value=True),
            patch.object(mon, "_pre_paste_guard", side_effect=guard_results),
        ):
            self.assertFalse(mon._submit_prompt(window))

        self.assertIn(("ctrl", "a"), hotkey_calls)
        self.assertNotIn(("ctrl", "v"), hotkey_calls)

    def test_all_fallbacks_exhausted_skip_without_paste_side_effects(self) -> None:
        cfg = tool.Config(prompt="x", post_submit_activity_wait_seconds=0.0)
        mon = tool.PromptMonitor(cfg)

        hotkey_calls: list[tuple[str, ...]] = []

        class FakeAutoGui:
            @staticmethod
            def hotkey(*keys: str) -> None:
                hotkey_calls.append(tuple(keys))

            @staticmethod
            def press(_key: str) -> None:
                return None

        class FakeClip:
            @staticmethod
            def copy(_text: str) -> None:
                raise AssertionError("paste should not occur")

        window = SimpleNamespace(activate=lambda: None, left=0, top=0, width=1000, height=800)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch.object(tool, "pyperclip", FakeClip()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_is_chat_active", return_value=False),
            patch.object(mon, "_autodetect_chat_input_click", return_value=None),
            patch.object(mon, "_probe_click_for_chat_input", return_value=None),
            patch.object(mon, "_focus_verified_chat_input", return_value=False),
            patch.object(mon, "_try_verified_hotkey_focus", return_value=False),
        ):
            self.assertFalse(mon._submit_prompt(window))

        self.assertNotIn(("ctrl", "v"), hotkey_calls)

    def test_hard_lock_chat_zone_helper(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)
        window = SimpleNamespace(left=100, top=200, width=1000, height=800)

        self.assertTrue(mon._is_hard_lock_chat_zone(window, (820, 840)))
        self.assertFalse(mon._is_hard_lock_chat_zone(window, (500, 840)))

    def test_default_safe_click_adapts_for_squished_window(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)
        window = SimpleNamespace(left=100, top=200, width=800, height=600)

        self.assertEqual(mon._default_safe_input_click(window), (644, 716))

    def test_hard_lock_chat_zone_relaxes_for_squished_window(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)
        window = SimpleNamespace(left=100, top=200, width=800, height=600)

        self.assertTrue(mon._is_hard_lock_chat_zone(window, (564, 584)))
        self.assertFalse(mon._is_hard_lock_chat_zone(window, (420, 584)))

    def test_focus_click_candidates_cover_squished_vertical_hitbox(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)
        window = SimpleNamespace(left=100, top=200, width=800, height=600)

        self.assertEqual(
            mon._focus_click_candidates(window, (644, 716)),
            (
                (644, 668),
                (548, 668),
                (724, 668),
                (596, 668),
                (692, 668),
                (644, 692),
                (548, 692),
                (724, 692),
                (596, 692),
                (692, 692),
                (644, 716),
                (548, 716),
                (724, 716),
                (596, 716),
                (692, 716),
                (644, 644),
                (548, 644),
                (724, 644),
                (596, 644),
                (692, 644),
            ),
        )

    def test_focus_click_candidates_filter_outside_squished_safe_band(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)
        window = SimpleNamespace(left=100, top=200, width=800, height=600)

        candidates = mon._focus_click_candidates(window, (520, 716))

        self.assertNotIn((424, 668), candidates)
        self.assertIn((520, 668), candidates)

    def test_focus_click_candidates_large_window_try_target_before_upward_offset(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)
        window = SimpleNamespace(left=100, top=200, width=1000, height=800)

        self.assertEqual(
            mon._focus_click_candidates(window, (820, 840)),
            (
                (820, 840),
                (820, 808),
                (820, 776),
            ),
        )

    def test_focus_verified_tries_next_candidate_when_first_misses(self) -> None:
        cfg = tool.Config(prompt="x")
        mon = tool.PromptMonitor(cfg)

        clicks: list[tuple[int, int]] = []

        class FakeAutoGui:
            @staticmethod
            def click(x: int, y: int) -> None:
                clicks.append((x, y))

        window = SimpleNamespace(left=100, top=200, width=800, height=600)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch.object(tool, "Desktop", object()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_uia_point_is_chat_input", side_effect=[False, False, False, True]),
        ):
            self.assertTrue(mon._focus_verified_chat_input(window, (644, 716)))

        self.assertEqual(clicks, [(644, 668), (548, 668)])

    def test_focus_verified_blocks_hard_lock_zone_without_uia_proof(self) -> None:
        cfg = tool.Config(prompt="x", allow_force_submit_in_hard_lock_zone=True)
        mon = tool.PromptMonitor(cfg)

        class FakeAutoGui:
            @staticmethod
            def click(_x: int, _y: int) -> None:
                return None

        window = SimpleNamespace(left=0, top=0, width=1000, height=800)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch.object(tool, "Desktop", object()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_uia_point_is_chat_input", return_value=False),
            patch.object(mon, "_hard_lock_above_click", return_value=(820, 736)),
            patch.object(mon, "_is_hard_lock_chat_zone", return_value=True),
            patch.object(mon, "_uia_focused_edit_looks_like_chat_input", return_value=False),
            patch.object(mon, "_uia_focused_control_looks_like_safe_lower_input", return_value=False),
        ):
            self.assertFalse(mon._focus_verified_chat_input(window, (820, 768)))


if __name__ == "__main__":
    unittest.main()
