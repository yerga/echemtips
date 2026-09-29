"""Compact opt-in conditioning editor; no permanent potential/time fields."""
from dataclasses import asdict
import json
from PySide6 import QtWidgets as Q, QtCore
from .conditioning import ConditioningParameters


class ConditioningControl(Q.QPushButton):
    """One summary button opens a transactional pre/post hold dialog."""
    def __init__(self,page):
        super().__init__(page)
        self.page=page
        self.values=asdict(ConditioningParameters())
        # Hidden change notifier participates in existing scan-duration refresh.
        self.change=Q.QLineEdit(self); self.change.hide()
        self.clicked.connect(self.edit)
        self.set_values(self.values)

    def set_values(self,values):
        """Validate and apply a complete hold configuration atomically."""
        candidate=ConditioningParameters(**values)
        errors=candidate.validate_conditioning(self.page.app.settings)
        if errors: raise ValueError('\n'.join(errors))
        self.values=asdict(candidate)
        parts=[f'{side.title()} {getattr(candidate,side+"_hold_s"):g} s' for side in ('pre','post') if getattr(candidate,side+'_hold_enabled')]
        self.setText('Pre/post holds… · '+(' · '.join(parts) if parts else 'Off'))
        self.change.setText(json.dumps(self.values,sort_keys=True))

    def edit(self):
        """Open optional hold fields without expanding the experiment page."""
        if self.page.app.any_experiment_active:
            self.page.app.show_error('Stop the experiment before changing conditioning.'); return
        dialog=Q.QDialog(self.page); dialog.setWindowTitle('Optional pre/post measurement holds')
        dialog.resize(540,380); layout=Q.QVBoxLayout(dialog)
        note=Q.QLabel('Applied after contact and settling, once around the complete measurement program at each landing. Disabled holds add no time.'); note.setWordWrap(True); layout.addWidget(note)
        controls={}
        for side in ('pre','post'):
            group=Q.QGroupBox(side.title()+'-measurement hold'); group.setCheckable(True)
            form=Q.QFormLayout(group)
            e=Q.QDoubleSpinBox(); e.setRange(-10,10); e.setDecimals(4); e.setSuffix(' V')
            t=Q.QDoubleSpinBox(); t.setRange(.000001,300); t.setDecimals(6); t.setSuffix(' s')
            e.setValue(self.values[side+'_hold_v']); t.setValue(self.values[side+'_hold_s'])
            form.addRow('Potential E1',e); form.addRow('Duration',t)
            group.setChecked(self.values[side+'_hold_enabled']); layout.addWidget(group)
            controls[side]=(group,e,t)
        preview=Q.QLabel(); preview.setWordWrap(True); preview.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(preview)
        def values():
            """Collect the dialog draft without modifying the experiment."""
            return {key:value for side,(group,e,t) in controls.items() for key,value in [(side+'_hold_enabled',group.isChecked()),(side+'_hold_v',e.value()),(side+'_hold_s',t.value())]}
        def refresh():
            """Describe the full sequence and the final applied potential."""
            try:
                p=self.page.parameters(); sequence=[f'Contact → settling {p.settling_time_s:g} s']
                v=values()
                if v['pre_hold_enabled']: sequence.append(f"Pre: {v['pre_hold_v']:g} V / {v['pre_hold_s']:g} s")
                if hasattr(p,'cv_start_v'):
                    targets=[p.cv_start_v,p.cv_vertex1_v] + ([] if p.waveform=='LSV' else [p.cv_vertex2_v,p.cv_start_v])
                    sequence.append(p.waveform+': '+' → '.join(f'{e:g} V' for e in targets)+f' ({p.cycles} cycle(s))')
                    if getattr(p,'scan_rates_v_s',None): sequence.append('Rates: '+', '.join(f'{r:g}' for r in p.scan_rates_v_s)+' V/s; holds bracket the entire series')
                    end=targets[-1]
                else:
                    sequence.append('I–t: '+ ' → '.join(f'{getattr(p,n+"_potential_v"):g} V / {getattr(p,n+"_hold_s"):g} s' for n in ('initial','step','return'))+f' × {p.cycles}')
                    end=p.return_potential_v
                if v['post_hold_enabled']:
                    sequence.append(f"Post: {v['post_hold_v']:g} V / {v['post_hold_s']:g} s"); end=v['post_hold_v']
                sequence.append(('Retract' if getattr(p,'retract_after',True) else 'Finish without retract')+f' at {end:g} V')
                if getattr(p,'recipes',None): sequence.append('All recipes share these holds; the measurement waveform varies by recipe.')
                preview.setText('\n→ '.join(sequence))
            except (ValueError,AttributeError): preview.setText('Enter valid measurement parameters to preview the full sequence.')
        for group,e,t in controls.values():
            group.toggled.connect(refresh); e.valueChanged.connect(refresh); t.valueChanged.connect(refresh)
        buttons=Q.QDialogButtonBox(Q.QDialogButtonBox.StandardButton.Ok|Q.QDialogButtonBox.StandardButton.Cancel)
        def accept():
            """Commit valid draft values; leave invalid drafts open for repair."""
            try: self.set_values(values())
            except ValueError as exc: Q.QMessageBox.warning(dialog,'Invalid holds',str(exc)); return
            dialog.accept()
        buttons.accepted.connect(accept); buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons); refresh(); dialog.exec()
