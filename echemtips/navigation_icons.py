"""Small, consistent vector navigation marks; labels remain authoritative."""
from functools import lru_cache
from PySide6 import QtCore, QtGui


# Explicit assignments prevent electrochemical "scan rates" being mistaken
# for spatial scanning. New catalog entries should select a family here.
ICON_FAMILIES = {
    'Settings': 'settings',
    'Move piezo': 'move',
    'All experiments': 'library',
    'Watch current': 'current',
    'Watch position': 'position',
    'Preflight': 'check',
    'Characterize pipette': 'pipette',
    'CV': 'cv',
    'Approach': 'approach',
    'Approach + CV': 'cv',
    'Approach + CV scan-rate series': 'cv_series',
    'Approach + I-t': 'current',
    'Scan hopping + CV': 'scan',
    'Scan hopping + I-t': 'scan',
    'Adaptive hopping + CV / LSV': 'adaptive',
    'Adaptive hopping + I-t': 'adaptive',
    'Combinatorial scan + CV / LSV': 'combinatorial',
    'Combinatorial scan + I-t': 'combinatorial',
}


@lru_cache(maxsize=96)
def navigation_icon(name, sidebar=False):
    """Render resolution-independent line symbols in sidebar or content colors."""
    icon = QtGui.QIcon()
    for size in (24, 48, 96):
        pixmap = QtGui.QPixmap(size, size); pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(pixmap); painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.scale(size / 24, size / 24)
        painter.setPen(QtGui.QPen(QtGui.QColor('#bad2e3' if sidebar else '#176c78'), 1.7,
                                 QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenCapStyle.RoundCap,
                                 QtCore.Qt.PenJoinStyle.RoundJoin))
        def line(points):
            """Draw one connected stroke in the shared 24-unit coordinate system."""
            painter.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(*p) for p in points]))
        family = ICON_FAMILIES.get(name.replace('I–t', 'I-t'), 'check')
        if family == 'settings':
            for x,y in ((5,8),(12,16),(19,10)):
                line([(x,3),(x,y-2)]); line([(x,y+2),(x,21)])
                painter.drawEllipse(QtCore.QPointF(x,y),2,2)
        elif family == 'move':
            line([(3,12),(21,12)]); line([(12,3),(12,21)])
            for points in ([(6,9),(3,12),(6,15)],[(18,9),(21,12),(18,15)],[(9,6),(12,3),(15,6)],[(9,18),(12,21),(15,18)]): line(points)
        elif family in {'scan', 'adaptive', 'combinatorial', 'library'}:
            for x,y in ((5,5),(12,5),(19,5),(5,12),(12,12),(19,12),(5,19),(12,19),(19,19)):
                painter.drawEllipse(QtCore.QPointF(x,y),1.4,1.4)
            if family == 'adaptive': line([(5,5),(12,12),(19,5),(19,19)])
            elif family == 'combinatorial': line([(3,22),(12,22),(21,22)])
            else: line([(5,5),(19,5),(19,12),(5,12),(5,19),(19,19)])
        elif family in {'cv', 'cv_series'}:
            line([(3,18),(6,7),(12,4),(19,10),(21,17),(15,20),(8,15),(3,18)])
            if family == 'cv_series':
                line([(5,17),(8,10),(12,8),(17,12),(19,16),(15,17),(10,14),(5,17)])
        elif family == 'current':
            line([(3,18),(7,18),(8,4),(10,11),(13,15),(17,17),(21,18)])
        elif family == 'position':
            line([(3,20),(3,4)]); line([(3,20),(21,20)]); line([(5,16),(10,16),(10,11),(15,11),(15,6),(21,6)])
        elif family in {'approach', 'pipette'}:
            line([(8,3),(16,3),(12,15),(8,3)]); line([(12,16),(12,19)]); line([(4,22),(20,22)])
            if family == 'pipette':
                line([(18,5),(21,5),(21,17),(18,17)])
        else:
            painter.drawEllipse(QtCore.QPointF(12,12),9,9); line([(7,12),(10,16),(17,8)])
        painter.end(); icon.addPixmap(pixmap)
    return icon
