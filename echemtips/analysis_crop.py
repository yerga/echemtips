"""Physical-coordinate map cropping without renumbering or altering recordings."""
from dataclasses import replace
import numpy as np
from PySide6 import QtWidgets as Q


def inside(x, y, bounds):
    """Select cell centres inclusively; bounds are xmin, xmax, ymin, ymax in µm."""
    if bounds is None: return True
    if len(bounds)!=4 or not np.isfinite(bounds).all() or bounds[0]>bounds[1] or bounds[2]>bounds[3]:
        raise ValueError('Crop limits must be finite with minimum ≤ maximum.')
    return bounds[0]<=x<=bounds[1] and bounds[2]<=y<=bounds[3]


def grid_spacing(coordinates):
    """Retain original row/column spacing even after a crop leaves a single cell."""
    points=list(coordinates)
    def step(axis):
        """Use smallest positive grid spacing, with 1 µm for genuinely singleton axes."""
        values=sorted({float(p[axis]) for p in points})
        return float(min(np.diff(values))) if len(values)>1 else 1.0
    return step(0),step(1)

def crop_frames(frames, bounds):
    """Crop every frame before estimating colour limits; preserve physical hop IDs."""
    if bounds is None:return frames
    indices=[i for i,pixel in enumerate(frames.pixels) if inside(*frames.coordinates[pixel],bounds)]
    if not indices:raise ValueError('No measured map locations lie inside these crop limits.')
    coordinates={pixel:xy for pixel,xy in frames.coordinates.items() if inside(*xy,bounds)}
    return replace(frames,values=frames.values[:,indices],pixels=[frames.pixels[i] for i in indices],
                   coordinates=coordinates,recipe={**frames.recipe,'crop_xy_um':list(bounds),
                   'cell_spacing_um':grid_spacing(frames.coordinates.values())})


def choose_crop(parent, dataset, bounds):
    """Return (accepted, bounds); a reset restores the complete recorded grid."""
    pixels=(dataset.metadata.get('scan_grid') or {}).get('pixels',[]) if dataset else []
    if not pixels:
        Q.QMessageBox.information(parent,'Crop XY','Open a scan recording with physical grid coordinates first.')
        return False,bounds
    xs=[p['x_um'] for p in pixels]; ys=[p['y_um'] for p in pixels]
    full=(min(xs),max(xs),min(ys),max(ys))
    dialog=Q.QDialog(parent); dialog.setWindowTitle('Crop maps in physical coordinates')
    layout=Q.QFormLayout(dialog)
    note=Q.QLabel('Include cells whose centres lie inside these bounds (µm).\nOriginal recordings and hop numbers are unchanged.')
    note.setWordWrap(True);layout.addRow(note)
    inputs=[]
    for title,value in zip(('X minimum','X maximum','Y minimum','Y maximum'),bounds or full):
        spin=Q.QDoubleSpinBox();spin.setRange(-1e9,1e9);spin.setDecimals(8);spin.setValue(value);spin.setSuffix(' µm')
        layout.addRow(title,spin);inputs.append(spin)
    buttons=Q.QDialogButtonBox(Q.QDialogButtonBox.StandardButton.Ok|Q.QDialogButtonBox.StandardButton.Cancel|Q.QDialogButtonBox.StandardButton.Reset)
    result=[bounds]
    def accept():
        """Reject inverted ranges and empty selections before closing the editor."""
        selected=tuple(w.value() for w in inputs)
        try:
            if not any(inside(p['x_um'],p['y_um'],selected) for p in pixels):raise ValueError('The crop contains no grid locations.')
        except ValueError as exc:Q.QMessageBox.warning(dialog,'Invalid crop',str(exc));return
        result[0]=selected;dialog.accept()
    def reset():
        """Clear the crop, not the measured data."""
        result[0]=None;dialog.accept()
    buttons.accepted.connect(accept);buttons.rejected.connect(dialog.reject)
    buttons.button(Q.QDialogButtonBox.StandardButton.Reset).clicked.connect(reset)
    layout.addRow(buttons)
    return dialog.exec()==Q.QDialog.DialogCode.Accepted,result[0]
