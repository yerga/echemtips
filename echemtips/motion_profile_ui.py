"""Optional Z speed profiles embedded in the motion setup card."""
from .motion_profiles import MotionProfileParameters, PROFILE_FIELDS
from .optional_setup import OptionalSetup
from .qt_common import Choice, label
from PySide6 import QtWidgets as Q


class MotionProfileControl(OptionalSetup):
    """Independent approach and withdrawal controls in a collapsible form."""

    def __init__(self, page):
        super().__init__(page, 'Z motion profiles')
        self._check('approach_profile_enabled', 'Two-speed approach', 0)
        self._field('approach_fast_um_s', 'Fast approach rate', 15, 'µm/s', 1, 0)
        self._field('approach_slow_um_s', 'Slow approach rate', 2, 'µm/s', 1, 1)
        self._field('approach_clearance_um', 'Clearance before expected contact', 5, 'µm', 2, 0)
        self._note('Without a usable surface prediction, approach stays slow. Clearance must cover unmeasured relief.', 3)
        self._check('retract_profile_enabled', 'Two-speed retraction', 4)
        self._field('retract_slow_um_s', 'Slow retract rate', 1, 'µm/s', 5, 0)
        self._field('retract_fast_um_s', 'Fast retract rate', 15, 'µm/s', 5, 1)
        holder = Q.QWidget(); layout = Q.QVBoxLayout(holder); layout.setContentsMargins(0,0,0,0)
        layout.addWidget(label('Switch to fast retraction', 'muted'))
        self.mode = Choice(('After a distance', 'After detected detachment'), 'After a distance')
        layout.addWidget(self.mode); self.form.addWidget(holder, 6, 0, 1, 2)
        self.mode.currentIndexChanged.connect(self._refresh)
        self._field('retract_switch_distance_um', 'Slow withdrawal from contact', 5, 'µm', 7, 0)
        self._field('retract_buffer_um', 'Additional withdrawal after detection', 1, 'µm', 7, 1)
        self._note('If detachment is unclear, withdrawal stays slow to the configured endpoint.', 8)
        self.set_values({})

    @property
    def values(self):
        """Return the live draft, including the switching strategy."""
        return self._read() | {'retract_automatic':self.mode.currentIndex()==1}

    def set_values(self, values):
        """Restore settings without restoring predictions or runtime events."""
        candidate = MotionProfileParameters(**values)
        self._loading = True; self.mode.setCurrentIndex(int(candidate.retract_automatic))
        self._restore({k:getattr(candidate,k) for k in PROFILE_FIELDS})

    def _sync_enabled(self):
        approach = self.checks['approach_profile_enabled'].isChecked()
        retract = self.checks['retract_profile_enabled'].isChecked()
        automatic = self.mode.currentIndex()==1
        for key, field in self.fields.items():
            enabled = approach if key.startswith('approach_') else retract
            if key=='retract_switch_distance_um': enabled = retract and not automatic
            if key=='retract_buffer_um': enabled = retract and automatic
            field.setEnabled(enabled)
        self.mode.setEnabled(retract)
        for enabled, name in ((approach, 'approach_rate'), (retract, 'retract_rate')):
            widget = getattr(self.page, name, None)
            if widget is None and isinstance(getattr(self.page, 'fields', None), dict):
                widget = self.page.fields.get('approach_rate_um_s' if name=='approach_rate' else 'retract_rate_um_s')
            if widget is not None: widget.setEnabled(not enabled)

    def _summary(self, values):
        return ' / '.join(s.title() for s in ('approach','retract')
                         if values[s+'_profile_enabled']) or 'Off'
