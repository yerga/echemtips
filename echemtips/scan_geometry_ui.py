"""Alternative scan geometry entry with explicit rounded grid consequences."""
import math
from PySide6 import QtWidgets as Q


def grid_geometry(center_x, center_y, width, height, spacing):
    """Return endpoint/count fields; preserve area and disclose actual spacing."""
    if not all(math.isfinite(v) for v in (center_x, center_y, width, height, spacing)) or min(width, height) < 0 or spacing <= 0:
        raise ValueError('Widths must be nonnegative and spacing must be positive; all values must be finite.')
    nx = max(2, round(width / spacing) + 1) if width else 1
    ny = max(2, round(height / spacing) + 1) if height else 1
    if max(nx, ny) > 64: raise ValueError('This grid exceeds 64 points per axis; increase spacing or reduce the area.')
    return dict(x_start=center_x-width/2, x_end=center_x+width/2, x_points=nx,
                y_start=center_y-height/2, y_end=center_y+height/2, y_points=ny)


def edit_geometry(page):
    """Apply a reviewed center/width grid without moving the instrument."""
    if page.app.any_experiment_active: return
    try:
        x0,x1 = page.x_start.float(), page.x_end.float()
        y0,y1 = page.y_start.float(), page.y_end.float()
        dialog = Q.QDialog(page); dialog.setWindowTitle('Scan center, size and spacing')
        layout = Q.QFormLayout(dialog); controls = []
        for name,value in [('Center X / µm',(x0+x1)/2),('Center Y / µm',(y0+y1)/2),('Width X / µm',abs(x1-x0)),('Width Y / µm',abs(y1-y0)),('Desired hop spacing / µm',5)]:
            field = Q.QDoubleSpinBox(); field.setDecimals(4); field.setRange(-1e6,1e6); field.setValue(value); field.setKeyboardTracking(False)
            controls.append(field); layout.addRow(name,field)
        summary = Q.QLabel(); summary.setWordWrap(True); layout.addRow(summary)
        def preview():
            """Show exact resulting spacing before the geometry is accepted."""
            try:
                values = grid_geometry(*(control.value() for control in controls))
                dx = (values['x_end']-values['x_start']) / max(1, values['x_points']-1)
                dy = (values['y_end']-values['y_start']) / max(1, values['y_points']-1)
                summary.setText(f"{values['x_points']} × {values['y_points']} points · actual spacing X {dx:g}, Y {dy:g} µm. Endpoints are preserved; point count is rounded. Direction is increasing X/Y. Reconfirm combinatorial assignments after grid changes.")
            except ValueError as exc: summary.setText(str(exc))
        for control in controls: control.valueChanged.connect(preview)
        preview()
        actions = Q.QDialogButtonBox(Q.QDialogButtonBox.StandardButton.Ok | Q.QDialogButtonBox.StandardButton.Cancel)
        actions.accepted.connect(dialog.accept); actions.rejected.connect(dialog.reject); layout.addRow(actions)
        if dialog.exec() != Q.QDialog.DialogCode.Accepted: return
        values = grid_geometry(*(control.value() for control in controls))
        if min(values['x_start'],values['y_start']) < 0 or values['x_end'] > page.app.settings.x_range_um or values['y_end'] > page.app.settings.y_range_um:
            raise ValueError('The proposed region is outside the configured piezo ranges.')
        for key,value in values.items(): getattr(page,key).entry.setText(str(value))
    except ValueError as exc: page.app.show_error(str(exc))
