"""Explicit, reversible E1 reference conversion for offline analysis only.

Nominal aqueous values at 25 °C; not a calibration of a SECCM quasi-reference.
The transformation is E_new = sign * E_recorded + E_source(SHE) - E_target(SHE)
+ custom_offset. Current signs and E2 are deliberately unchanged.
"""
from copy import deepcopy
import math
from PySide6 import QtWidgets as Q
from .analysis_core import AnalysisDataset, NumericRows

REFERENCE_SOURCES = (
    'https://mcauleygroup.net/article/1/',
    'https://www.metrohm.cn/content/dam/metrohm/shared/documents/fact-sheets/81098050EN.pdf',
    'https://doi.org/10.1002/anie.202102803',
)
REFERENCES = {'SHE': 0.0, 'RHE': None, 'Ag/AgCl (saturated KCl)': .197,
              'Ag/AgCl (3 mol/L KCl)': .207, 'SCE (saturated KCl)': .241}
# Only absolute E1 program potentials: amplitudes, E2, rates and timings must not shift.
POTENTIAL_KEYS = {'start_v','vertex1_v','vertex2_v','cv_start_v','cv_vertex1_v','cv_vertex2_v',
    'approach_potential_v','initial_potential_v','step_potential_v','return_potential_v',
    'map_potential_v','objective_potential_v','end_v','dc_potential_v','approach_voltage_v'}


def reference_potential(name, ph=0):
    """Return nominal volts vs SHE at 25 °C, with RHE's Nernst pH dependence."""
    if name not in REFERENCES: raise ValueError('Unknown reference electrode.')
    if name=='RHE':
        if not math.isfinite(ph) or not 0<=ph<=14: raise ValueError('Enter an aqueous pH from 0 to 14.')
        return -0.0591593497*ph
    return REFERENCES[name]


def conversion(config):
    """Validate an explicit recorded-polarity choice and return affine coefficients."""
    sign={'IUPAC':1,'Instrument-native':-1}.get(config.get('input_polarity'))
    if sign is None:raise ValueError('Confirm the recorded E1 polarity before converting.')
    offset=float(config.get('custom_offset_v',0))
    if not math.isfinite(offset):raise ValueError('Custom offset must be finite.')
    if config.get('mode','database')=='database':
        offset+=reference_potential(config['source'],float(config.get('source_ph',0)))-reference_potential(config['target'],float(config.get('target_ph',0)))
    elif config.get('mode')!='custom':raise ValueError('Unknown conversion mode.')
    return sign,offset


def convert_dataset(dataset, config):
    """Return a converted copy; retain source data, E2, current signs and identities."""
    if not config or not config.get('enabled'):return dataset
    if dataset.metadata.get('analysis_reference'):raise ValueError('Always convert from the original recording, not a converted copy.')
    if 'voltage1_v' not in dataset.columns:raise ValueError('This recording has no E1 potential channel.')
    sign,offset=conversion(config)
    matrix=dataset.rows.matrix.copy();matrix[:,dataset.columns.index('voltage1_v')]=sign*matrix[:,dataset.columns.index('voltage1_v')]+offset
    metadata=deepcopy(dataset.metadata)
    def transform(value):
        """Transform waveform endpoints, including nested combinatorial recipes."""
        if isinstance(value,dict):
            for key,item in value.items():
                if key in POTENTIAL_KEYS and isinstance(item,(int,float)):value[key]=sign*item+offset
                else:transform(item)
        elif isinstance(value,list):
            for item in value:transform(item)
    transform(metadata.get('parameters',{}))
    target=config['target'] if config.get('mode','database')=='database' else config.get('custom_label','Custom reference')
    if not str(target).strip():raise ValueError('Name the output reference scale.')
    metadata['analysis_reference']={**config,'target_label':target,'sign':sign,'offset_v':offset,
        'temperature_c':25 if config.get('mode','database')=='database' else None,
        'scope':'E1 only; output potential uses IUPAC polarity; currents and E2 unchanged',
        'sources':list(REFERENCE_SOURCES)}
    return AnalysisDataset(dataset.path,dataset.columns,NumericRows(dataset.columns,matrix),metadata)


def potential_label(text, dataset):
    """Annotate only E1 axes, preserving current and secondary-potential labels."""
    reference=dataset.metadata.get('analysis_reference') if dataset else None
    if reference and ('Potential E1' in text or 'voltage1_v' in text):
        return text.replace('(V)',f"(V vs {reference['target_label']})")
    return text


