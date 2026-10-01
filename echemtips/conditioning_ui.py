"""Optional pre/post holds embedded in the measurement setup card."""
from dataclasses import asdict
from .conditioning import ConditioningParameters
from .optional_setup import OptionalSetup


class ConditioningControl(OptionalSetup):
    """Collapsible styled holds; disclosure and enablement are independent."""

    def __init__(self, page):
        super().__init__(page, 'Pre/post measurement holds')
        self._note('After contact and settling; applied once around the complete measurement program.', 0)
        for side, row in (('pre', 1), ('post', 3)):
            self._check(side+'_hold_enabled', side.title()+'-measurement hold', row)
            self._field(side+'_hold_v', 'Potential E1', 0, 'V', row+1, 0)
            self._field(side+'_hold_s', 'Duration', 1, 's', row+1, 1)
        self.set_values({})

    def set_values(self, values):
        """Restore presets while leaving the disclosure state unchanged."""
        candidate = ConditioningParameters(**values)
        errors = candidate.validate_conditioning(self.page.app.settings)
        if errors: raise ValueError('\n'.join(errors))
        self._restore(asdict(candidate))

    def _sync_enabled(self):
        for side in ('pre', 'post'):
            enabled = self.checks[side+'_hold_enabled'].isChecked()
            for suffix in ('v', 's'): self.fields[side+'_hold_'+suffix].setEnabled(enabled)

    def _summary(self, values):
        return ' / '.join(f'{side.title()} {values[side+"_hold_s"]:g} s'
                         for side in ('pre', 'post') if values[side+'_hold_enabled']) or 'Off'
