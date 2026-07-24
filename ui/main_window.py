"""FDEM desktop control, acquisition, storage, and plotting."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT
from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from config import (
    AMPLITUDE_MODES,
    DEFAULT_AMPLITUDE_MODE,
    DEFAULT_AMPLITUDE_V,
    DEFAULT_AI29_ATTENUATION,
    DEFAULT_AI29_SHUNT_RESISTANCE_OHM,
    DEFAULT_CYCLES,
    DEFAULT_FREQUENCY_HZ,
    DEFAULT_FREQUENCY_STEP_HZ,
    DEFAULT_POST_ACQ_MS,
    DEFAULT_PRE_ACQ_MS,
    DEFAULT_SAMPLES_PER_CYCLE,
    EXTERNAL_TRIGGER_MS,
    EXTERNAL_TRIGGER_V,
    MAX_FREQUENCY_HZ,
    MIN_FREQUENCY_HZ,
    PXI_CODE_PATH,
    PXI_HOST,
    PXI_PYTHON,
    PXI_SCP_PATH,
    PXI_USER,
    READY_THRESHOLD_V,
    SAMPLES_PER_CYCLE_OPTIONS,
)
from fdem_acquisition import build_fdem_waveform
from logger import log, log_exception, log_ssh
from ui.frequency_window import FrequencyWindow
from ui.monitor_window import MonitorWindow
from ui.widgets import MplCanvas

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


def _global_excepthook(exc_type, exc_value, exc_tb):
    log.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))
    sys.__excepthook__(exc_type, exc_value, exc_tb)


sys.excepthook = _global_excepthook


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


class WorkerSignals(QObject):
    finished = Signal(bool, str, str)
    files_ready = Signal(bool, str, object)
    connection = Signal(bool)
    ready = Signal(bool, float, str)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FDEM 探测系统")
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(int(screen.width() * 0.85), int(screen.height() * 0.82))
        self.setMinimumSize(1000, 680)

        self._signals = WorkerSignals()
        self._signals.connection.connect(self._set_connection_state)
        self._signals.files_ready.connect(self._on_files_ready)
        self._signals.ready.connect(self._set_ready_state)
        self._data_t = None
        self._data_rx = None
        self._data_i = None
        self._last_params = None
        self._pxi_online = False
        self._charged = False
        self._frequency_window = None
        self._monitor_window = None
        self._operation_busy = False
        self._operation_signals = None
        self._ready_poll_in_flight = False

        self._setup_controls()
        self._setup_charts()
        self._ready_timer = QTimer(self)
        self._ready_timer.setInterval(1000)
        self._ready_timer.timeout.connect(self._poll_ready)
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("就绪 - Signal in 默认幅值为 6 Vpk (12 Vpp)，请执行波形预检")
        self._refresh_history()
        self._update_measurement_count()
        self._update_preflight()
        threading.Thread(target=self._check_connection, daemon=True).start()

    def _setup_controls(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        left = QWidget()
        left.setMinimumWidth(410)
        layout = QVBoxLayout(left)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        project_group = QGroupBox("项目")
        project_row = QHBoxLayout(project_group)
        project_row.addWidget(QLabel("名称:"))
        self._project_edit = QLineEdit("FDEM测线")
        self._project_edit.textChanged.connect(self._refresh_history)
        project_row.addWidget(self._project_edit)
        layout.addWidget(project_group)

        state_row = QHBoxLayout()
        state_row.setContentsMargins(8, 2, 8, 2)
        state_row.addWidget(QLabel("功放:"))
        self._ready_label = QLabel("未充电")
        self._ready_label.setStyleSheet("color:#ef6c00;font-weight:bold")
        state_row.addWidget(self._ready_label)
        state_row.addSpacing(18)
        self._pxi_label = QLabel("PXI: 检测中")
        state_row.addWidget(self._pxi_label)
        state_row.addStretch()
        layout.addLayout(state_row)

        params_group = QGroupBox("FDEM 发射与采集参数")
        params_grid = QGridLayout(params_group)
        params_grid.setContentsMargins(16, 16, 16, 14)
        params_grid.setHorizontalSpacing(12)
        params_grid.setVerticalSpacing(9)
        params_grid.setColumnStretch(0, 0)
        params_grid.setColumnStretch(1, 1)
        params_grid.setColumnStretch(2, 0)

        def add_parameter_row(row, text, widget, hint=""):
            label = QLabel(text)
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            label.setMinimumWidth(125)
            widget.setMinimumWidth(165)
            params_grid.addWidget(label, row, 0)
            params_grid.addWidget(widget, row, 1)
            if hint:
                hint_label = QLabel(hint)
                hint_label.setStyleSheet("color:#616161")
                params_grid.addWidget(hint_label, row, 2)

        self._frequency_spin = QDoubleSpinBox()
        self._frequency_spin.setRange(MIN_FREQUENCY_HZ, MAX_FREQUENCY_HZ)
        self._frequency_spin.setDecimals(3)
        self._frequency_spin.setValue(DEFAULT_FREQUENCY_HZ)
        self._frequency_spin.setSuffix(" Hz")
        self._frequency_step_spin = QDoubleSpinBox()
        self._frequency_step_spin.setRange(0.001, MAX_FREQUENCY_HZ)
        self._frequency_step_spin.setDecimals(3)
        self._frequency_step_spin.setValue(DEFAULT_FREQUENCY_STEP_HZ)
        self._frequency_step_spin.setSuffix(" Hz")
        self._frequency_step_button = QPushButton("f + df")
        self._frequency_step_button.setFixedWidth(72)
        self._frequency_step_button.setToolTip("将当前发射频率增加 df")
        self._frequency_step_button.clicked.connect(self._increase_frequency)
        self._cycles_spin = QSpinBox()
        self._cycles_spin.setRange(1, 10_000)
        self._cycles_spin.setValue(DEFAULT_CYCLES)
        self._amplitude_spin = QDoubleSpinBox()
        self._amplitude_spin.setRange(0.001, 10.0)
        self._amplitude_spin.setDecimals(3)
        self._amplitude_spin.setValue(DEFAULT_AMPLITUDE_V)
        self._amplitude_spin.setSuffix(" V")
        self._amplitude_spin.setReadOnly(True)
        self._amplitude_spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self._amplitude_mode = QComboBox()
        self._amplitude_mode.addItem("请选择 Vpp / Vpk", "")
        for mode in AMPLITUDE_MODES:
            self._amplitude_mode.addItem(mode, mode)
        default_mode_index = self._amplitude_mode.findData(DEFAULT_AMPLITUDE_MODE)
        if default_mode_index >= 0:
            self._amplitude_mode.setCurrentIndex(default_mode_index)
        self._samples_combo = QComboBox()
        for samples in SAMPLES_PER_CYCLE_OPTIONS:
            self._samples_combo.addItem(str(samples), samples)
        self._samples_combo.setCurrentIndex(SAMPLES_PER_CYCLE_OPTIONS.index(DEFAULT_SAMPLES_PER_CYCLE))
        self._pre_spin = QDoubleSpinBox()
        self._pre_spin.setRange(0.0, 10_000.0)
        self._pre_spin.setValue(DEFAULT_PRE_ACQ_MS)
        self._pre_spin.setSuffix(" ms")
        self._post_spin = QDoubleSpinBox()
        self._post_spin.setRange(0.0, 10_000.0)
        self._post_spin.setValue(DEFAULT_POST_ACQ_MS)
        self._post_spin.setSuffix(" ms")
        self._shunt_spin = QDoubleSpinBox()
        self._shunt_spin.setRange(0.001, 100_000.0)
        self._shunt_spin.setValue(DEFAULT_AI29_SHUNT_RESISTANCE_OHM)
        self._shunt_spin.setSuffix(" Ohm")
        self._atten_spin = QDoubleSpinBox()
        self._atten_spin.setRange(0.001, 100_000.0)
        self._atten_spin.setValue(DEFAULT_AI29_ATTENUATION)
        self._atten_spin.setSuffix(" x")

        add_parameter_row(0, "频率 f", self._frequency_spin)
        add_parameter_row(1, "频率步进 df", self._frequency_step_spin)
        params_grid.addWidget(self._frequency_step_button, 1, 2)
        add_parameter_row(2, "完整周期 n", self._cycles_spin, "周期")
        add_parameter_row(3, "标称幅值", self._amplitude_spin, "固定")
        add_parameter_row(4, "幅值定义", self._amplitude_mode, "必选")
        add_parameter_row(5, "每周期采样点", self._samples_combo, "点")
        add_parameter_row(6, "发射前采集", self._pre_spin)
        add_parameter_row(7, "发射后采集", self._post_spin)
        add_parameter_row(8, "ai29 采样电阻", self._shunt_spin)
        add_parameter_row(9, "ai29 探头衰减", self._atten_spin)

        self._derived_label = QLabel()
        self._derived_label.setWordWrap(True)
        self._derived_label.setMinimumHeight(42)
        self._derived_label.setContentsMargins(10, 6, 10, 6)
        self._derived_label.setStyleSheet(
            "background:#f5f5f5;border:1px solid #d5d5d5;border-radius:5px;color:#424242"
        )
        params_grid.addWidget(self._derived_label, 10, 0, 1, 3)
        layout.addWidget(params_group)

        external_group = QGroupBox("外接发生器")
        external_grid = QGridLayout(external_group)
        external_grid.setContentsMargins(16, 12, 16, 12)
        external_grid.setHorizontalSpacing(10)
        external_grid.setVerticalSpacing(8)
        self._external_frequency_spin = QDoubleSpinBox()
        self._external_frequency_spin.setRange(MIN_FREQUENCY_HZ, MAX_FREQUENCY_HZ)
        self._external_frequency_spin.setDecimals(3)
        self._external_frequency_spin.setValue(DEFAULT_FREQUENCY_HZ)
        self._external_frequency_spin.setSuffix(" Hz")
        self._external_cycles_spin = QSpinBox()
        self._external_cycles_spin.setRange(1, 10_000)
        self._external_cycles_spin.setValue(DEFAULT_CYCLES)
        external_grid.addWidget(QLabel("频率 f"), 0, 0)
        external_grid.addWidget(self._external_frequency_spin, 0, 1)
        external_grid.addWidget(QLabel("正弦周期 n"), 1, 0)
        external_grid.addWidget(self._external_cycles_spin, 1, 1)
        external_grid.addWidget(
            QLabel(f"ao0 触发：{EXTERNAL_TRIGGER_V:g} V，{EXTERNAL_TRIGGER_MS:g} ms"), 2, 0, 1, 2
        )
        self._btn_external = QPushButton("外接发生器触发 + 接收")
        self._btn_external.setFixedHeight(38)
        self._btn_external.setToolTip("ao0 输出触发脉冲，随后采集 ai31 和 ai29")
        self._btn_external.clicked.connect(self._on_external_fire)
        self._btn_external.setEnabled(False)
        external_grid.addWidget(self._btn_external, 3, 0, 1, 2)
        layout.addWidget(external_group)

        safety_group = QGroupBox("IGBT 安全提示")
        safety_layout = QVBoxLayout(safety_group)
        safety_layout.setSpacing(7)
        warning = QLabel("警告：IGBT 无保护。ao0 出现直流或正弦 offset 会立即烧坏。")
        warning.setWordWrap(True)
        warning.setStyleSheet("color:#b71c1c;font-weight:bold")
        safety_layout.addWidget(warning)
        self._preflight_label = QLabel()
        self._preflight_label.setWordWrap(True)
        safety_layout.addWidget(self._preflight_label)
        scope_notice = QLabel(
            "操作前请用 DC 耦合示波器确认：直接发射时 ao0 无直流/offset；"
            "外接发生器模式为 4 V、10 ms 触发脉冲且随后回到 0 V。"
        )
        scope_notice.setWordWrap(True)
        scope_notice.setStyleSheet("color:#616161")
        safety_layout.addWidget(scope_notice)
        layout.addWidget(safety_group)

        point_group = QGroupBox("测点管理")
        point_layout = QFormLayout(point_group)
        point_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self._point_edit = QLineEdit("测点1")
        self._point_edit.textChanged.connect(self._update_measurement_count)
        self._count_label = QLabel()
        point_layout.addRow("名称:", self._point_edit)
        point_layout.addRow(self._count_label)
        layout.addWidget(point_group)

        buttons = QGroupBox("操作")
        buttons_layout = QVBoxLayout(buttons)
        self._btn_charge = QPushButton("① 充电 (ao1: 0→4V→0)")
        self._btn_charge.setFixedHeight(38)
        self._btn_charge.clicked.connect(self._on_charge)
        self._btn_charge.setEnabled(False)
        self._ready_lamp = QLabel()
        self._ready_lamp.setFixedSize(14, 14)
        self._ready_text = QLabel("Ready 未知")
        self._set_ready_indicator(None)
        self._btn_fire = QPushButton("② 正弦发射 + 同步采集")
        self._btn_fire.setFixedHeight(42)
        self._btn_fire.setStyleSheet("background:#1565c0;color:white;font-weight:bold")
        self._btn_fire.clicked.connect(self._on_fire)
        self._btn_fire.setEnabled(False)
        charge_row = QHBoxLayout()
        charge_row.addWidget(self._btn_charge, 1)
        charge_row.addWidget(self._ready_lamp)
        charge_row.addWidget(self._ready_text)
        buttons_layout.addLayout(charge_row)
        buttons_layout.addWidget(self._btn_fire)
        aux = QHBoxLayout()
        self._btn_frequency = QPushButton("幅相")
        self._btn_frequency.clicked.connect(self._on_frequency)
        self._btn_screenshot = QPushButton("截图")
        self._btn_screenshot.clicked.connect(self._on_screenshot)
        self._btn_log = QPushButton("日志")
        self._btn_log.clicked.connect(self._on_log)
        self._btn_monitor = QPushButton("实时监测")
        self._btn_monitor.setStyleSheet(
            "background:#00695c;color:white;font-weight:bold"
        )
        self._btn_monitor.clicked.connect(self._on_monitor)
        self._btn_monitor.setEnabled(False)
        for button in (self._btn_frequency, self._btn_screenshot, self._btn_log):
            aux.addWidget(button)
        buttons_layout.addLayout(aux)
        buttons_layout.addWidget(self._btn_monitor)
        layout.addWidget(buttons)
        layout.addStretch()

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setFrameShape(QScrollArea.NoFrame)
        left_scroll.setFixedWidth(440)
        left_scroll.setWidget(left)
        root.addWidget(left_scroll)

        self._chart_container = QWidget()
        self._chart_layout = QVBoxLayout(self._chart_container)
        self._chart_layout.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._chart_container, 1)

        for widget in (
            self._frequency_spin, self._cycles_spin, self._amplitude_spin,
            self._amplitude_mode, self._samples_combo, self._pre_spin, self._post_spin,
        ):
            if isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._parameters_changed)
            else:
                widget.valueChanged.connect(self._parameters_changed)
        self._external_frequency_spin.valueChanged.connect(self._update_fire_enabled)
        self._external_cycles_spin.valueChanged.connect(self._update_fire_enabled)
        self._shunt_spin.valueChanged.connect(self._draw_all)
        self._atten_spin.valueChanged.connect(self._draw_all)

    def _setup_charts(self):
        loader = QHBoxLayout()
        loader.addWidget(QLabel("历史测点:"))
        self._history_point = QComboBox()
        self._history_point.currentTextChanged.connect(self._history_point_changed)
        loader.addWidget(self._history_point)
        loader.addWidget(QLabel("编号:"))
        self._history_number = QSpinBox()
        self._history_number.setRange(1, 9999)
        loader.addWidget(self._history_number)
        load_button = QPushButton("加载")
        load_button.clicked.connect(self._load_history)
        loader.addWidget(load_button)
        loader.addStretch()
        loader.addWidget(QLabel("跳过前:"))
        self._skip_spin = QDoubleSpinBox()
        self._skip_spin.setRange(0.0, 100.0)
        self._skip_spin.setValue(0.5)
        self._skip_spin.setSuffix(" ms")
        self._skip_spin.setDecimals(2)
        self._skip_spin.setFixedWidth(90)
        self._skip_spin.setToolTip("绘图时跳过开头 N ms（过滤充电瞬态），不影响存储数据和幅相分析")
        self._skip_spin.valueChanged.connect(self._draw_all)
        loader.addWidget(self._skip_spin)
        self._chart_layout.addLayout(loader)

        self._canvas_rx = MplCanvas()
        self._canvas_rx.ax.set_title("接收线圈电压 ai31 (时域)")
        self._canvas_rx.init_line("#1B5E20")
        self._chart_layout.addWidget(self._canvas_rx, 2)
        self._canvas_i = MplCanvas()
        self._canvas_i.ax.set_title("发射电流监测 ai29 (时域)")
        self._canvas_i.ax.set_ylabel("Current (A)")
        self._canvas_i.init_line("#B71C1C")
        self._chart_layout.addWidget(self._canvas_i, 1)
        self._toolbar = NavigationToolbar2QT(self._canvas_rx, self)
        self.addToolBar(self._toolbar)

    def _parameters(self):
        mode = self._amplitude_mode.currentData()
        if not mode:
            raise ValueError("必须明确选择 6 Vpk (12 Vpp) 的幅值定义")
        _, params = build_fdem_waveform(
            self._frequency_spin.value(), self._cycles_spin.value(),
            self._amplitude_spin.value(), mode, self._samples_combo.currentData(),
            self._pre_spin.value(), self._post_spin.value(),
        )
        params["shunt_resistance_ohm"] = self._shunt_spin.value()
        params["atten"] = self._atten_spin.value()
        return params

    def _parameters_changed(self, *_):
        self._update_preflight()

    def _increase_frequency(self):
        current = self._frequency_spin.value()
        step = self._frequency_step_spin.value()
        updated = min(current + step, self._frequency_spin.maximum())
        self._frequency_spin.setValue(updated)
        if updated >= self._frequency_spin.maximum():
            self._status.showMessage(
                f"频率已达到上限 {self._frequency_spin.maximum():g} Hz"
            )
        else:
            self._status.showMessage(f"频率已增加 {step:g} Hz，当前 {updated:g} Hz")

    def _update_preflight(self):
        try:
            params = self._parameters()
            self._derived_label.setText(
                f"采样率 {params['sample_rate']/1000:.3f} kS/s；发射 {params['transmit_duration_ms']:.3f} ms；"
                f"峰值 {params['peak_amplitude_v']:.3f} V；总点数 {params['total_samples']}"
            )
            self._preflight_label.setText("软件波形预检：通过（整数周期、数字零均值、首尾回零）")
            self._preflight_label.setStyleSheet("color:#2e7d32;font-weight:bold")
        except ValueError as exc:
            self._derived_label.setText("参数未就绪")
            self._preflight_label.setText(f"软件波形预检：未通过 - {exc}")
            self._preflight_label.setStyleSheet("color:#b71c1c;font-weight:bold")
        self._update_fire_enabled()

    def _update_fire_enabled(self, *_):
        try:
            self._parameters()
            valid = True
        except ValueError:
            valid = False
        self._btn_fire.setEnabled(
            self._pxi_online and self._charged and valid and not self._operation_busy
        )
        self._btn_external.setEnabled(
            self._pxi_online and self._charged and self._external_parameters_valid()
            and not self._operation_busy
        )
        self._btn_charge.setEnabled(self._pxi_online and not self._operation_busy)

    def _external_parameters_valid(self):
        return (
            MIN_FREQUENCY_HZ <= self._external_frequency_spin.value() <= MAX_FREQUENCY_HZ
            and self._external_cycles_spin.value() >= 1
        )

    def _check_connection(self):
        try:
            process = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=3", "-o", "BatchMode=yes",
                 f"{PXI_USER}@{PXI_HOST}", "echo ok"],
                capture_output=True, timeout=5,
            )
            self._signals.connection.emit(process.returncode == 0)
        except Exception:
            self._signals.connection.emit(False)

    def _set_connection_state(self, online):
        self._pxi_online = online
        self._pxi_label.setText("PXI: 已连接" if online else "PXI: 离线（可查看本地数据）")
        self._pxi_label.setStyleSheet(f"color:{'#2e7d32' if online else '#c62828'}")
        self._btn_charge.setEnabled(online)
        self._btn_monitor.setEnabled(online)
        if not online:
            self._ready_timer.stop()
            self._set_ready_indicator(None)
        self._update_fire_enabled()

    def _set_ready_indicator(self, ready, voltage=None):
        if ready is True:
            color, text = "#2e7d32", "可放电"
        elif ready is False:
            color, text = "#c62828", "不可放电"
        else:
            color, text = "#9e9e9e", "Ready 未知"
        self._ready_lamp.setStyleSheet(
            f"background:{color};border:1px solid #616161;border-radius:7px"
        )
        suffix = f" ({voltage:.2f} V)" if voltage is not None else ""
        self._ready_text.setText(f"{text}{suffix}")
        self._ready_text.setStyleSheet(f"color:{color};font-weight:bold")

    def _poll_ready(self):
        if not self._pxi_online or self._operation_busy or self._ready_poll_in_flight:
            return
        self._ready_poll_in_flight = True
        threading.Thread(target=self._ready_worker, daemon=True).start()

    def _ready_worker(self):
        try:
            target = f"{PXI_USER}@{PXI_HOST}"
            command = (
                f'set "PYTHONUTF8=1" && set "PYTHONIOENCODING=utf-8" && '
                f'cd /d "{PXI_CODE_PATH}" && {PXI_PYTHON} fdem_acquisition.py ready'
            )
            process = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=3", target, command],
                capture_output=True, timeout=8,
            )
            stdout, stderr = _decode(process.stdout), _decode(process.stderr)
            if process.returncode != 0:
                self._signals.ready.emit(False, float("nan"), stderr or stdout)
                return
            match = re.search(r"READY_VOLTAGE:([-+0-9.eE]+)", stdout)
            if not match:
                self._signals.ready.emit(False, float("nan"), "PXI 未返回 Ready 电压")
                return
            self._signals.ready.emit(True, float(match.group(1)), "")
        except Exception as exc:
            self._signals.ready.emit(False, float("nan"), str(exc))

    def _set_ready_state(self, ok, voltage, error):
        self._ready_poll_in_flight = False
        if not ok or not np.isfinite(voltage):
            self._set_ready_indicator(None)
            if error:
                log.warning("Ready status read failed: %s", error)
            return
        self._set_ready_indicator(voltage > READY_THRESHOLD_V, voltage)

    @staticmethod
    def _remote_worker(signals, command, timeout):
        try:
            target = f"{PXI_USER}@{PXI_HOST}"
            mkdir = subprocess.run(
                ["ssh", target, f'if not exist "{PXI_CODE_PATH}" mkdir "{PXI_CODE_PATH}"'],
                capture_output=True, timeout=20,
            )
            if mkdir.returncode != 0:
                signals.finished.emit(False, "", _decode(mkdir.stderr))
                return
            files = ("fdem_acquisition.py", "config.py")
            process = subprocess.run(
                ["scp", "-o", "ConnectTimeout=10", *[str(PROJECT_DIR / name) for name in files],
                 f"{target}:{PXI_SCP_PATH}/"],
                capture_output=True, timeout=30,
            )
            if process.returncode != 0:
                signals.finished.emit(False, "", f"代码推送失败: {_decode(process.stderr)}")
                return
            utf8_command = (
                f'set "PYTHONUTF8=1" && set "PYTHONIOENCODING=utf-8" && {command}'
            )
            process = subprocess.run(["ssh", target, utf8_command], capture_output=True, timeout=timeout)
            stdout, stderr = _decode(process.stdout), _decode(process.stderr)
            log_ssh(command, process.returncode == 0, stdout, stderr)
            signals.finished.emit(process.returncode == 0, stdout, stderr)
        except Exception as exc:
            log_exception(exc, "PXI remote operation")
            signals.finished.emit(False, "", str(exc))

    def _run_remote(self, command, callback, timeout=300):
        if self._operation_busy:
            QMessageBox.warning(self, "操作进行中", "请等待当前 PXI 操作完成")
            return False
        self._operation_busy = True
        self._update_fire_enabled()
        signals = WorkerSignals()
        self._operation_signals = signals

        def finished(ok, stdout, stderr):
            self._operation_busy = False
            self._operation_signals = None
            callback(ok, stdout, stderr)
            self._update_fire_enabled()

        signals.finished.connect(finished)
        threading.Thread(
            target=self._remote_worker, args=(signals, command, timeout), daemon=True
        ).start()
        return True

    def _on_charge(self):
        self._status.showMessage("正在发送 ao1 启动脉冲（0V→4V保持500ms→0V）...")

        def done(ok, stdout, stderr):
            if not ok or "START_PULSE_OK" not in stdout:
                QMessageBox.critical(self, "充电失败", stderr or stdout)
                return
            self._charged = True
            self._ready_label.setText("Start 脉冲已发送，请确认功放状态")
            self._ready_label.setStyleSheet("color:#2e7d32;font-weight:bold")
            self._status.showMessage(
                "Start 脉冲已完成（4V保持500ms）；ao1 应已回到 0V，确认功放准备完成后发射"
            )
            self._set_ready_indicator(None)
            self._ready_timer.start()
            self._poll_ready()
            self._update_fire_enabled()

        command = f'cd /d "{PXI_CODE_PATH}" && {PXI_PYTHON} fdem_acquisition.py start-enable'
        self._run_remote(command, done)

    def _on_fire(self):
        # If the real-time monitor is streaming ai31, it holds the FDEM_RxAcq
        # task open. Transmitting now would produce DaqError -50103.
        if (
            self._monitor_window is not None
            and self._monitor_window.isVisible()
            and self._monitor_window._process is not None
        ):
            answer = QMessageBox.warning(
                self, "实时监测运行中",
                "实时监测正在使用 ai31，发射前必须停止监测。\n点击「确定」自动停止监测并继续发射。",
                QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel,
            )
            if answer != QMessageBox.Ok:
                return
            self._monitor_window._on_stop()
        try:
            params = self._parameters()
        except ValueError as exc:
            QMessageBox.critical(self, "参数错误", str(exc))
            return
        self._status.showMessage("正在进行 FDEM 正弦发射与同步采集...")
        command = (
            f'cd /d "{PXI_CODE_PATH}" && {PXI_PYTHON} fdem_acquisition.py transmit'
            f" --frequency-hz {params['frequency_hz']} --cycles {params['cycles']}"
            f" --amplitude-v {params['amplitude_v']} --amplitude-mode {params['amplitude_mode']}"
            f" --samples-per-cycle {params['samples_per_cycle']}"
            f" --pre-acq-ms {params['pre_acq_ms']} --post-acq-ms {params['post_acq_ms']}"
        )

        destination = self._next_destination_prefix(params["frequency_hz"])

        def done(ok, stdout, stderr):
            if not ok:
                QMessageBox.critical(self, "发射采集失败", stderr or stdout)
                self._update_fire_enabled()
                return
            prefix = next(
                (line.split(":", 1)[1].strip() for line in stdout.splitlines()
                 if line.startswith("DATA_SAVED:")), None
            )
            if not prefix:
                QMessageBox.critical(self, "数据错误", "PXI 未返回数据文件路径")
                self._update_fire_enabled()
                return
            # Keep hardware controls locked until all four files are validated
            # and atomically promoted to their final names.
            self._operation_busy = True
            self._update_fire_enabled()
            threading.Thread(
                target=self._pull_files, args=(prefix, params, destination), daemon=True
            ).start()

        duration_s = params["total_samples"] / params["sample_rate"]
        self._run_remote(command, done, timeout=max(60.0, duration_s + 60.0))

    def _on_external_fire(self):
        if (
            self._monitor_window is not None
            and self._monitor_window.isVisible()
            and self._monitor_window._process is not None
        ):
            answer = QMessageBox.warning(
                self, "实时监测运行中",
                "实时监测正在使用 ai31，发射前必须停止监测。\n点击「确定」自动停止监测并继续采集。",
                QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel,
            )
            if answer != QMessageBox.Ok:
                return
            self._monitor_window._on_stop()
        if not self._external_parameters_valid():
            QMessageBox.critical(self, "参数错误", "外接发生器频率或周期数无效")
            return
        frequency = self._external_frequency_spin.value()
        cycles = self._external_cycles_spin.value()
        sample_rate = frequency * self._samples_combo.currentData()
        total_duration = EXTERNAL_TRIGGER_MS / 1000.0 + cycles / frequency + self._post_spin.value() / 1000.0
        params = {
            "frequency_hz": frequency,
            "cycles": cycles,
            "amplitude_v": None,
            "amplitude_mode": "external",
            "peak_amplitude_v": None,
            "samples_per_cycle": self._samples_combo.currentData(),
            "sample_rate": sample_rate,
            "pre_acq_ms": 0.0,
            "post_acq_ms": self._post_spin.value(),
            "trigger_v": EXTERNAL_TRIGGER_V,
            "trigger_ms": EXTERNAL_TRIGGER_MS,
            "total_samples": int(np.ceil(total_duration * sample_rate)) + 1,
            "shunt_resistance_ohm": self._shunt_spin.value(),
            "atten": self._atten_spin.value(),
        }
        self._status.showMessage("正在通过 ao0 触发外接发生器并同步采集...")
        command = (
            f'cd /d "{PXI_CODE_PATH}" && {PXI_PYTHON} fdem_acquisition.py external-transmit'
            f" --frequency-hz {frequency} --cycles {cycles}"
            f" --samples-per-cycle {params['samples_per_cycle']}"
            f" --post-acq-ms {params['post_acq_ms']}"
        )
        destination = self._next_destination_prefix(frequency)

        def done(ok, stdout, stderr):
            if not ok:
                QMessageBox.critical(self, "外接发生器采集失败", stderr or stdout)
                self._update_fire_enabled()
                return
            prefix = next(
                (line.split(":", 1)[1].strip() for line in stdout.splitlines()
                 if line.startswith("DATA_SAVED:")), None
            )
            if not prefix:
                QMessageBox.critical(self, "数据错误", "PXI 未返回数据文件路径")
                self._update_fire_enabled()
                return
            self._operation_busy = True
            self._update_fire_enabled()
            threading.Thread(
                target=self._pull_files, args=(prefix, params, destination), daemon=True
            ).start()

        duration_s = params["total_samples"] / params["sample_rate"]
        self._run_remote(command, done, timeout=max(60.0, duration_s + 60.0))

    def _pull_files(self, prefix, params, local_prefix):
        try:
            basename = prefix.replace("\\", "/").rsplit("/", 1)[-1]
            remote_dir = f"{PXI_USER}@{PXI_HOST}:{PXI_SCP_PATH}/data"
            suffixes = ("_t.npy", "_rx.npy", "_current.npy", "_info.json")
            with tempfile.TemporaryDirectory(dir=local_prefix.parent) as temp_dir:
                temp_prefix = Path(temp_dir) / "record"
                for suffix in suffixes:
                    process = subprocess.run(
                        ["scp", "-o", "ConnectTimeout=10", f"{remote_dir}/{basename}{suffix}",
                         f"{temp_prefix}{suffix}"],
                        capture_output=True, timeout=30,
                    )
                    if process.returncode != 0:
                        raise RuntimeError(_decode(process.stderr))
                arrays = [np.load(f"{temp_prefix}{suffix}") for suffix in suffixes[:3]]
                if any(array.ndim != 1 for array in arrays):
                    raise RuntimeError("Downloaded arrays must be one-dimensional")
                if len({array.size for array in arrays}) != 1:
                    raise RuntimeError("Downloaded arrays have inconsistent lengths")
                with Path(f"{temp_prefix}_info.json").open(encoding="utf-8") as handle:
                    saved_params = json.load(handle)
                if arrays[0].size != int(saved_params["total_samples"]):
                    raise RuntimeError("Downloaded sample count does not match metadata")
                saved_params["shunt_resistance_ohm"] = params["shunt_resistance_ohm"]
                saved_params["atten"] = params["atten"]
                with Path(f"{temp_prefix}_info.json").open("w", encoding="utf-8") as handle:
                    json.dump(saved_params, handle, indent=2, ensure_ascii=False)
                for suffix in suffixes:
                    Path(f"{temp_prefix}{suffix}").replace(Path(f"{local_prefix}{suffix}"))
            self._signals.files_ready.emit(True, str(local_prefix), params)
        except Exception as exc:
            log_exception(exc, "Pull PXI data")
            self._signals.files_ready.emit(False, str(exc), params)

    def _on_files_ready(self, ok, value, params):
        self._operation_busy = False
        if not ok:
            QMessageBox.critical(self, "数据拉取失败", value)
            self._update_fire_enabled()
            return
        try:
            self._load_prefix(Path(value))
            self._status.showMessage(f"采集完成并保存: {value}")
            self._refresh_history()
            self._update_measurement_count()
        except Exception as exc:
            QMessageBox.critical(self, "加载失败", str(exc))
        self._update_fire_enabled()

    @staticmethod
    def _safe_name(value, fallback):
        name = re.sub(r'[\\/:*?"<>|]', "_", value.strip())
        if name in ("", ".", ".."):
            return fallback
        return name

    def _point_dir(self):
        project = self._safe_name(self._project_edit.text(), "default")
        point = self._safe_name(self._point_edit.text(), "测点")
        path = DATA_DIR / project / point
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _frequency_tag(frequency_hz):
        text = f"{float(frequency_hz):.6f}".rstrip("0").rstrip(".")
        return f"{text.replace('.', 'p')}Hz"

    def _next_destination_prefix(self, frequency_hz):
        point_dir = self._point_dir()
        number = self._next_measurement_number(point_dir)
        point = self._safe_name(self._point_edit.text(), "测点")
        return point_dir / f"{point}_{number:03d}_{self._frequency_tag(frequency_hz)}"

    @staticmethod
    def _next_measurement_number(point_dir):
        numbers = []
        for path in point_dir.glob("*_rx.npy"):
            match = re.search(r"_(\d+)(?:_[^_]+Hz)?_rx\.npy$", path.name)
            if match:
                numbers.append(int(match.group(1)))
        return max(numbers, default=0) + 1

    @staticmethod
    def _measurement_prefix(point_dir, point, number):
        old_prefix = point_dir / f"{point}_{number:03d}"
        if Path(f"{old_prefix}_rx.npy").exists():
            return old_prefix
        matches = sorted(point_dir.glob(f"{point}_{number:03d}_*Hz_rx.npy"))
        if not matches:
            raise FileNotFoundError(f"找不到测量数据: {point} 第 {number} 次")
        if len(matches) > 1:
            raise RuntimeError(f"测量编号 {number} 对应多个频率文件")
        return Path(str(matches[0])[:-len("_rx.npy")])

    def _update_measurement_count(self, *_):
        number = self._next_measurement_number(self._point_dir())
        self._count_label.setText(f"第 {number} 次测量就绪")

    def _refresh_history(self, *_):
        if not hasattr(self, "_history_point"):
            return
        current = self._history_point.currentText()
        self._history_point.clear()
        project = DATA_DIR / self._safe_name(self._project_edit.text(), "default")
        if project.exists():
            for path in sorted(project.iterdir()):
                if path.is_dir() and any(path.glob("*_rx.npy")):
                    self._history_point.addItem(path.name)
        index = self._history_point.findText(current)
        if index >= 0:
            self._history_point.setCurrentIndex(index)

    def _history_point_changed(self, point):
        if not point:
            return
        project = DATA_DIR / self._safe_name(self._project_edit.text(), "default") / point
        self._history_number.setMaximum(max(1, self._next_measurement_number(project) - 1))
        self._history_number.setValue(self._history_number.maximum())

    def _load_history(self):
        point = self._history_point.currentText()
        if not point:
            return
        project = DATA_DIR / self._safe_name(self._project_edit.text(), "default") / point
        try:
            prefix = self._measurement_prefix(
                project, point, self._history_number.value()
            )
            self._load_prefix(prefix)
            self._status.showMessage(f"已加载: {prefix}")
        except Exception as exc:
            QMessageBox.critical(self, "加载失败", str(exc))

    def _load_prefix(self, prefix):
        self._data_t = np.load(f"{prefix}_t.npy")
        self._data_rx = np.load(f"{prefix}_rx.npy")
        self._data_i = np.load(f"{prefix}_current.npy")
        with open(f"{prefix}_info.json", encoding="utf-8") as handle:
            self._last_params = json.load(handle)
        self._last_params.setdefault(
            "shunt_resistance_ohm", DEFAULT_AI29_SHUNT_RESISTANCE_OHM
        )
        self._last_params.setdefault("atten", DEFAULT_AI29_ATTENUATION)
        self._shunt_spin.setValue(float(self._last_params["shunt_resistance_ohm"]))
        self._atten_spin.setValue(float(self._last_params["atten"]))
        self._draw_all()

    def _draw_all(self, *_):
        if self._data_t is None:
            return
        t_ms = self._data_t * 1000.0
        skip_ms = self._skip_spin.value()
        skip_samples = int(skip_ms / 1000.0 * self._last_params.get("sample_rate", 1)) if skip_ms > 0 else 0
        skip_samples = min(skip_samples, max(0, len(t_ms) - 1))
        t_plot = t_ms[skip_samples:]
        rx_plot = self._data_rx[skip_samples:]
        i_plot = self._data_i[skip_samples:]
        frequency = self._last_params.get("frequency_hz", 0.0)
        self._canvas_rx.update_line(t_plot, rx_plot, f"接收线圈 ai31 - {frequency:g} Hz")
        self._canvas_i.update_line(
            t_plot, i_plot * self._atten_spin.value() / self._shunt_spin.value(),
            f"发射电流 ai29 - {frequency:g} Hz "
            f"(探头 x{self._atten_spin.value():g} / {self._shunt_spin.value():g} Ohm)",
        )

    def _on_frequency(self):
        if self._data_rx is None or self._data_i is None or not self._last_params:
            QMessageBox.information(self, "提示", "请先采集或加载数据")
            return
        try:
            self._frequency_window = FrequencyWindow(
                self._data_rx, self._data_i, self._last_params, self
            )
            self._frequency_window.show()
        except Exception as exc:
            QMessageBox.critical(self, "幅相分析失败", str(exc))

    def _on_screenshot(self):
        path = DATA_DIR / f"screenshot_{datetime.now():%Y%m%d_%H%M%S}.png"
        self._canvas_rx.fig.savefig(path, dpi=150, bbox_inches="tight")
        self._status.showMessage(f"截图已保存: {path}")

    def _on_log(self):
        path = PROJECT_DIR / "logs" / "fdem.log"
        if path.exists():
            subprocess.run(["open", str(path)], check=False)
        else:
            QMessageBox.information(self, "提示", "日志文件不存在")

    def _on_monitor(self):
        """Open real-time ai31 monitoring window."""
        if not self._pxi_online:
            QMessageBox.warning(self, "PXI 离线", "PXI 未连接，无法启动实时监测")
            return
        if self._monitor_window is None or not self._monitor_window.isVisible():
            self._monitor_window = MonitorWindow(self)
        self._monitor_window.show()
        self._monitor_window.raise_()
        self._monitor_window.activateWindow()
