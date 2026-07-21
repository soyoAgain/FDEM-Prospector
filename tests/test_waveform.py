import unittest

import numpy as np

from fdem_acquisition import build_external_trigger, build_fdem_waveform


class WaveformTests(unittest.TestCase):
    def test_external_trigger_is_4v_for_10ms_then_zero(self):
        trigger = build_external_trigger()
        self.assertEqual(trigger.size, 101)
        self.assertTrue(np.all(trigger[:100] == 4.0))
        self.assertEqual(trigger[-1], 0.0)

    def test_vpp_waveform_is_complete_and_zero_mean(self):
        wave, params = build_fdem_waveform(1000.0, 7, 3.3, "Vpp", 100, 2.0, 3.0)
        start = params["pre_samples"]
        stop = start + params["sine_samples"]
        self.assertEqual(params["sine_samples"], 700)
        self.assertAlmostEqual(np.max(wave[start:stop]), 1.65)
        self.assertAlmostEqual(float(np.mean(wave[start:stop])), 0.0, places=14)
        self.assertEqual(wave[start], 0.0)
        self.assertEqual(wave[stop], 0.0)
        self.assertTrue(np.all(wave[:start] == 0.0))
        self.assertTrue(np.all(wave[stop:] == 0.0))

    def test_vpk_interpretation(self):
        wave, params = build_fdem_waveform(50.0, 1, 3.3, "Vpk", 100)
        self.assertAlmostEqual(params["peak_amplitude_v"], 3.3)
        self.assertAlmostEqual(np.max(wave), 3.3)

    def test_rejects_invalid_parameters(self):
        invalid = [
            (1000.0, 0, 3.3, "Vpp", 100),
            (1000.0, 1.5, 3.3, "Vpp", 100),
            (0.0, 1, 3.3, "Vpp", 100),
            (1000.0, 1, 0.0, "Vpp", 100),
            (1000.0, 1, 3.3, "unknown", 100),
            (1000.0, 1, 3.3, "Vpp", 3),
        ]
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ValueError):
                build_fdem_waveform(*args)

    def test_rejects_excessive_duration_or_allocation(self):
        with self.assertRaises(ValueError):
            build_fdem_waveform(1.0, 1000, 3.3, "Vpp", 100)
        with self.assertRaises(ValueError):
            build_fdem_waveform(1.0, 1, 3.3, "Vpp", 500_000, 10_000.0, 0.0)


if __name__ == "__main__":
    unittest.main()
