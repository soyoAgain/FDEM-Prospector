"""Shared Chinese font configuration for Qt and Matplotlib."""

from __future__ import annotations

import matplotlib
from matplotlib import font_manager

FONT_CANDIDATES = (
    "PingFang SC",
    "Hiragino Sans GB",
    "Heiti SC",
    "STHeiti",
    "Microsoft YaHei",
    "SimHei",
    "Noto Sans CJK SC",
    "Arial Unicode MS",
    "DejaVu Sans",
)


def available_font_family() -> str:
    installed = {font.name for font in font_manager.fontManager.ttflist}
    return next((name for name in FONT_CANDIDATES if name in installed), "sans-serif")


def configure_matplotlib_fonts() -> str:
    family = available_font_family()
    matplotlib.rcParams["font.family"] = "sans-serif"
    matplotlib.rcParams["font.sans-serif"] = [family, *FONT_CANDIDATES]
    matplotlib.rcParams["axes.unicode_minus"] = False
    return family


def configure_qt_font(app) -> str:
    from PySide6.QtGui import QFont, QFontDatabase

    installed = set(QFontDatabase.families())
    family = next((name for name in FONT_CANDIDATES if name in installed), app.font().family())
    font = QFont(family)
    font.setPointSize(app.font().pointSize())
    app.setFont(font)
    return family
