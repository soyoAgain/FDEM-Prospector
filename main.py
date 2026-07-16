"""FDEM control and acquisition desktop application."""

import sys

from PySide6.QtWidgets import QApplication

from font_config import configure_qt_font
from ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("FDEM Acquisition")
    configure_qt_font(app)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
