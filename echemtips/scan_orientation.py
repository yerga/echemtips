"""Portable, physical-coordinate scan orientation diagrams (no Qt dependency)."""

import math


def orientation_svg(parameters) -> str:
    """Render the planned array, acquisition order and optional marker as SVG.

    Coordinates are commanded XY in micrometres, not microscopy coordinates.
    This is a plan, not evidence that a landing or footprint was successful.
    """
    points = parameters.grid()
    coords = [(x, y) for _r, _c, x, y in points]
    if parameters.marker_enabled:
        coords.append(parameters.marker_position())
    if not coords or not all(math.isfinite(v) for xy in coords for v in xy):
        raise ValueError('Scan coordinates must be finite.')
    xs, ys = zip(*coords)
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    span = max(xmax - xmin, ymax - ymin, 1.0)
    scale = 245 / span
    cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2

    def xy(x, y):
        """Project physical coordinates with positive Y pointing upwards."""
        return 205 + (x - cx) * scale, 170 - (y - cy) * scale

    svg = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 410 360">',
           '<rect width="410" height="360" fill="white"/>',
           '<g font-family="sans-serif" fill="#203747" font-size="12">',
           '<text x="12" y="18">Planned scan · commanded XY (µm)</text>']
    route = ' '.join(f'{a:.3f},{b:.3f}' for x, y in coords[:len(points)] for a, b in [xy(x, y)])
    svg.append(f'<polyline points="{route}" fill="none" stroke="#75b9b4" stroke-width="1.5"/>')
    radius = min(5.0, max(1.2, 100 / max(parameters.x_points, parameters.y_points)))
    for index, (_r, _c, x, y) in enumerate(points):
        a, b = xy(x, y)
        svg.append(f'<circle cx="{a:.3f}" cy="{b:.3f}" r="{radius}" fill="#008b83"/>')
        if len(points) <= 100 or index in (0, len(points) - 1):
            svg.append(f'<text x="{a + 6:.3f}" y="{b - 6:.3f}">{index + 1}</text>')
    if parameters.marker_enabled:
        a, b = xy(*parameters.marker_position())
        u, v = xy(*coords[len(points) - 1])
        svg.append(f'<path d="M {u:.3f} {v:.3f} L {a:.3f} {b:.3f}" fill="none" stroke="#b96a1b" stroke-dasharray="4 4"/>')
        svg.append(f'<circle cx="{a:.3f}" cy="{b:.3f}" r="6" fill="#b96a1b"/>')
        svg.append(f'<text x="{a + 9:.3f}" y="{b - 9:.3f}">M</text>')
    svg.extend([
        '<path d="M 25 290 L 65 290 L 60 286 M 65 290 L 60 294 M 25 290 L 25 250 L 21 255 M 25 250 L 29 255" fill="none" stroke="#203747"/>',
        '<text x="70" y="294">+X</text><text x="15" y="243">+Y</text>',
        f'<text x="12" y="316">X {xmin:g}–{xmax:g} · Y {ymin:g}–{ymax:g} µm</text>',
        '<text x="12" y="334">Numbers: acquisition order · M: final marker</text>',
        '<text x="12" y="352">Planned positions; see JSON for marker completion.</text>',
        '</g></svg>',
    ])
    return '\n'.join(svg)
