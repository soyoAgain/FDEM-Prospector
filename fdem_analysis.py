"""Frequency-domain measurements for coherent FDEM records."""

from __future__ import annotations

import numpy as np

from config import (
    DEFAULT_AI29_ATTENUATION,
    DEFAULT_AI29_SHUNT_RESISTANCE_OHM,
    MIN_TRANSMIT_CURRENT_APK,
)


def complex_amplitude(signal, sample_rate: float, frequency_hz: float, start: int, count: int) -> complex:
    """Return the peak complex amplitude at one frequency over a coherent segment."""
    values = np.asarray(signal, dtype=np.float64)
    if start < 0 or count <= 0 or start + count > values.size:
        raise ValueError("Invalid analysis segment")
    segment = values[start:start + count]
    phase = np.exp(-2j * np.pi * frequency_hz * np.arange(count) / sample_rate)
    return complex(2.0 * np.dot(segment, phase) / count)


def analyze_fdem(data_rx, data_i, params: dict) -> dict:
    """Measure coherent fundamental amplitudes and relative phase."""
    sample_rate = float(params["sample_rate"])
    frequency_hz = float(params["frequency_hz"])
    start = int(params["pre_samples"])
    count = int(params["sine_samples"])

    rx = complex_amplitude(data_rx, sample_rate, frequency_hz, start, count)
    shunt_resistance = float(
        params.get("shunt_resistance_ohm", DEFAULT_AI29_SHUNT_RESISTANCE_OHM)
    )
    if not np.isfinite(shunt_resistance) or shunt_resistance <= 0:
        raise ValueError("ai29 shunt resistance must be finite and positive")
    attenuation = float(params.get("atten", DEFAULT_AI29_ATTENUATION))
    if not np.isfinite(attenuation) or attenuation <= 0:
        raise ValueError("ai29 attenuation must be finite and positive")
    current = (
        complex_amplitude(data_i, sample_rate, frequency_hz, start, count)
        * attenuation
        / shunt_resistance
    )
    result = {
        "frequency_hz": frequency_hz,
        "rx_amplitude_vpk": abs(rx),
        "rx_phase_deg": float(np.degrees(np.angle(rx))),
        "current_amplitude_apk": abs(current),
        "current_phase_deg": float(np.degrees(np.angle(current))),
        "ai29_shunt_resistance_ohm": shunt_resistance,
        "ai29_attenuation": attenuation,
    }
    if abs(current) >= MIN_TRANSMIT_CURRENT_APK:
        transfer = rx / current
        result.update({
            "transfer_magnitude": abs(transfer),
            "transfer_phase_deg": float(np.degrees(np.angle(transfer))),
            "transfer_in_phase": float(transfer.real),
            "transfer_quadrature": float(transfer.imag),
        })
    else:
        result.update({
            "transfer_magnitude": None,
            "transfer_phase_deg": None,
            "transfer_in_phase": None,
            "transfer_quadrature": None,
        })
    return result
