import argparse
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import auto_trigger_copilot_chat as tool


class ParseArgsTests(unittest.TestCase):
    def test_version_flag_exits_cleanly(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            tool.parse_args(["--version"])
        self.assertEqual(ctx.exception.code, 0)

    def test_prompt_or_prompt_file_required(self) -> None:
        with self.assertRaises(SystemExit):
            tool.parse_args([])

    def test_max_prompts_range_guard(self) -> None:
        with self.assertRaises(SystemExit):
            tool.parse_args(["--prompt", "hi", "--max-prompts", "0"])
        with self.assertRaises(SystemExit):
            tool.parse_args(["--prompt", "hi", "--max-prompts", "513"])

    def test_prompt_file_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            prompt_path = Path(tmp_dir) / "prompt.txt"
            prompt_path.write_text("hello world\n", encoding="utf-8")

            cfg = tool.parse_args(["--prompt-file", str(prompt_path)])
            self.assertEqual(cfg.prompt, "hello world")

    def test_prompt_file_marker_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            prompt_path = Path(tmp_dir) / "prompts.md"
            prompt_path.write_text(
                "header\nPrompt a1\nline1\nline2\nEND A1\nfooter\n",
                encoding="utf-8",
            )

            cfg = tool.parse_args(
                [
                    "--prompt-file",
                    str(prompt_path),
                    "--prompt-start-marker",
                    "Prompt a1",
                    "--prompt-end-marker",
                    "END A1",
                ]
            )
            self.assertEqual(cfg.prompt, "Prompt a1\nline1\nline2\nEND A1")

    def test_prompt_markers_require_prompt_file(self) -> None:
        with self.assertRaises(SystemExit):
            tool.parse_args(["--prompt", "hello", "--prompt-start-marker", "Prompt a1"])

    def test_profile_supplies_ratio_and_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            prompt_path = root / "prompt.txt"
            prompt_path.write_text("hello", encoding="utf-8")
            template = root / "stop.png"
            template.write_bytes(b"not-a-real-image")
            profile = root / "profile.json"
            profile.write_text(
                json.dumps(
                    {
                        "stop_template": "stop.png",
                        "input_click_x_ratio": 0.5,
                        "input_click_y_ratio": 0.75,
                    }
                ),
                encoding="utf-8",
            )

            cfg = tool.parse_args(
                [
                    "--prompt-file",
                    str(prompt_path),
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

        with patch.object(mon, "_uia_detect_halt_keyword", return_value=True):
            self.assertFalse(mon._should_halt(SimpleNamespace()))

    def test_should_halt_runs_after_first_submit(self) -> None:
        cfg = tool.Config(prompt="x", halt_keyword="HALT NOW", disable_halt_keyword_scan=False)
        mon = tool.PromptMonitor(cfg)
        mon._submitted = 1

        with patch.object(tool, "Desktop", object()):
            with patch.object(mon, "_uia_detect_halt_keyword", return_value=True):
                self.assertTrue(mon._should_halt(SimpleNamespace()))


if __name__ == "__main__":
    unittest.main()
