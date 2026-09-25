"""Persistent, hardware-independent control layouts and experiment presets."""
import json
from pathlib import Path
from copy import deepcopy
from PySide6 import QtCore, QtWidgets as Q
from .qt_common import Field, Heatmap


def form_values(page):
    """Serialize named form controls only, never runtime or instrument objects."""
    result = {}
    for name, widget in vars(page).items():
        if isinstance(widget, Field): result[name] = widget.entry.text()
        elif isinstance(widget, Q.QComboBox): result[name] = widget.currentText()
        elif isinstance(widget, Q.QCheckBox): result[name] = widget.isChecked()
    if hasattr(page, 'rate_editor'): result['scan_rates'] = page.rate_editor.values()
    if getattr(page, 'combinatorial', False):
        result['recipe_plan'] = deepcopy(page._recipe_plan)
        result['recipe_options'] = getattr(page, '_recipe_options', None)
    return result


def restore_form(page, values):
    """Restore known fields only; callers validate the complete configuration."""
    for name, value in values.items():
        widget = getattr(page, name, None)
        if isinstance(widget, Field): widget.entry.setText(str(value))
        elif isinstance(widget, Q.QComboBox):
            if widget.findText(str(value)) < 0: raise ValueError(f'Unknown choice for {name}: {value}')
            widget.setCurrentText(str(value))
        elif isinstance(widget, Q.QCheckBox):
            if not isinstance(value, bool): raise ValueError(f'Expected a checkbox value for {name}')
            widget.setChecked(value)
    if 'scan_rates' in values and hasattr(page, 'rate_editor'): page.rate_editor.set_rates(values['scan_rates'])
    if getattr(page, 'combinatorial', False):
        plan = values.get('recipe_plan')
        page._recipe_plan = (plan[0], plan[1], tuple(plan[2])) if plan else None
        page._recipe_options = values.get('recipe_options') or ('Interleaved', 1, 0)
        if hasattr(page, 'refresh_recipe_summary'): page.refresh_recipe_summary()


def preset(page, save):
    """Save or load a measurement preset, rejecting incompatible conventions."""
    if page.app.any_experiment_active:
        page.app.show_error('Stop the experiment before changing presets.'); return
    dialog = Q.QFileDialog.getSaveFileName if save else Q.QFileDialog.getOpenFileName
    path, _ = dialog(page, 'Save measurement preset' if save else 'Load measurement preset', '', 'Measurement preset (*.json)')
    if not path: return
    before = form_values(page)
    try:
        if save:
            params = page.parameters(); errors = params.validate(page.app.settings)
            if errors: raise ValueError('\n'.join(errors))
            payload = dict(schema=1, kind='echemtips-measurement-preset', experiment=page.recording_name,
                           polarity=page.app.settings.polarity_convention, controls=before)
            output = QtCore.QSaveFile(path)
            if not output.open(QtCore.QIODevice.OpenModeFlag.WriteOnly): raise OSError(output.errorString())
            data = json.dumps(payload, indent=2, allow_nan=False).encode()
            if output.write(data) != len(data) or not output.commit(): raise OSError(output.errorString())
        else:
            payload = json.loads(Path(path).read_text())
            if payload.get('kind') != 'echemtips-measurement-preset' or payload.get('schema') != 1: raise ValueError('Not a supported measurement preset.')
            if payload.get('experiment') != page.recording_name: raise ValueError('This preset belongs to a different experiment.')
            if payload.get('polarity') != page.app.settings.polarity_convention: raise ValueError('Preset polarity differs. Review and recreate it in the required convention; signs are not converted automatically.')
            restore_form(page, payload['controls'])
            errors = page.parameters().validate(page.app.settings)
            if errors: raise ValueError('\n'.join(errors))
        page.app.toast('Preset saved' if save else 'Preset loaded and validated; no movement commanded', 'success')
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        restore_form(page, before); page.app.show_error(str(exc))


class LayoutWorkspace(QtCore.QObject):
    """Remember layout choices independently from experimental parameters."""
    def __init__(self, app):
        super().__init__(app); self.app = app
        self.store = QtCore.QSettings(str(app.store.path.with_name('workspace.ini')), QtCore.QSettings.Format.IniFormat)
        self.splitters = {}
        view = app.menuBar().addMenu('View')
        sidebar = app.findChild(Q.QFrame, 'sidebar')
        toggle = view.addAction('Show sidebar'); toggle.setCheckable(True); toggle.setChecked(True)
        toggle.setShortcut('Ctrl+B'); toggle.toggled.connect(sidebar.setVisible)
        view.addAction('Experiment library…', app.open_library).setShortcut('Ctrl+K')
        for name, page in app.pages.items():
            if not getattr(page, 'experiment_key', ''): continue
            root = page.body.layout()
            if root.count() == 2:
                setup, plots = root.takeAt(0).widget(), root.takeAt(0).widget()
                page.setup_panel = setup
                setup.setMaximumWidth(16777215)
                split = Q.QSplitter(); split.setChildrenCollapsible(False)
                split.addWidget(setup); split.addWidget(plots); split.setStretchFactor(1, 1)
                root.addWidget(split); self.splitters[name] = split
                sizes = self.store.value(name + '/sizes')
                split.setSizes([int(x) for x in sizes] if sizes else [400, 650])
            if hasattr(page, 'parameters'):
                menu_button = Q.QToolButton(); menu_button.setText('Preset…'); menu = Q.QMenu(menu_button)
                menu.addAction('Save / duplicate preset…', lambda checked=False, p=page: preset(p, True))
                menu.addAction('Load preset…', lambda checked=False, p=page: preset(p, False))
                menu_button.setMenu(menu); menu_button.setPopupMode(Q.QToolButton.ToolButtonPopupMode.InstantPopup)
                page.layout().itemAt(0).layout().addWidget(menu_button)
            tabs = page.body.findChild(Q.QTabWidget)
            if tabs:
                for index in range(tabs.count()):
                    panel = tabs.widget(index)
                    if tabs.tabText(index) != 'Maps' or not hasattr(panel, '_cards'): continue
                    selector = Q.QComboBox(); selector.addItems(['Both maps', 'Contact Z only', 'Current only'])
                    selector.setToolTip('Choose one large map or compare both')
                    tabs.setCornerWidget(selector)
                    def choose(value, panel=panel):
                        """Change map visibility without clearing acquired results."""
                        for n, card in enumerate(panel._cards): card.setVisible(value == 0 or n == value - 1)
                        panel._reflow()
                    selector.currentIndexChanged.connect(choose)
                    tabs.currentChanged.connect(lambda value, selector=selector, index=index: selector.setVisible(value == index))
                    selector.setVisible(tabs.currentIndex() == index)
                tabs.setCurrentIndex(int(self.store.value(name + '/tab', 0)))
        geometry = self.store.value('geometry')
        if geometry: app.restoreGeometry(geometry)

    def save(self):
        """Persist window geometry and per-experiment split/tab choices."""
        self.store.setValue('geometry', self.app.saveGeometry())
        for name, splitter in self.splitters.items():
            self.store.setValue(name + '/sizes', splitter.sizes())
            tabs = self.app.pages[name].body.findChild(Q.QTabWidget)
            if tabs: self.store.setValue(name + '/tab', tabs.currentIndex())
        self.store.sync()
