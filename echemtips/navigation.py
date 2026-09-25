"""Experiment catalog and persistent, hardware-independent navigation choices."""
from dataclasses import dataclass
import json
from pathlib import Path

from PySide6 import QtCore, QtWidgets


@dataclass(frozen=True)
class ExperimentEntry:
    """Declarative page registration; adding a page needs no sidebar changes."""
    name: str
    category: str
    description: str
    page_factory: str
    experiment_key: str = ""


EXPERIMENTS = (
    ExperimentEntry("Adaptive hopping + LSV", "Scanning", "Bayesian spatial search with conservative tilt survey, contact-gated LSV and an auditable decision log.", "AdaptivePage", "adaptive"),
    ExperimentEntry("Watch current", "Monitoring", "Monitor both current channels over time.", "WatchPage"),
    ExperimentEntry("Watch position", "Monitoring", "Monitor measured X, Y and Z positions.", "WatchPositionPage"),
    ExperimentEntry("Preflight", "Setup and diagnostics", "Guided checks before an experiment.", "PreflightPage"),
    ExperimentEntry("Characterize pipette", "Setup and diagnostics", "Measure pipette resistance and response.", "PipetteCharacterizationPage"),
    ExperimentEntry("CV", "Single-point", "Standalone cyclic voltammetry (CV) or linear sweep voltammetry (LSV) without an approach.", "StandaloneCVPage", "cv"),
    ExperimentEntry("Approach", "Single-point", "Approach a surface using current feedback.", "StandaloneApproachPage", "approach"),
    ExperimentEntry("Approach + CV", "Single-point", "Detect contact, then record CV or one-way LSV.", "ApproachCVPage", "approach_cv"),
    ExperimentEntry("Approach + CV scan-rate series", "Single-point", "One contact, then CV or LSV at an ordered list of scan rates.", "ApproachCVSeriesPage", "approach_cv_series"),
    ExperimentEntry("Approach + I-t", "Single-point", "Detect contact, then measure potential steps and current over time.", "ApproachITPage", "approach_it"),
    ExperimentEntry("Scan hopping + CV", "Scanning", "Record CV or one-way LSV at each surface hop; optional combinatorial recipes, parameter matrices and randomized conditions.", "ScanHoppingCVPage", "scan_cv"),
    ExperimentEntry("Scan hopping + I-t", "Scanning", "Record potential steps and current over time at each hop; optional combinatorial recipes and randomized conditions.", "ScanHoppingITPage", "scan_it"),
    ExperimentEntry("Move piezo", "Setup and diagnostics", "Manually position X, Y and Z piezos.", "MovePiezoPage"),
    ExperimentEntry("Settings", "Setup and diagnostics", "Instrument, recording and display preferences.", "SettingsPage"),
)
DEFAULT_FAVORITES = ["Watch current", "Watch position", "Approach + CV", "Approach + I-t",
                     "Scan hopping + CV", "Scan hopping + I-t"]
ANCHORED = {"Move piezo", "Settings"}


