"""Real-time ai31 oscilloscope monitor window."""

from __future__ import annotations

import base64
import queue
import subprocess
import threading
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from config import PXI_CODE_PATH, PXI_HOST, PXI_PYTHON, PXI_SCP_PATH, PXI_USER
from ui.widgets import MplCanvas

PROJECT_DIR = Path(__file__).resolve().parent.parent

_MONITOR_RATES = [1_000, 5_000, 10_000, 50_000, 100_000]
_DEFAULT_RATE = 10_000
_CHUNK_SAMPLES = 500
_PLOT_INTERVAL_MS = 50  # 20 Hz update rate


def _decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


class MonitorWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ai31 实时监测")
        self.resize(860, 460)
        self.setMinimumSize(600, 360)

        self._process: subprocess.Popen | None = None
        self._queue: queue.Queue = queue.Queue()
        self._buffer = np.zeros(0)
        self._sample_rate = float(_DEFAULT_RATE)

        self._setup_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(_PLOT_INTERVAL_MS)
        self._timer.timeout.connect(self._update_plot)

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("就绪，点击「开始监测」连接 PXI")

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 4)
        root.setSpacing(8)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("采样率:"))
        self._rate_combo = QComboBox()
        for r in _MONITOR_RATES:
            self._rate_combo.addItem(f"{r / 1000:.0f} kS/s", r)
        self._rate_combo.setCurrentIndex(_MONITOR_RATES.index(_DEFAULT_RATE))
        controls.addWidget(self._rate_combo)

        controls.addSpacing(20)
        controls.addWidget(QLabel("显示时长:"))
        self._window_spin = QDoubleSpinBox()
        self._window_spin.setRange(50.0, 10_000.0)
        self._window_spin.setValue(500.0)
        self._window_spin.setSuffix(" ms")
        self._window_spin.setDecimals(0)
        self._window_spin.setSingleStep(100.0)
        controls.addWidget(self._window_spin)

        controls.addStretch()

        self._btn_start = QPushButton("开始监测")
        self._btn_start.setFixedHeight(34)
        self._btn_start.setStyleSheet(
            "background:#2e7d32;color:white;font-weight:bold;padding:0 16px"
        )
        self._btn_start.clicked.connect(self._on_start)

        self._btn_stop = QPushButton("停止")
        self._btn_stop.setFixedHeight(34)
        self._btn_stop.setStyleSheet(
            "background:#c62828;color:white;font-weight:bold;padding:0 16px"
        )
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_stop.setEnabled(False)

        controls.addWidget(self._btn_start)
        controls.addWidget(self._btn_stop)
        root.addLayout(controls)

        self._canvas = MplCanvas()
        self._canvas.ax.set_title("接收线圈 ai31 实时监测")
        self._canvas.ax.set_xlabel("Time (ms)")
        self._canvas.ax.set_ylabel("Voltage (V)")
        self._canvas.init_line("#1B5E20")
        root.addWidget(self._canvas, 1)

        self._toolbar = NavigationToolbar2QT(self._canvas, self)
        self.addToolBar(self._toolbar)

    def _on_start(self):
        self._sample_rate = float(self._rate_combo.currentData())
        self._buffer = np.zeros(0)
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._rate_combo.setEnabled(False)
        self._status.showMessage("正在推送代码并连接 PXI...")
        threading.Thread(target=self._stream_worker, daemon=True).start()
        self._timer.start()

    def _stream_worker(self):
        """Background thread: SCP script then open persistent SSH stream."""
        try:
            target = f"{PXI_USER}@{PXI_HOST}"
            files = [str(PROJECT_DIR / f) for f in ("fdem_acquisition.py", "config.py")]
            scp = subprocess.run(
                ["scp", "-o", "ConnectTimeout=10", *files, f"{target}:{PXI_SCP_PATH}/"],
                capture_output=True, timeout=30,
            )
            if scp.returncode != 0:
                self._queue.put(("ERROR", f"代码推送失败: {_decode_bytes(scp.stderr)}"))
                return

            cmd = (
                f'set "PYTHONUTF8=1" && set "PYTHONIOENCODING=utf-8" && '
                f'cd /d "{PXI_CODE_PATH}" && '
                f"{PXI_PYTHON} fdem_acquisition.py monitor "
                f"--monitor-rate {int(self._sample_rate)} "
                f"--monitor-chunk {_CHUNK_SAMPLES}"
            )
            self._process = subprocess.Popen(
                ["ssh", target, cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            for raw_line in self._process.stdout:
                line = _decode_bytes(raw_line).strip()
                if line == "MONITOR_READY":
                    self._queue.put(("READY", None))
                elif line.startswith("D:"):
                    try:
                        arr = np.frombuffer(
                            base64.b64decode(line[2:]), dtype=np.float64
                        )
                        self._queue.put(("DATA", arr))
                    except Exception:
                        pass
                elif line.startswith("MONITOR_ERROR:"):
                    self._queue.put(("ERROR", line[14:]))
                    return
        except Exception as exc:
            self._queue.put(("ERROR", str(exc)))

    def _update_plot(self):
        got_data = False
        while True:
            try:
                kind, payload = self._queue.get_nowait()
                if kind == "READY":
                    self._status.showMessage(
                        f"监测中 — {self._sample_rate / 1000:.0f} kS/s，"
                        f"显示 {int(self._window_spin.value())} ms"
                    )
                elif kind == "DATA":
                    self._buffer = np.concatenate([self._buffer, payload])
                    max_samples = max(
                        1, int(self._window_spin.value() / 1000.0 * self._sample_rate)
                    )
                    if self._buffer.size > max_samples:
                        self._buffer = self._buffer[-max_samples:]
                    got_data = True
                elif kind == "ERROR":
                    self._status.showMessage(f"错误: {payload}")
                    self._on_stop()
                    return
            except queue.Empty:
                break

        if got_data and self._buffer.size > 0:
            n = self._buffer.size
            t_ms = np.linspace(
                -(n - 1) / self._sample_rate * 1000.0, 0.0, n
            )
            self._canvas.update_line(t_ms, self._buffer, "接收线圈 ai31 实时监测")

    def _on_stop(self):
        self._timer.stop()
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._rate_combo.setEnabled(True)
        self._status.showMessage("已停止")

    def closeEvent(self, event):
        self._on_stop()
        event.accept()
