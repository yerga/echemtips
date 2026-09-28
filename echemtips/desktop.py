"""Optional desktop signals, isolated from instrument control and acquisition."""
import math
from PySide6 import QtCore, QtWidgets
from .branding import application_icon


class DesktopStatus(QtCore.QObject):
    """Best-effort notifications and percentage badges; never own hardware state."""
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.base_title = window.windowTitle()
        self.tray = QtWidgets.QSystemTrayIcon(application_icon(), self)
        self.tray.setToolTip('eChemTips — instrument control')
        self.tray.messageClicked.connect(self.show_window)
        self.tray.activated.connect(lambda reason: self.show_window() if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger else None)
        self.menu = QtWidgets.QMenu(window)
        self.menu.addAction('Show instrument control', self.show_window)
        self.tray.setContextMenu(self.menu)
        self.last_progress = None
        app = QtWidgets.QApplication.instance()
        if hasattr(app, 'setBadgeNumber'): app.setBadgeNumber(0)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        QtWidgets.QApplication.instance().aboutToQuit.connect(self.clear)

    def show_window(self):
        """Reveal the existing window; clicking a notification never starts a run."""
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def notify(self, title, message, *, error=False):
        """Submit a short optional OS message, retaining normal in-app messages."""
        if not self.window.settings.desktop_notifications:
            return
        if not self.tray.isSystemTrayAvailable() or not self.tray.supportsMessages():
            return
        self.tray.show()
        kind = self.tray.MessageIcon.Critical if error else self.tray.MessageIcon.Information
        self.tray.showMessage(title, str(message)[:240], kind, 7000)

    def refresh(self):
        """Update at most once per percentage change, using experiment progress."""
        if not self.window.settings.desktop_notifications:
            self.tray.hide()
        active = next((e for e in self.window.experiments.values() if e.active), None)
        value = None
        if active is not None and self.window.settings.desktop_progress:
            progress = float(active.progress)
            if math.isfinite(progress): value = min(99, max(0, int(progress * 100)))
        if value == self.last_progress:
            return
        self.last_progress = value
        self.window.setWindowTitle(self.base_title if value is None else f'{self.base_title} — {value}%')
        app = QtWidgets.QApplication.instance()
        # Qt 6.5+; unsupported desktop integrations safely ignore this request.
        if hasattr(app, 'setBadgeNumber'): app.setBadgeNumber(value or 0)

    def clear(self):
        """Remove process-owned badges and the tray icon on normal shutdown."""
        self.timer.stop()
        self.tray.hide()
        app = QtWidgets.QApplication.instance()
        if hasattr(app, 'setBadgeNumber'): app.setBadgeNumber(0)
        self.window.setWindowTitle(self.base_title)
