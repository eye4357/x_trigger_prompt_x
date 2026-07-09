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
            element_info = SimpleNamespace(automation_id="terminalInput", class_name="xterm")

            @staticmethod
            def rectangle() -> FakeRect:
                return FakeRect()

            @staticmethod
            def window_text() -> str:
                return "Terminal"

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
            self.assertIsNone(mon._autodetect_chat_input_click(window))

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
            input_click_x=100,
            input_click_y=200,
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
            patch.object(mon, "_focus_verified_chat_input", return_value=True),
        ):
            self.assertTrue(mon._submit_prompt(window))

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

        window = SimpleNamespace(activate=lambda: None, left=0, top=0, width=100, height=100)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch.object(tool, "pyperclip", FakeClip()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_is_chat_active", return_value=False),
            patch.object(mon, "_autodetect_chat_input_click", return_value=(100, 200)),
            patch.object(mon, "_focus_verified_chat_input", return_value=True),
        ):
            self.assertTrue(mon._submit_prompt(window))

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

        window = SimpleNamespace(activate=lambda: None, left=0, top=0, width=100, height=100)

        with (
            patch.object(tool, "pyautogui", FakeAutoGui()),
            patch.object(tool, "pyperclip", FakeClip()),
            patch("x_trigger_prompt_x.time.sleep", return_value=None),
            patch.object(mon, "_is_chat_active", return_value=False),
            patch.object(mon, "_autodetect_chat_input_click", return_value=None),
            patch.object(mon, "_probe_click_for_chat_input", return_value=(100, 200)),
            patch.object(mon, "_focus_verified_chat_input", return_value=True),
        ):
            self.assertTrue(mon._submit_prompt(window))


if __name__ == "__main__":
    unittest.main()