class FavoriteStore:
    """Save ordered sidebar favorites independently of instrument settings."""
    def __init__(self, settings_path):
        self.path = Path(settings_path).with_name("navigation.json")

    def load(self):
        """Recover valid favorites, keeping an intentionally empty list empty."""
        try:
            values = json.loads(self.path.read_text(encoding="utf-8"))["favorites"]
            if not isinstance(values, list):
                raise ValueError("Invalid favorites")
            known = {entry.name for entry in EXPERIMENTS} - ANCHORED
            return list(dict.fromkeys(value for value in values if isinstance(value, str) and value in known))
        except (OSError, ValueError, KeyError, TypeError):
            return list(DEFAULT_FAVORITES)

    def save(self, favorites):
        """Atomically persist preferences; failures leave the previous file intact."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        output = QtCore.QSaveFile(str(self.path))
        if not output.open(QtCore.QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(output.errorString())
        payload = (json.dumps({"favorites": favorites}, indent=2) + "\n").encode()
        if output.write(payload) != len(payload):
            output.cancelWriting()
            raise OSError(output.errorString())
        if not output.commit():
            raise OSError(output.errorString())


class ExperimentLibrary(QtWidgets.QDialog):
    """Search all registered pages and customize the ordered favorite shortcuts."""
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.setWindowTitle("Experiment library")
        self.resize(700, 540)
        layout = QtWidgets.QVBoxLayout(self)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search experiments, tools or descriptions…")
        self.category = QtWidgets.QComboBox()
        self.category.addItems(["All categories", *dict.fromkeys(e.category for e in EXPERIMENTS)])
        layout.addWidget(self.search); layout.addWidget(self.category)
        self.results = QtWidgets.QTreeWidget()
        self.results.setHeaderLabels(["Experiment / tool", "Category", "Sidebar"])
        self.results.setRootIsDecorated(False)
        self.results.setStyleSheet("QTreeWidget::item { padding: 4px 2px; }")
        layout.addWidget(self.results, 1)
        self.description = QtWidgets.QLabel()
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        actions = QtWidgets.QHBoxLayout()
        self.open_button = QtWidgets.QPushButton("Open")
        self.pin_button = QtWidgets.QPushButton("Pin to sidebar")
        self.up_button = QtWidgets.QPushButton("Move up")
        self.down_button = QtWidgets.QPushButton("Move down")
        reset = QtWidgets.QPushButton("Restore defaults")
        for button in (self.open_button, self.pin_button, self.up_button, self.down_button, reset):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.search.textChanged.connect(self.refresh)
        self.category.currentIndexChanged.connect(self.refresh)
        self.results.currentItemChanged.connect(self._selected)
        self.results.itemDoubleClicked.connect(self._open)
        self.open_button.clicked.connect(self._open)
        self.pin_button.clicked.connect(self._pin)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        reset.clicked.connect(lambda: self._save(list(DEFAULT_FAVORITES)))
        self.refresh()

    def _name(self):
        item = self.results.currentItem()
        return item.data(0, QtCore.Qt.ItemDataRole.UserRole) if item else None

    def refresh(self, *_args):
        """Filter the catalog without instantiating or starting experiments."""
        selected = self._name()
        query = self.search.text().casefold().replace("–", "-")
        self.results.clear()
        for entry in EXPERIMENTS:
            if self.category.currentIndex() and entry.category != self.category.currentText():
                continue
            if not all(word in f"{entry.name} {entry.category} {entry.description}".casefold() for word in query.split()):
                continue
            pinned = "Always visible" if entry.name in ANCHORED else (
                f"★ {self.app.favorites.index(entry.name) + 1}" if entry.name in self.app.favorites else "")
            item = QtWidgets.QTreeWidgetItem([entry.name.replace("I-t", "I–t"), entry.category, pinned])
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, entry.name)
            self.results.addTopLevelItem(item)
            if entry.name == selected:
                self.results.setCurrentItem(item)
        if self.results.currentItem() is None and self.results.topLevelItemCount():
            self.results.setCurrentItem(self.results.topLevelItem(0))
        self.results.resizeColumnToContents(0)
        self.results.resizeColumnToContents(1)
        self._selected()

    def _selected(self, *_args):
        name = self._name()
        entry = next((e for e in EXPERIMENTS if e.name == name), None)
        self.description.setText(entry.description if entry else "No matching experiments.")
        self.open_button.setEnabled(entry is not None)
        self.pin_button.setEnabled(entry is not None and name not in ANCHORED)
        self.pin_button.setText("Unpin from sidebar" if name in self.app.favorites else "Pin to sidebar")
        index = self.app.favorites.index(name) if name in self.app.favorites else -1
        self.up_button.setEnabled(index > 0)
        self.down_button.setEnabled(0 <= index < len(self.app.favorites) - 1)

    def _open(self, *_args):
        if self._name():
            self.app.show_page(self._name())
            self.close()

    def _save(self, favorites):
        self.app.set_favorites(favorites)
        self.refresh()

    def _pin(self):
        name = self._name()
        if name and name not in ANCHORED:
            values = list(self.app.favorites)
            values.remove(name) if name in values else values.append(name)
            self._save(values)

    def _move(self, direction):
        name = self._name()
        if name not in self.app.favorites:
            return
        values = list(self.app.favorites)
        index = values.index(name)
        target = index + direction
        if 0 <= target < len(values):
            values[index], values[target] = values[target], values[index]
            self._save(values)
