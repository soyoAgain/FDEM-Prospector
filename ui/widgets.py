import matplotlib
import sys

matplotlib.use("QtAgg")

from font_config import configure_matplotlib_fonts

configure_matplotlib_fonts()

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure


class MplCanvas(FigureCanvas):
    def __init__(self):
        self.fig = Figure(figsize=(6, 3), dpi=100)
        self.fig.subplots_adjust(bottom=0.15, left=0.12)
        self.ax = self.fig.add_subplot(111)
        self.ax.grid(True, alpha=0.3)
        self.ax.set_xlabel("Time (ms)")
        self.ax.set_ylabel("Voltage (V)")
        super().__init__(self.fig)
        self.setMinimumHeight(260)
        self._line = None

    def set_cursor(self, cursor):
        """Avoid a macOS Qt/AppKit cursor crash during Matplotlib hover updates."""
        if sys.platform == "darwin":
            return
        super().set_cursor(cursor)

    def init_line(self, color="#1B5E20"):
        (self._line,) = self.ax.plot([], [], lw=0.7, color=color)

    def update_line(self, x, y, title=None):
        if self._line is None:
            self.init_line()
        self._line.set_data(x, y)
        self.ax.relim()
        self.ax.autoscale_view()
        if title:
            self.ax.set_title(title)
        self.draw_idle()
