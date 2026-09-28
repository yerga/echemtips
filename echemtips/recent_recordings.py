"""Local-only recording discovery and a bounded shared recent-file history."""
from pathlib import Path
from PySide6 import QtCore, QtWidgets
from .models import SettingsStore


def recording_from_urls(urls):
    """Resolve a single recording or its JSON sidecar; reject remote/mixed drops."""
    paths = []
    for url in urls:
        if not url.isLocalFile(): return None
        path = Path(url.toLocalFile()).expanduser().resolve()
        if path.suffix.lower() == '.json': path = path.with_suffix('.csv')
        if path.suffix.lower() not in {'.csv', '.tsv', '.tdms', '.set'} or not path.is_file(): return None
        if path not in paths: paths.append(path)
    return paths[0] if len(paths) == 1 else None


class RecentRecordings(QtCore.QObject):
    """Remember successful loads/saves only; opening always uses the normal loader."""
    def __init__(self, window, parent_menu, opener):
        super().__init__(window)
        self.window, self.opener = window, opener
        self.preferences = QtCore.QSettings(str(SettingsStore().path.with_name('recent-recordings.ini')), QtCore.QSettings.Format.IniFormat)
        self.menu = parent_menu.addMenu('Recent recordings')
        self.menu.aboutToShow.connect(self.rebuild)
        window.setAcceptDrops(True)
        window.installEventFilter(self)

    def paths(self):
        """Read shared history without touching potentially large recordings."""
        self.preferences.sync()
        values = self.preferences.value('paths', [])
        if isinstance(values, str): values = [values]
        return [p for p in values if isinstance(p, str)][:12] if isinstance(values, list) else []

    def remember(self, path):
        """Store a canonical path once, most-recent first, without copying data."""
        path = str(Path(path).expanduser().resolve())
        self.preferences.setValue('paths', [path] + [p for p in self.paths() if p != path][:11])
        self.preferences.sync()

    def clear(self):
        """Forget local history without deleting recordings."""
        self.preferences.remove('paths'); self.preferences.sync()

    def rebuild(self):
        """Show full paths as tooltips; missing files stay visible but disabled."""
        self.menu.clear()
        paths = self.paths()
        for path in paths:
            p = Path(path)
            action = self.menu.addAction(f'{p.name} — {p.parent.name}')
            action.setToolTip(path)
            action.setEnabled(p.is_file())
            action.triggered.connect(lambda checked=False, filename=p: self.opener(filename))
        if not paths: self.menu.addAction('No recent recordings').setEnabled(False)
        self.menu.addSeparator()
        self.menu.addAction('Clear history', self.clear).setEnabled(bool(paths))

    def eventFilter(self, watched, event):
        """Handle a local recording drop without changing acquisition ownership."""
        if event.type() in (QtCore.QEvent.Type.DragEnter, QtCore.QEvent.Type.Drop):
            path = recording_from_urls(event.mimeData().urls())
            if path is not None:
                event.acceptProposedAction()
                if event.type() == QtCore.QEvent.Type.Drop: self.opener(path)
                return True
        return super().eventFilter(watched, event)
