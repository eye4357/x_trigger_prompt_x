import unittest
from types import SimpleNamespace
from unittest.mock import patch

import calibrate_trigger_profile as calibrator


class CalibratorHelperTests(unittest.TestCase):
    def test_clamp(self) -> None:
        self.assertEqual(calibrator.clamp(5, 0, 10), 5)
        self.assertEqual(calibrator.clamp(-1, 0, 10), 0)
        self.assertEqual(calibrator.clamp(11, 0, 10), 10)

    def test_window_region_normalizes_values(self) -> None:
        window = SimpleNamespace(left=-10, top=-20, width=0, height=0)
        self.assertEqual(calibrator.window_region(window), (0, 0, 1, 1))

    def test_find_vscode_window_prefers_active(self) -> None:
        active = SimpleNamespace(title="Visual Studio Code", width=100, height=100)
        largest = SimpleNamespace(title="Visual Studio Code", width=200, height=200)

        fake_gw = SimpleNamespace(
            getAllWindows=lambda: [active, largest],
            getActiveWindow=lambda: active,
        )

        with patch.object(calibrator, "gw", fake_gw):
            found = calibrator.find_vscode_window(r".*Visual Studio Code.*")

        self.assertIs(found, active)

    def test_version_flag_exits_cleanly(self) -> None:
        with patch("sys.argv", ["calibrate_trigger_profile.py", "--version"]), self.assertRaises(SystemExit) as ctx:
            calibrator.main()
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
