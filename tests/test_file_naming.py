import tempfile
import unittest
from pathlib import Path

from ui.main_window import MainWindow


class FileNamingTests(unittest.TestCase):
    def test_frequency_tag(self):
        self.assertEqual(MainWindow._frequency_tag(1000.0), "1000Hz")
        self.assertEqual(MainWindow._frequency_tag(12.5), "12p5Hz")
        self.assertEqual(MainWindow._frequency_tag(1.2345678), "1p234568Hz")

    def test_numbering_supports_old_and_frequency_names(self):
        with tempfile.TemporaryDirectory() as directory:
            point_dir = Path(directory)
            (point_dir / "测点1_002_rx.npy").touch()
            (point_dir / "测点1_004_1000Hz_rx.npy").touch()
            self.assertEqual(MainWindow._next_measurement_number(point_dir), 5)

    def test_resolves_old_and_frequency_names(self):
        with tempfile.TemporaryDirectory() as directory:
            point_dir = Path(directory)
            old = point_dir / "测点1_002"
            Path(f"{old}_rx.npy").touch()
            new = point_dir / "测点1_003_12p5Hz"
            Path(f"{new}_rx.npy").touch()
            self.assertEqual(MainWindow._measurement_prefix(point_dir, "测点1", 2), old)
            self.assertEqual(MainWindow._measurement_prefix(point_dir, "测点1", 3), new)


if __name__ == "__main__":
    unittest.main()
