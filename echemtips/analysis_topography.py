"""Reversible contact-height, plane and coordinate transformations for analysis."""
from dataclasses import replace
import numpy as np
from .analysis_core import AnalysisError


def scan_origin(dataset, *, coordinates=None, spacing=None):
    """Place zero at the visible grid's lower-left outer cell edge, not its centre."""
    from .analysis_crop import grid_spacing
    pixels = dataset.metadata.get('scan_grid', {}).get('pixels', [])
    full = [(float(p['x_um']), float(p['y_um'])) for p in pixels if p['scan_pixel'] >= 0]
    selected = list(coordinates) if coordinates is not None else full
    if not selected:
        return (0., 0.)
    dx, dy = spacing or grid_spacing(full)
    return min(p[0] for p in selected)-dx/2, min(p[1] for p in selected)-dy/2


def relative_frames(frames, origin):
    """Translate an immutable movie snapshot without changing values or hop IDs."""
    result = replace(frames, coordinates={p: (x-origin[0], y-origin[1])
                   for p, (x, y) in frames.coordinates.items()},
                   recipe={**frames.recipe, 'xy_origin_um': list(origin), 'xy_coordinates': 'map-corner-relative'})
    if hasattr(frames, 'auto_limits'):
        result.auto_limits = frames.auto_limits
    return result


def contact_points(dataset, cv_groups, hop_groups):
    """Use explicit contact metadata or median Z during validated surface programs.

    Surface estimates are not threshold-crossing measurements. Never substitute
    whole-hop extrema, which can include failed approaches and retraction.
    """
    from .analysis_frames import it_surface_rows
    from .analysis_surface_z import hop_dataset, stationary_sweep_rows, stationary_pulse_rows
    pixels = dataset.metadata.get('scan_grid', {}).get('pixels', [])
    groups, sources = {}, {}
    for group in cv_groups:
        groups.setdefault(group.pixel, []).append(group.rows)
    for group in hop_groups:
        if group.pixel in groups:
            continue
        per_hop = hop_dataset(dataset, group)
        found = it_surface_rows(per_hop, group)
        if found is not None:
            groups[group.pixel] = [found[0]]
        else:
            sweep = stationary_sweep_rows(per_hop, group)
            if sweep is not None:
                groups[group.pixel] = [sweep]
                sources[group.pixel] = 'stationary CV/LSV sweep Z (estimate; full cycle not required)'
            else:
                pulse = stationary_pulse_rows(per_hop, group)
                if pulse is not None:
                    groups[group.pixel] = [pulse]
                    sources[group.pixel] = 'stationary I–t pulse Z (estimate; actual transition)'
    points = []
    for pixel in pixels:
        if pixel['scan_pixel'] < 0 or pixel.get('contact_detected') is False or pixel.get('status') in ('failed', 'aborted', 'no_contact'):
            continue
        value = pixel.get('contact_z_um')
        source = 'recorded contact Z'
        count = 1
        if value is None:
            rows = groups.get(pixel['scan_pixel'], [])
            arrays = [r.matrix[:, r.columns.index('z_um')] for r in rows if 'z_um' in r.columns]
            if not arrays:
                continue
            z = np.concatenate(arrays); z = z[np.isfinite(z)]
            if not len(z):
                continue
            value, count = float(np.median(z)), len(z)
            source = sources.get(pixel['scan_pixel'], 'surface-program median Z (estimate)')
        if np.isfinite([value, pixel['x_um'], pixel['y_um']]).all():
            points.append(dict(scan_pixel=pixel['scan_pixel'], x_um=pixel['x_um'], y_um=pixel['y_um'],
                               value=float(value), samples=count, source=source))
    return points


def transform_points(points, *, origin=(0., 0.), flatten=False, height=False):
    """Subtract a least-squares plane before optional max(Z)-Z height conversion.

    Fit only supplied finite cells (after cropping), requiring three noncollinear
    positions. Plane coefficients use centred physical coordinates for stability.
    """
    if not points:
        raise AnalysisError('No usable surface Z data for this selection.')
    xyz = np.array([[p['x_um'], p['y_um'], p['value']] for p in points], dtype=float)
    if not np.isfinite(xyz).all():
        raise AnalysisError('Topography requires finite coordinates and Z values.')
    values = xyz[:, 2].copy()
    context = dict(xy_origin_um=list(origin), flatten=flatten, topography_height=height)
    if flatten:
        centre = xyz[:, :2].mean(axis=0)
        design = np.column_stack((xyz[:, :2]-centre, np.ones(len(xyz))))
        coefficients, _, rank, _ = np.linalg.lstsq(design, values, rcond=None)
        if rank < 3:
            raise AnalysisError('Tilt removal needs at least three noncollinear usable landings (more than one row and column).')
        values -= design @ coefficients
        context.update(plane_origin_um=centre.tolist(), plane_coefficients=coefficients.tolist(),
                       plane_fit='least squares: Z = a*(X-X0) + b*(Y-Y0) + c')
    if height:
        context['height_zero_z_um'] = float(values.max())
        values = values.max()-values
    result = [dict(p, x_um=float(p['x_um']-origin[0]), y_um=float(p['y_um']-origin[1]), value=float(v))
              for p, v in zip(points, values)]
    return result, context


def set_xy_labels(plot, relative):
    """Keep on-screen and publication labels explicit about the coordinate origin."""
    suffix = ' from map corner' if relative else ' position'
    plot.xy_labels = ('X'+suffix+' (µm)', 'Y'+suffix+' (µm)')
    plot.plot_item.setLabel('bottom', 'X'+suffix, units='µm')
    plot.plot_item.setLabel('left', 'Y'+suffix, units='µm')


def show_3d(plot):
    """Open a rotatable, exportable 3D snapshot without requiring OpenGL."""
    from PySide6 import QtWidgets as Q
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
    from .analysis_export import snapshot, render_figure, FigureExportDialog
    data = snapshot(plot)
    data.context['projection'] = '3d'
    options = dict(font='DejaVu Sans', font_size=10, width=180, height=140, dpi=100,
                   xlabel=data.xlabel, ylabel=data.ylabel, quantity=data.quantity,
                   title='', palette=data.palette, limits=data.limits, legend=False, line_width=1.)
    figure = render_figure(data, options)
    dialog = Q.QDialog(plot); dialog.setWindowTitle('3D surface — drag to rotate'); dialog.resize(850, 700)
    layout = Q.QVBoxLayout(dialog); canvas = FigureCanvasQTAgg(figure)
    layout.addWidget(NavigationToolbar2QT(canvas, dialog)); layout.addWidget(canvas, 1)
    note = Q.QLabel('Points are measured/estimated landings; the surface connects adjacent grid cells. Gaps remain blank. Vertical scale is exaggerated.')
    note.setWordWrap(True); layout.addWidget(note)
    export = Q.QPushButton('Export publication figure…'); layout.addWidget(export)
    def save():
        """Preserve the rotated viewing angle and the full-resolution snapshot."""
        editor = FigureExportDialog(plot)
        editor.data = data
        editor.data.context.update(elevation=figure.axes[0].elev, azimuth=figure.axes[0].azim)
        editor.preview_figure(); editor.exec()
    export.clicked.connect(save)
    canvas.draw(); dialog.exec()
