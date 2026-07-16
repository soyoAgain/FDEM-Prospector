import unittest

import matplotlib

from font_config import configure_matplotlib_fonts


class FontConfigTests(unittest.TestCase):
    def test_configures_an_installed_font(self):
        family = configure_matplotlib_fonts()
        self.assertTrue(family)
        self.assertIn(family, matplotlib.rcParams["font.sans-serif"])
        self.assertFalse(matplotlib.rcParams["axes.unicode_minus"])


if __name__ == "__main__":
    unittest.main()
