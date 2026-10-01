"""Compact transactional editor for independently enabled Z speed profiles."""
import json
from PySide6 import QtWidgets as Q
from .motion_profiles import MotionProfileParameters, PROFILE_FIELDS


class MotionProfileControl(Q.QPushButton):
    """Keep optional speed/distance controls in a dialog with a page summary."""
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.change = Q.QLineEdit(self); self.change.hide()
        self.clicked.connect(self.edit)
        self.set_values({})

    def set_values(self, values):
        """Restore configuration without restoring runtime predictions/events."""
        candidate = MotionProfileParameters(**values)
        self.values = {k:getattr(candidate,k) for k in PROFILE_FIELDS}
        self.setText('Z motion profile… · '+(' / '.join(s.title() for s in ('approach','retract') if self.values[s+'_profile_enabled']) or 'Off'))
        self.change.setText(json.dumps(self.values,sort_keys=True))
        for side,name in (('approach','approach_rate'),('retract','retract_rate')):
            widget = getattr(self.page,name,None)
            if widget is not None: widget.setEnabled(not self.values[side+'_profile_enabled'])

    def edit(self):
        """Edit an independently enabled approach and withdrawal profile."""
        if self.page.app.any_experiment_active:
            self.page.app.show_error('Stop the experiment before changing the motion profile.'); return
        dialog = Q.QDialog(self.page); dialog.setWindowTitle('Z motion profiles')
        layout = Q.QVBoxLayout(dialog); controls = {}
        def spin(key, unit):
            """Build a physical-unit field in the dialog draft."""
            w = Q.QDoubleSpinBox(); w.setDecimals(3)
            w.setRange(.001 if key.endswith('_um_s') else 0,1000 if key.endswith('_um_s') else self.page.app.settings.z_range_um)
            w.setSuffix(' '+unit); w.setValue(self.values[key]); controls[key] = w
            return w
        approach = Q.QGroupBox('Two-speed approach'); approach.setCheckable(True)
        form = Q.QFormLayout(approach)
        form.addRow('Fast rate',spin('approach_fast_um_s','µm/s')); form.addRow('Slow rate',spin('approach_slow_um_s','µm/s'))
        text = Q.QLabel('Slow throughout until contact heights support a surface prediction. Fast motion ends before predicted contact.'); text.setWordWrap(True); form.addRow(text)
        advanced = Q.QGroupBox('Adjust prediction clearance'); advanced.setCheckable(True); advanced.setChecked(False)
        adv = Q.QFormLayout(advanced); margin = spin('approach_clearance_um','µm'); adv.addRow('Clearance before expected contact',margin)
        margin.setToolTip('Must cover unmeasured sample relief; a plane prediction cannot detect hidden obstacles.')
        form.addRow(advanced); approach.setChecked(self.values['approach_profile_enabled']); layout.addWidget(approach)
        retract = Q.QGroupBox('Two-speed retraction'); retract.setCheckable(True); form = Q.QFormLayout(retract)
        form.addRow('Slow rate',spin('retract_slow_um_s','µm/s')); form.addRow('Fast rate',spin('retract_fast_um_s','µm/s'))
        mode = Q.QComboBox(); mode.addItems(['After a distance','After detected detachment']); mode.setCurrentIndex(int(self.values['retract_automatic']))
        form.addRow('Switch to fast motion',mode)
        distance = spin('retract_switch_distance_um','µm'); form.addRow('Slow withdrawal from contact',distance)
        buffer = spin('retract_buffer_um','µm'); form.addRow('Additional withdrawal after detection',buffer)
        note = Q.QLabel('Uses the selected feedback current. If detachment is unclear, stays slow to the configured endpoint.'); note.setWordWrap(True); form.addRow(note)
        def update_mode():
            """Make the fixed distance unavailable in automatic mode."""
            distance.setEnabled(mode.currentIndex()==0); buffer.setEnabled(mode.currentIndex()==1)
        mode.currentIndexChanged.connect(update_mode); update_mode()
        retract.setChecked(self.values['retract_profile_enabled']); layout.addWidget(retract)
        buttons = Q.QDialogButtonBox(Q.QDialogButtonBox.StandardButton.Ok | Q.QDialogButtonBox.StandardButton.Cancel)
        def accept():
            """Validate draft values before applying them to the measurement."""
            values = {k:w.value() for k,w in controls.items()}
            values.update(approach_profile_enabled=approach.isChecked(),retract_profile_enabled=retract.isChecked(),retract_automatic=mode.currentIndex()==1)
            try:
                params = self.page.parameters()
                for k,v in values.items(): setattr(params,k,v)
                errors = params.validate_motion_profiles(self.page.app.settings)
                if errors: raise ValueError('\n'.join(errors))
                self.set_values(values)
            except ValueError as exc:
                Q.QMessageBox.warning(dialog,'Invalid Z profile',str(exc)); return
            dialog.accept()
        buttons.accepted.connect(accept); buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons); dialog.resize(520,620); dialog.exec()
