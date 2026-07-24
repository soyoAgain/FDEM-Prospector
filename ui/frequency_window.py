"""FDEM coherent frequency result window."""

from PySide6.QtWidgets import QFormLayout, QLabel, QMainWindow, QWidget

from fdem_analysis import analyze_fdem


class FrequencyWindow(QMainWindow):
    def __init__(self, data_rx, data_i, params, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FDEM 幅值与相位")
        self.setMinimumWidth(440)
        result = analyze_fdem(data_rx, data_i, params)
        central = QWidget()
        form = QFormLayout(central)
        self.setCentralWidget(central)

        def add(label, value, unit=""):
            text = "不可用" if value is None else f"{value:.6g}{unit}"
            form.addRow(label, QLabel(text))

        add("频率:", result["frequency_hz"], " Hz")
        add("接收幅值:", result["rx_amplitude_vpk"], " Vpk")
        add("接收相位（未校准）:", result["rx_phase_deg"], " deg")
        add("发射电流幅值:", result["current_amplitude_apk"], " Apk")
        add("发射电流相位（未校准）:", result["current_phase_deg"], " deg")
        add("ai29 采样电阻:", result["ai29_shunt_resistance_ohm"], " Ohm")
        add("ai29 探头衰减倍数:", result["ai29_attenuation"], " x")
        add("Rx/Current 幅值:", result["transfer_magnitude"], " V/A")
        add("Rx/Current 相位:", result["transfer_phase_deg"], " deg")
        add("同相分量:", result["transfer_in_phase"], " V/A")
        add("正交分量:", result["transfer_quadrature"], " V/A")
