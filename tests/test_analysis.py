import unittest

import numpy as np

from fdem_analysis import analyze_fdem


class AnalysisTests(unittest.TestCase):
    def test_recovers_transfer_amplitude_and_phase(self):
        frequency = 1000.0
        sample_rate = 100_000.0
        count = 1000
        pre = 100
        phase = 0.4
        k = np.arange(count)
        current = np.concatenate((np.zeros(pre), 2.0 * np.cos(2 * np.pi * frequency * k / sample_rate)))
        rx = np.concatenate((np.zeros(pre), 0.5 * np.cos(2 * np.pi * frequency * k / sample_rate + phase)))
        result = analyze_fdem(rx, current, {
            "sample_rate": sample_rate,
            "frequency_hz": frequency,
            "pre_samples": pre,
            "sine_samples": count,
            "shunt_resistance_ohm": 1.0,
            "atten": 1.0,
        })
        self.assertAlmostEqual(result["current_amplitude_apk"], 2.0, places=12)
        self.assertAlmostEqual(result["rx_amplitude_vpk"], 0.5, places=12)
        self.assertAlmostEqual(result["transfer_magnitude"], 0.25, places=12)
        self.assertAlmostEqual(result["transfer_phase_deg"], np.degrees(phase), places=12)

    def test_converts_ai29_shunt_voltage_to_current(self):
        frequency = 1000.0
        sample_rate = 100_000.0
        count = 1000
        phase = 2 * np.pi * frequency * np.arange(count) / sample_rate
        result = analyze_fdem(np.sin(phase), 0.2 * np.sin(phase), {
            "sample_rate": sample_rate,
            "frequency_hz": frequency,
            "pre_samples": 0,
            "sine_samples": count,
            "shunt_resistance_ohm": 10.0,
            "atten": 100.0,
        })
        self.assertAlmostEqual(result["current_amplitude_apk"], 2.0, places=12)
        self.assertAlmostEqual(result["transfer_magnitude"], 0.5, places=12)

    def test_default_500x_probe_and_r47_shunt_conversion(self):
        frequency = 1000.0
        sample_rate = 100_000.0
        count = 1000
        phase = 2 * np.pi * frequency * np.arange(count) / sample_rate
        result = analyze_fdem(np.sin(phase), 1e-3 * np.sin(phase), {
            "sample_rate": sample_rate,
            "frequency_hz": frequency,
            "pre_samples": 0,
            "sine_samples": count,
        })
        self.assertAlmostEqual(result["current_amplitude_apk"], 0.5 / 0.47, places=12)

    def test_rejects_transfer_when_current_is_below_threshold(self):
        count = 1000
        params = {
            "sample_rate": 100_000.0,
            "frequency_hz": 1000.0,
            "pre_samples": 0,
            "sine_samples": count,
            "shunt_resistance_ohm": 1.0,
            "atten": 1.0,
        }
        phase = 2 * np.pi * params["frequency_hz"] * np.arange(count) / params["sample_rate"]
        result = analyze_fdem(np.sin(phase), 1e-9 * np.sin(phase), params)
        self.assertIsNone(result["transfer_magnitude"])


if __name__ == "__main__":
    unittest.main()
