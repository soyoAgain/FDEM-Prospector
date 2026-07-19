"""FDEM PXI acquisition program, invoked remotely by the Mac GUI.

The Signal in channel is safety critical: it must only receive a validated,
zero-offset, finite-duration sine wave. Never replace it with TEM step logic.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime

import numpy as np

from config import (
    AMPLITUDE_MODES,
    CH_AI_CURRENT,
    CH_RX,
    CH_SIGNAL_IN,
    CH_START,
    DEV_RX,
    DEV_TX,
    MAX_AO_ABS_V,
    MAX_SAMPLE_RATE,
    MAX_ACQUISITION_DURATION_S,
    MAX_CYCLES,
    MAX_FREQUENCY_HZ,
    MAX_TOTAL_SAMPLES,
    MIN_FREQUENCY_HZ,
    WAVEFORM_ENDPOINT_ATOL_V,
    WAVEFORM_MEAN_ATOL_V,
)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def amplitude_to_peak(amplitude_v: float, amplitude_mode: str) -> float:
    if amplitude_mode not in AMPLITUDE_MODES:
        raise ValueError(f"Amplitude mode must be one of {AMPLITUDE_MODES}")
    if not np.isfinite(amplitude_v) or amplitude_v <= 0:
        raise ValueError("Amplitude must be finite and positive")
    return amplitude_v if amplitude_mode == "Vpk" else amplitude_v / 2.0


def build_fdem_waveform(
    frequency_hz: float,
    cycles: int,
    amplitude_v: float,
    amplitude_mode: str,
    samples_per_cycle: int,
    pre_acq_ms: float = 0.0,
    post_acq_ms: float = 0.0,
) -> tuple[np.ndarray, dict]:
    """Build zero pre-roll, complete sine cycles, explicit zero, and post-roll."""
    if isinstance(cycles, bool) or not isinstance(cycles, (int, np.integer)):
        raise ValueError("Cycle count must be an integer")
    if not 1 <= cycles <= MAX_CYCLES:
        raise ValueError(f"Cycle count must be in 1..{MAX_CYCLES}")
    if not np.isfinite(frequency_hz) or not MIN_FREQUENCY_HZ <= frequency_hz <= MAX_FREQUENCY_HZ:
        raise ValueError(f"Frequency must be in {MIN_FREQUENCY_HZ:g}..{MAX_FREQUENCY_HZ:g} Hz")
    if isinstance(samples_per_cycle, bool) or not isinstance(samples_per_cycle, (int, np.integer)):
        raise ValueError("Samples per cycle must be an integer")
    if samples_per_cycle < 4:
        raise ValueError("Samples per cycle must be at least 4")
    if not np.isfinite(pre_acq_ms) or not np.isfinite(post_acq_ms):
        raise ValueError("Pre/post acquisition times must be finite")
    if pre_acq_ms < 0 or post_acq_ms < 0:
        raise ValueError("Pre/post acquisition times cannot be negative")

    peak_v = amplitude_to_peak(float(amplitude_v), amplitude_mode)
    if peak_v > MAX_AO_ABS_V:
        raise ValueError(f"Peak amplitude exceeds AO limit ({MAX_AO_ABS_V:g} V)")
    sample_rate = float(frequency_hz) * int(samples_per_cycle)
    if sample_rate > MAX_SAMPLE_RATE:
        raise ValueError(
            f"Sample rate {sample_rate:g} S/s exceeds AI/AO limit {MAX_SAMPLE_RATE:g} S/s"
        )

    sine_samples = int(cycles) * int(samples_per_cycle)
    pre_samples = int(round(pre_acq_ms / 1000.0 * sample_rate))
    post_samples = int(round(post_acq_ms / 1000.0 * sample_rate))
    total_samples = pre_samples + sine_samples + post_samples + 1
    duration_s = total_samples / sample_rate
    if total_samples > MAX_TOTAL_SAMPLES:
        raise ValueError(f"Total samples exceed limit ({MAX_TOTAL_SAMPLES})")
    if duration_s > MAX_ACQUISITION_DURATION_S:
        raise ValueError(
            f"Acquisition duration exceeds limit ({MAX_ACQUISITION_DURATION_S:g} s)"
        )
    phase = 2.0 * np.pi * np.arange(sine_samples, dtype=np.float64) / samples_per_cycle
    sine = peak_v * np.sin(phase)
    # The extra zero is the requested value at t=n/f, not a replacement for
    # measuring the physical output after task close or device reset.
    waveform = np.concatenate((np.zeros(pre_samples), sine, np.zeros(post_samples + 1)))
    params = {
        "frequency_hz": float(frequency_hz),
        "cycles": int(cycles),
        "amplitude_v": float(amplitude_v),
        "amplitude_mode": amplitude_mode,
        "peak_amplitude_v": float(peak_v),
        "samples_per_cycle": int(samples_per_cycle),
        "sample_rate": sample_rate,
        "pre_acq_ms": float(pre_acq_ms),
        "post_acq_ms": float(post_acq_ms),
        "pre_samples": pre_samples,
        "sine_samples": sine_samples,
        "post_samples": post_samples,
        "total_samples": total_samples,
        "transmit_duration_ms": 1000.0 * cycles / frequency_hz,
    }
    validate_fdem_waveform(waveform, params)
    return waveform, params


def validate_fdem_waveform(waveform, params: dict) -> None:
    values = np.asarray(waveform, dtype=np.float64)
    if values.ndim != 1 or values.size != int(params["total_samples"]):
        raise ValueError("Waveform sample count mismatch")
    if not np.all(np.isfinite(values)):
        raise ValueError("Waveform contains NaN or infinity")
    if np.max(np.abs(values)) > MAX_AO_ABS_V:
        raise ValueError("Waveform exceeds AO range")
    start = int(params["pre_samples"])
    stop = start + int(params["sine_samples"])
    sine = values[start:stop]
    if sine.size == 0 or abs(float(np.mean(sine))) > WAVEFORM_MEAN_ATOL_V:
        raise ValueError("Waveform has a non-zero digital mean")
    if abs(values[start]) > WAVEFORM_ENDPOINT_ATOL_V or abs(values[stop]) > WAVEFORM_ENDPOINT_ATOL_V:
        raise ValueError("Waveform does not start and end at zero")
    if np.any(values[:start] != 0.0) or np.any(values[stop:] != 0.0):
        raise ValueError("Pre/post acquisition region must be exactly zero")


def set_start_level(enable: bool) -> None:
    """Send the one-shot Start pulse: 0 V -> 4 V for 500 ms -> 0 V.

    The amplifier starts charging when ao1 is high. The final explicit zero
    sample is required because a finite AO task otherwise holds its last
    sample, leaving ao1 at 4 V after the task completes.
    """
    import nidaqmx
    from nidaqmx.constants import AcquisitionType

    task = nidaqmx.Task("FDEM_Start")
    try:
        task.ao_channels.add_ao_voltage_chan(f"{DEV_TX}/{CH_START}", min_val=0.0, max_val=10.0)
        if enable:
            # 1 ms low establishes the idle state, 500 ms high starts charging,
            # and the final zero sample explicitly returns ao1 to idle.
            signal = np.concatenate((np.zeros(10), np.full(5000, 4.0), np.zeros(1)))
        else:
            signal = np.zeros(1)
        task.timing.cfg_samp_clk_timing(
            rate=10_000,
            sample_mode=AcquisitionType.FINITE,
            samps_per_chan=signal.size,
        )
        task.write(signal, auto_start=True)
        task.wait_until_done(timeout=2)
    finally:
        task.close()
    print("START_PULSE_OK" if enable else "START_DISABLED")


def fdem_transmit_and_acquire(waveform: np.ndarray, params: dict):
    """Acquire ai31 and ai29 while ao0 plays the validated sine waveform.

    AI and AO use their own internal clocks (software-ordered start, same as
    TEM). PXI_Trig0 hardware clock routing is NOT used here because chassis
    route availability has not been verified for this slot pair.  A shared
    hardware clock or PXI trigger can be added later once routing capability
    is confirmed with NI MAX on the target chassis.
    """
    import nidaqmx
    from nidaqmx.constants import AcquisitionType

    validate_fdem_waveform(waveform, params)
    sample_rate = float(params["sample_rate"])
    total_samples = int(params["total_samples"])
    timeout = total_samples / sample_rate + 5.0
    ao_task = nidaqmx.Task("FDEM_SignalIn")
    ai_task = nidaqmx.Task("FDEM_RxAcq")
    try:
        ao_task.ao_channels.add_ao_voltage_chan(
            f"{DEV_TX}/{CH_SIGNAL_IN}", min_val=-MAX_AO_ABS_V, max_val=MAX_AO_ABS_V
        )
        ao_task.timing.cfg_samp_clk_timing(
            rate=sample_rate,
            sample_mode=AcquisitionType.FINITE,
            samps_per_chan=total_samples,
        )
        ai_task.ai_channels.add_ai_voltage_chan(f"{DEV_RX}/{CH_RX}", min_val=-10.0, max_val=10.0)
        ai_task.ai_channels.add_ai_voltage_chan(
            f"{DEV_RX}/{CH_AI_CURRENT}", min_val=-10.0, max_val=10.0
        )
        ai_task.timing.cfg_samp_clk_timing(
            rate=sample_rate,
            sample_mode=AcquisitionType.FINITE,
            samps_per_chan=total_samples,
        )
        ao_task.write(waveform, auto_start=False)
        # AI starts first so it is ready before the AO trigger edge (same
        # software-ordered approach used in TEM).
        ai_task.start()
        ao_task.start()
        ao_task.wait_until_done(timeout=timeout)
        ai_task.wait_until_done(timeout=timeout)
        data = np.asarray(
            ai_task.read(number_of_samples_per_channel=total_samples, timeout=5),
            dtype=np.float64,
        )
        if data.shape != (2, total_samples):
            raise RuntimeError(f"Unexpected AI data shape: {data.shape}")
    finally:
        # Closing/resetting is not treated as IGBT protection. Physical ao0
        # behavior in every failure state must be verified with a DC scope.
        try:
            ao_task.close()
        finally:
            ai_task.close()
    t = np.arange(total_samples, dtype=np.float64) / sample_rate
    return t, data[0], data[1]


def save_result(t, data_rx, data_i, params: dict) -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")
    prefix = os.path.join(DATA_DIR, f"fdem_{timestamp}")
    np.save(f"{prefix}_t.npy", t)
    np.save(f"{prefix}_rx.npy", data_rx)
    np.save(f"{prefix}_current.npy", data_i)
    with open(f"{prefix}_info.json", "w", encoding="utf-8") as handle:
        json.dump(params, handle, indent=2, ensure_ascii=False)
    print(f"DATA_SAVED:{prefix}")
    return prefix


def monitor_ai31(sample_rate: float = 10_000.0, chunk_samples: int = 500) -> None:
    """Continuously read ai31 and print base64-encoded chunks to stdout.

    Each line of output is either:
        MONITOR_READY        - acquisition started
        D:<base64>           - float64 array of chunk_samples voltages
        MONITOR_ERROR:<msg>  - fatal error
    """
    import base64

    import nidaqmx
    from nidaqmx.constants import AcquisitionType

    task = nidaqmx.Task("FDEM_Monitor")
    try:
        task.ai_channels.add_ai_voltage_chan(
            f"{DEV_RX}/{CH_RX}", min_val=-10.0, max_val=10.0
        )
        task.timing.cfg_samp_clk_timing(
            rate=sample_rate,
            sample_mode=AcquisitionType.CONTINUOUS,
        )
        task.start()
        print("MONITOR_READY", flush=True)
        while True:
            data = np.array(
                task.read(number_of_samples_per_channel=chunk_samples, timeout=10.0),
                dtype=np.float64,
            )
            encoded = base64.b64encode(data.tobytes()).decode("ascii")
            print(f"D:{encoded}", flush=True)
    except Exception as exc:
        print(f"MONITOR_ERROR:{exc}", flush=True)
    finally:
        task.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=("start-enable", "start-disable", "transmit", "monitor"),
    )
    parser.add_argument("--frequency-hz", type=float, default=1000.0)
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--amplitude-v", type=float, default=3.3)
    parser.add_argument("--amplitude-mode", choices=AMPLITUDE_MODES)
    parser.add_argument("--samples-per-cycle", type=int, default=250)
    parser.add_argument("--pre-acq-ms", type=float, default=10.0)
    parser.add_argument("--post-acq-ms", type=float, default=10.0)
    parser.add_argument("--monitor-rate", type=float, default=10_000.0)
    parser.add_argument("--monitor-chunk", type=int, default=500)
    args = parser.parse_args()

    if args.command == "start-enable":
        set_start_level(True)
        return
    if args.command == "start-disable":
        set_start_level(False)
        return
    if args.command == "monitor":
        monitor_ai31(args.monitor_rate, args.monitor_chunk)
        return
    if args.amplitude_v != 3.3:
        parser.error("FDEM nominal amplitude is fixed at 3.3 V")

    # Reset both boards before transmit to release tasks left by a previous
    # failed run, monitor session, or abrupt process exit. ao1 has already
    # completed its one-shot pulse and returned to 0 V.
    import nidaqmx as _nidaqmx
    for _dev in (DEV_TX, DEV_RX):
        try:
            _nidaqmx.system.Device(_dev).reset_device()
        except Exception as _e:
            print(f"WARN: reset {_dev} failed: {_e}", flush=True)

    waveform, params = build_fdem_waveform(
        args.frequency_hz,
        args.cycles,
        args.amplitude_v,
        args.amplitude_mode,
        args.samples_per_cycle,
        args.pre_acq_ms,
        args.post_acq_ms,
    )
    t, data_rx, data_i = fdem_transmit_and_acquire(waveform, params)
    save_result(t, data_rx, data_i, params)

    # Reset ao0 after acquisition while ao1 is already at its idle state.
    try:
        _nidaqmx.system.Device(DEV_TX).reset_device()
    except Exception as _e:
        print(f"WARN: post-transmit reset {DEV_TX} failed: {_e}", flush=True)


if __name__ == "__main__":
    main()
