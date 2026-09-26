"""Recipe planner shared by hopping CV/LSV and I–t pages."""
from copy import deepcopy
from collections import Counter
import json

from PySide6 import QtCore, QtGui, QtWidgets as Q

from .combinatorial import assign, factorial, program_fields
from .qt_common import Card, button, label

CAPTIONS = {
    'cv_start_v': 'Start E (V)', 'cv_vertex1_v': 'Vertex 1 / LSV end (V)',
    'cv_vertex2_v': 'Vertex 2 (V)', 'cv_scan_rate_v_s': 'Scan rate (V/s)',
    'cycles': 'Cycles', 'waveform': 'CV / LSV',
    'initial_potential_v': 'Initial E (V)', 'initial_hold_s': 'Initial time (s)',
    'step_potential_v': 'Pulse E (V)', 'step_hold_s': 'Pulse time (s)',
    'return_potential_v': 'Return E (V)', 'return_hold_s': 'Return time (s)',
}
PALETTE = ('#d1ece8', '#dbe8fa', '#fce4cb', '#e8ddf4', '#f8dce4', '#e4ebca')


class RecipeDialog(Q.QDialog):
    """Edit full recipes and inspect physical assignments before accepting."""
    def __init__(self, params, settings, recipes, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Combinatorial scan — recipes and assignments')
        self.resize(1000, 650)
        self.params, self.settings = params, settings
        self.fields = program_fields(params)
        root = Q.QVBoxLayout(self)
        note = label('Conditions vary only the measurement program. Motion, contact and settling settings are shared. '
                     'Replicates use fresh grid locations; row blocks may confound condition with sample heterogeneity.', word_wrap=True)
        root.addWidget(note)
        tabs = Q.QTabWidget(); root.addWidget(tabs, 1)
        page = Q.QWidget(); layout = Q.QVBoxLayout(page)
        self.table = Q.QTableWidget(0, len(self.fields) + 1)
        self.table.setHorizontalHeaderLabels(['Recipe name'] + [CAPTIONS[f] for f in self.fields])
        self.table.horizontalHeader().setSectionResizeMode(Q.QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(Q.QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)
        actions = Q.QHBoxLayout(); layout.addLayout(actions)
        actions.addWidget(button('Add / duplicate', self.add))
        actions.addWidget(button('Remove', self.remove))
        actions.addWidget(button('Edit selected…', self.edit_selected))
        actions.addWidget(button('Two-factor matrix…', self.matrix))
        actions.addWidget(button('Load recipes…', self.load))
        actions.addWidget(button('Save recipes…', self.save))
        tabs.addTab(page, 'Measurement recipes')
        plan = Q.QWidget(); pl = Q.QVBoxLayout(plan)
        row = Q.QHBoxLayout(); pl.addLayout(row)
        self.mode = Q.QComboBox(); self.mode.addItems(['Row blocks', 'Interleaved', 'Randomized'])
        self.rows = Q.QSpinBox(); self.rows.setRange(1, 64)
        self.seed = Q.QSpinBox(); self.seed.setRange(0, 2147483647)
        for caption, widget in [('Assignment', self.mode), ('Rows per recipe', self.rows), ('Random seed', self.seed)]:
            row.addWidget(Q.QLabel(caption)); row.addWidget(widget)
        pl.addWidget(label('Rows are numbered from Y start toward Y end; columns from X start toward X end. '
                           'Recipes repeat to fill the grid. The marker, if enabled, repeats the last array recipe.', word_wrap=True))
        self.preview = Q.QTableWidget(params.y_points, params.x_points)
        self.preview.setEditTriggers(Q.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview.horizontalHeader().setSectionResizeMode(Q.QHeaderView.ResizeMode.ResizeToContents)
        from .models import ScanHoppingCVParameters
        axis = ScanHoppingCVParameters._axis_values
        self.preview.setHorizontalHeaderLabels([f'X {i+1}: {x:g} µm' for i,x in enumerate(axis(params.x_start_um,params.x_end_um,params.x_points))])
        self.preview.setVerticalHeaderLabels([f'Y {i+1}: {y:g} µm' for i,y in enumerate(axis(params.y_start_um,params.y_end_um,params.y_points))])
        pl.addWidget(self.preview, 1)
        self.summary = label('', word_wrap=True); pl.addWidget(self.summary)
        tabs.addTab(plan, 'Assignment preview')
        import pyqtgraph as pg
        self.profile = pg.PlotWidget(background="w")
        self.profile.setLabel("left", "Potential E1", units="V")
        self.profile.setLabel("bottom", "Program time", units="s")
        self.profile.setMinimumHeight(140); self.profile.setMaximumHeight(180)
        root.addWidget(self.profile)
        self.error = label('', word_wrap=True); root.addWidget(self.error)
        buttons = Q.QDialogButtonBox(Q.QDialogButtonBox.StandardButton.Ok | Q.QDialogButtonBox.StandardButton.Cancel)
        buttons.button(Q.QDialogButtonBox.StandardButton.Ok).setText('Use this plan')
        buttons.accepted.connect(self.accept_plan); buttons.rejected.connect(self.reject); root.addWidget(buttons)
        for recipe in recipes or [{'name': 'Reference', **{f: getattr(params, f) for f in self.fields}}]:
            self.insert(recipe)
        self.table.itemChanged.connect(self.refresh)
        self.table.currentCellChanged.connect(self.refresh)
        self.mode.currentTextChanged.connect(self.refresh)
        self.rows.valueChanged.connect(self.refresh); self.seed.valueChanged.connect(self.refresh)
        self.refresh()

    def insert(self, recipe):
        """Add one complete recipe with a constrained waveform selector."""
        row = self.table.rowCount(); self.table.insertRow(row)
        for col, field in enumerate(('name', *self.fields)):
            if field == 'waveform':
                combo = Q.QComboBox(); combo.addItems(['CV', 'LSV']); combo.setCurrentText(recipe[field])
                combo.currentTextChanged.connect(self.refresh); self.table.setCellWidget(row, col, combo)
            else:
                self.table.setItem(row, col, Q.QTableWidgetItem(str(recipe[field])))

    def edit_selected(self):
        """Edit one condition in a labelled form without horizontal table scrolling."""
        try:
            recipes = self.values(); index = max(0, self.table.currentRow()); recipe = recipes[index]
            dialog = Q.QDialog(self); dialog.setWindowTitle('Edit recipe'); layout = Q.QFormLayout(dialog)
            fields = {}
            for key, value in recipe.items():
                if key == 'waveform':
                    widget = Q.QComboBox(); widget.addItems(['CV', 'LSV']); widget.setCurrentText(value)
                else: widget = Q.QLineEdit(str(value))
                fields[key] = widget; layout.addRow(CAPTIONS.get(key, key.title()), widget)
            def waveform():
                """Disable reverse-sweep fields when the condition is an LSV."""
                lsv = 'waveform' in fields and fields['waveform'].currentText() == 'LSV'
                for key in ('cv_vertex2_v', 'cycles'):
                    if key in fields: fields[key].setEnabled(not lsv)
                if lsv: fields['cycles'].setText('1')
            if 'waveform' in fields: fields['waveform'].currentIndexChanged.connect(waveform)
            waveform()
            actions = Q.QDialogButtonBox(Q.QDialogButtonBox.StandardButton.Ok | Q.QDialogButtonBox.StandardButton.Cancel)
            actions.accepted.connect(dialog.accept); actions.rejected.connect(dialog.reject); layout.addRow(actions)
            if dialog.exec() != Q.QDialog.DialogCode.Accepted: return
            result = {}
            for key, widget in fields.items():
                value = widget.currentText() if key == 'waveform' else widget.text().strip()
                result[key] = value if key in ('name', 'waveform') else int(value) if key == 'cycles' else float(value)
            candidate = deepcopy(self.params); candidate.recipes = [result]; candidate.recipe_assignment = [0] * candidate.point_count
            errors = candidate.validate(self.settings)
            if errors: raise ValueError('\n'.join(errors))
            self.table.blockSignals(True)
            for col, key in enumerate(('name', *self.fields)):
                if key == 'waveform': self.table.cellWidget(index, col).setCurrentText(result[key])
                else: self.table.item(index, col).setText(str(result[key]))
            self.table.blockSignals(False); self.refresh()
        except (ValueError, TypeError) as exc: self.error.setText(str(exc))

    def values(self):
        """Parse explicit values, rejecting partial or malformed table entries."""
        result = []
        for row in range(self.table.rowCount()):
            recipe = {}
            for col, field in enumerate(('name', *self.fields)):
                if field == 'waveform': value = self.table.cellWidget(row, col).currentText()
                else:
                    item = self.table.item(row, col)
                    if item is None: raise ValueError('Complete every recipe field.')
                    value = item.text().strip()
                    if field != 'name': value = int(value) if field == 'cycles' else float(value)
                recipe[field] = value
            result.append(recipe)
        if not result: raise ValueError('Add at least one recipe.')
        return result

    def add(self):
        """Duplicate the selected recipe with a new editable name."""
        try:
            recipes = self.values()
            recipe = dict(recipes[max(0, self.table.currentRow())])
            recipe['name'] = f'Condition {len(recipes)+1}'
            self.table.blockSignals(True); self.insert(recipe); self.table.blockSignals(False); self.refresh()
        except ValueError as exc: self.error.setText(str(exc))

    def remove(self):
        """Remove one recipe while retaining at least one condition."""
        if self.table.rowCount() > 1:
            self.table.removeRow(max(0, self.table.currentRow())); self.refresh()

    def refresh(self, *_):
        """Preview repeat counts and the actual physical condition layout."""
        if not hasattr(self, 'summary'): return
        blocker = QtCore.QSignalBlocker(self.table)
        if 'waveform' in self.fields:
            columns = ('name', *self.fields)
            for row in range(self.table.rowCount()):
                lsv = self.table.cellWidget(row, columns.index('waveform')).currentText() == 'LSV'
                for key in ('cv_vertex2_v', 'cycles'):
                    item = self.table.item(row, columns.index(key))
                    flags = item.flags()
                    item.setFlags(flags & ~QtCore.Qt.ItemFlag.ItemIsEditable if lsv else flags | QtCore.Qt.ItemFlag.ItemIsEditable)
                    item.setForeground(QtGui.QColor('#8a98a4' if lsv else '#182b3a'))
                    item.setToolTip('Not used by an LSV (one forward sweep)' if lsv else '')
        del blocker
        self.rows.setEnabled(self.mode.currentText() == 'Row blocks')
        self.seed.setEnabled(self.mode.currentText() == 'Randomized')
        try:
            recipes = self.values()
            assignment = assign(self.params.x_points, self.params.y_points, len(recipes), self.mode.currentText(), self.rows.value(), self.seed.value())
            for index, condition in enumerate(assignment):
                recipe = recipes[condition]
                item = Q.QTableWidgetItem(f'{condition+1}: {recipe["name"]}')
                item.setBackground(QtGui.QColor(PALETTE[condition % len(PALETTE)]))
                item.setForeground(QtGui.QColor('#182b3a'))
                item.setToolTip('\n'.join(f'{CAPTIONS.get(k,k)}: {v}' for k,v in recipe.items()))
                row, col = divmod(index, self.params.x_points)
                hop = row * self.params.x_points + (self.params.x_points - 1 - col if self.params.serpentine and row % 2 else col) + 1
                item.setText(item.text() + f'\nHop {hop}')
                self.preview.setItem(row, col, item)
            counts = Counter(assignment)
            self.summary.setText('Fresh-site replicates: ' + '; '.join(f'{r["name"]}: {counts[i]}' for i,r in enumerate(recipes)))
            self.error.setText('')
            self.draw_profile(recipes[max(0, self.table.currentRow())])
        except (ValueError, AttributeError) as exc: self.error.setText(str(exc))

    def draw_profile(self, recipe):
        """Show the selected recipe on a physical time/potential axis."""
        t, e = [0.0], []
        if 'cv_start_v' in recipe:
            e = [recipe['cv_start_v']]
            targets = ([recipe['cv_vertex1_v']] if recipe['waveform'] == 'LSV' else
                       [recipe['cv_vertex1_v'], recipe['cv_vertex2_v'], recipe['cv_start_v']])
            if recipe['cv_scan_rate_v_s'] <= 0 or not 1 <= recipe['cycles'] <= 100: return
            for _ in range(recipe['cycles']):
                for target in targets:
                    t.append(t[-1] + abs(target-e[-1])/recipe['cv_scan_rate_v_s']); e.append(target)
        else:
            e = [recipe['initial_potential_v']]
            if not 1 <= recipe['cycles'] <= 100: return
            for _ in range(recipe['cycles']):
                for level, hold in [('initial_potential_v','initial_hold_s'),('step_potential_v','step_hold_s'),('return_potential_v','return_hold_s')]:
                    t.extend([t[-1],t[-1]+recipe[hold]]); e.extend([recipe[level]]*2)
        self.profile.clear()
        self.profile.setTitle(recipe['name'])
        self.profile.plot(t,e,pen={'color':'#008f87','width':2})

    def matrix(self):
        """Generate a Cartesian product from two comma-separated factor lists."""
        dialog = Q.QDialog(self); dialog.setWindowTitle('Generate two-factor recipes')
        form = Q.QFormLayout(dialog)
        choices = [f for f in self.fields if f not in {'waveform', 'cycles'}]
        first, second = Q.QComboBox(), Q.QComboBox()
        for widget in (first, second):
            for field in choices: widget.addItem(CAPTIONS[field], field)
        second.setCurrentIndex(1)
        values, other = Q.QLineEdit(), Q.QLineEdit()
        values.setPlaceholderText('Comma-separated values'); other.setPlaceholderText('Comma-separated values')
        form.addRow('Factor A', first); form.addRow('Values A', values)
        form.addRow('Factor B', second); form.addRow('Values B', other)
        form.addRow(Q.QLabel('Replaces the recipe table; other fields use the selected recipe.'))
        buttons = Q.QDialogButtonBox(Q.QDialogButtonBox.StandardButton.Ok | Q.QDialogButtonBox.StandardButton.Cancel)
        form.addRow(buttons); buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        if dialog.exec() != Q.QDialog.DialogCode.Accepted: return
        try:
            recipes = factorial(self.values()[max(0,self.table.currentRow())], first.currentData(),
                                [float(v) for v in values.text().split(',')], second.currentData(),
                                [float(v) for v in other.text().split(',')])
            self.table.blockSignals(True); self.table.setRowCount(0)
            for recipe in recipes: self.insert(recipe)
            self.table.blockSignals(False); self.refresh()
        except ValueError as exc: self.error.setText(str(exc))

    def save(self):
        """Export reusable complete recipes, without instrument settings."""
        from pathlib import Path
        try:
            payload = {'schema': 1, 'fields': list(self.fields), 'recipes': self.values()}
            path, _ = Q.QFileDialog.getSaveFileName(self, 'Save recipes', '', 'JSON (*.json)')
            if path: Path(path).write_text(json.dumps(payload, indent=2, allow_nan=False), encoding='utf-8')
        except (OSError, ValueError) as exc: self.error.setText(str(exc))

    def load(self):
        """Validate a recipe file before replacing the editor contents."""
        from pathlib import Path
        path, _ = Q.QFileDialog.getOpenFileName(self, 'Load recipes', '', 'JSON (*.json)')
        if not path: return
        try:
            payload = json.loads(Path(path).read_text(encoding='utf-8'))
            if payload.get('schema') != 1 or payload.get('fields') != list(self.fields):
                raise ValueError('Recipe file belongs to another waveform family or schema.')
            recipes = payload['recipes']
            test = deepcopy(self.params); test.recipes = recipes
            test.recipe_assignment = [0] * test.point_count
            errors = test.validate(self.settings)
            if errors: raise ValueError('\n'.join(errors))
            self.table.blockSignals(True); self.table.setRowCount(0)
            for recipe in recipes: self.insert(recipe)
            self.table.blockSignals(False); self.refresh()
        except (OSError, ValueError, KeyError, TypeError) as exc: self.error.setText(str(exc))

    def accept_plan(self):
        """Validate all conditions and require every recipe to be sampled."""
        try:
            self.result_recipes = self.values()
            self.result_assignment = assign(self.params.x_points, self.params.y_points, len(self.result_recipes), self.mode.currentText(), self.rows.value(), self.seed.value())
            if len(set(self.result_assignment)) < len(self.result_recipes):
                raise ValueError('Some recipes have no landings. Increase the grid or reduce recipes / row-block size.')
            candidate = deepcopy(self.params); candidate.recipes = self.result_recipes; candidate.recipe_assignment = self.result_assignment
            errors = candidate.validate(self.settings)
            if errors: raise ValueError('\n'.join(errors))
            self.accept()
        except (ValueError, TypeError) as exc: self.error.setText(str(exc))


def install_planner(page, layout, default_card, preview_card):
    """Attach a mandatory recipe planner to a dedicated combinatorial page."""
    original = page.parameters
    page._recipe_plan = None
    card = Card('Combinatorial scan', '')
    box = Q.QVBoxLayout(card.body)
    summary = label('Configure recipes before starting.', word_wrap=True); box.addWidget(summary)
    edit = button('Plan recipes and assignments…', lambda: open_plan())
    box.addWidget(edit); layout.insertWidget(2, card)

    def parameters():
        """Attach a frozen, geometry-checked plan to the scan parameters."""
        params = original()
        if page._recipe_plan is None: raise ValueError('Configure the combinatorial plan first.')
        recipes, assignment, shape = page._recipe_plan
        if shape != (params.x_points, params.y_points):
            raise ValueError('Grid size changed. Reopen the recipe planner to confirm assignments.')
        params.recipes, params.recipe_assignment = deepcopy(recipes), list(assignment)
        options = getattr(page, "_recipe_options", ("Interleaved", 1, 0))
        params.recipe_design = dict(schema=1, assignment=options[0], rows_per_recipe=options[1], seed=options[2], indexing="physical row-major")
        return params

    def open_plan():
        """Edit a copy of the plan only while the instrument is idle."""
        if page.app.any_experiment_active:
            page.app.show_error('Stop the experiment before editing its plan.'); return
        try:
            params = original()
            if not 1 <= params.x_points <= 64 or not 1 <= params.y_points <= 64: raise ValueError('Use 1–64 points per axis.')
            dialog = RecipeDialog(params, page.app.settings, page._recipe_plan[0] if page._recipe_plan else [], page)
            previous = getattr(page, '_recipe_options', None)
            if previous:
                dialog.mode.setCurrentText(previous[0]); dialog.rows.setValue(previous[1]); dialog.seed.setValue(previous[2])
            if dialog.exec() == Q.QDialog.DialogCode.Accepted:
                page._recipe_options = (dialog.mode.currentText(), dialog.rows.value(), dialog.seed.value())
                page._recipe_plan = (dialog.result_recipes, dialog.result_assignment, (params.x_points, params.y_points))
                sync()
        except (ValueError, TypeError) as exc: page.app.show_error(str(exc))

    def sync():
        """Keep the shared-program controls and plan summary unambiguous."""
        if page._recipe_plan:
            recipes, assignment, _ = page._recipe_plan
            counts = Counter(assignment)
            summary.setText('; '.join(f'{r["name"]}: {counts[i]} landings' for i,r in enumerate(recipes)) + '\nMap values may reflect different conditions; compare like recipes in Analysis.')
        else: summary.setText('Configure recipes before starting.')
        try:
            duration = parameters().estimated_known_duration_s()
            page.duration_label.setText(f'Estimated known duration: {duration / 60:.1f} min + first approach / positioning')
        except (ValueError, ZeroDivisionError, AttributeError):
            pass
    default_card.hide()
    preview_card.hide()
    if hasattr(page, 'map_v'):
        map_card = Card('Current map', '')
        map_layout = Q.QVBoxLayout(map_card.body)
        map_layout.addWidget(page.map_v)
        layout.insertWidget(3, map_card)
    page.parameters = parameters
    page.recipe_edit_button = edit
    page.refresh_recipe_summary = sync