class ReferenceDialog(Q.QDialog):
    """Keep reference choices in one optional dialog, with an explicit offset preview."""
    def __init__(self, parent, config, dataset):
        super().__init__(parent);self.setWindowTitle('Potential reference · analysis only')
        self.resize(570,480);form=Q.QFormLayout(self);config=config or {}
        self.enabled=Q.QCheckBox('Convert E1 potential');self.enabled.setChecked(config.get('enabled',False));form.addRow(self.enabled)
        self.mode=Q.QComboBox();self.mode.addItem('Reference database (aqueous, 25 °C)','database');self.mode.addItem('Custom additive offset','custom')
        self.mode.setCurrentIndex(max(0,self.mode.findData(config.get('mode','database'))));form.addRow('Conversion',self.mode)
        self.polarity=Q.QComboBox();self.polarity.addItems(['Confirm recorded polarity…','IUPAC','Instrument-native'])
        stored=(dataset.metadata.get('settings') or {}).get('polarity_convention','') if dataset else ''
        self.polarity.setCurrentText(config.get('input_polarity',stored));form.addRow('Recorded E1 polarity',self.polarity)
        self.source=Q.QComboBox();self.target=Q.QComboBox()
        for widget,key,default in ((self.source,'source','Ag/AgCl (saturated KCl)'),(self.target,'target','RHE')):
            widget.addItems(REFERENCES);widget.setCurrentText(config.get(key,default));form.addRow('Recorded vs' if key=='source' else 'Convert to',widget)
        self.source_ph=Q.QDoubleSpinBox();self.target_ph=Q.QDoubleSpinBox()
        for widget,key,title in ((self.source_ph,'source_ph','Recorded RHE pH'),(self.target_ph,'target_ph','Target RHE pH')):
            widget.setRange(0,14);widget.setDecimals(3);widget.setValue(config.get(key,7));form.addRow(title,widget)
        self.offset=Q.QDoubleSpinBox();self.offset.setRange(-100,100);self.offset.setDecimals(6);self.offset.setSuffix(' V');self.offset.setValue(config.get('custom_offset_v',0));form.addRow('Additional offset (+ adds to E)',self.offset)
        self.custom_label=Q.QLineEdit(config.get('custom_label','Custom reference'));form.addRow('Custom output reference',self.custom_label)
        note=Q.QLabel('Enew = sign × Erecorded + Esource(SHE) − Etarget(SHE) + offset.\n'
            'Database values are nominal, not calibrated. A bare Ag/AgCl wire in the pipette is not automatically a saturated-KCl reference. Use a measured custom offset for quasi-references. No junction-potential or iR correction is applied.\n'
            'Only E1 changes; currents keep their recorded sign. Original files are untouched.')
        note.setWordWrap(True);form.addRow(note)
        self.preview=Q.QLabel();self.preview.setWordWrap(True);form.addRow(self.preview)
        buttons=Q.QDialogButtonBox(Q.QDialogButtonBox.StandardButton.Ok|Q.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept);buttons.rejected.connect(self.reject);form.addRow(buttons)
        for widget in (self.mode,self.polarity,self.source,self.target):widget.currentIndexChanged.connect(self._preview)
        for widget in (self.source_ph,self.target_ph,self.offset):widget.valueChanged.connect(self._preview)
        self.enabled.toggled.connect(self._preview);self._preview()

    def configuration(self):
        """Return portable JSON-compatible reference choices."""
        return dict(enabled=self.enabled.isChecked(),mode=self.mode.currentData(),input_polarity=self.polarity.currentText(),
            source=self.source.currentText(),target=self.target.currentText(),source_ph=self.source_ph.value(),target_ph=self.target_ph.value(),
            custom_offset_v=self.offset.value(),custom_label=self.custom_label.text().strip())

    def _preview(self,*_):
        database=self.mode.currentData()=='database'
        for widget in (self.source,self.target):widget.setEnabled(database)
        self.source_ph.setEnabled(database and self.source.currentText()=='RHE')
        self.target_ph.setEnabled(database and self.target.currentText()=='RHE');self.custom_label.setEnabled(not database)
        try:
            sign,offset=conversion(self.configuration())
            self.preview.setText(f'E1 output = {sign:+d} × recorded E1 {offset:+.6f} V' if self.enabled.isChecked() else 'Reference conversion is off.')
        except (ValueError,KeyError) as exc:self.preview.setText(str(exc))

    def _accept(self):
        try:
            config=self.configuration()
            if config['enabled']:
                conversion(config)
                if config['mode']=='custom' and not config['custom_label']:raise ValueError('Name the custom output reference.')
        except (ValueError,KeyError) as exc:Q.QMessageBox.warning(self,'Reference conversion',str(exc));return
        self.accept()


def edit_reference(window):
    """Apply changes through the background loader; never compound offsets."""
    if window.loading:return
    dialog=ReferenceDialog(window,window.reference_config,window.source_dataset)
    if dialog.exec()!=Q.QDialog.DialogCode.Accepted:return
    window.reference_config=dialog.configuration()
    if window.source_dataset is not None:
        window.load_recording(window.source_dataset.path,source=window.source_dataset)
