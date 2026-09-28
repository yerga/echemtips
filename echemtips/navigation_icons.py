"""Small, consistent vector navigation marks; labels remain authoritative."""
from functools import lru_cache
from PySide6 import QtCore, QtGui


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
        lower = name.lower()
        if 'settings' in lower:
            for x,y in ((5,8),(12,16),(19,10)):
                line([(x,3),(x,y-2)]); line([(x,y+2),(x,21)])
                painter.drawEllipse(QtCore.QPointF(x,y),2,2)
        elif 'move' in lower:
            line([(3,12),(21,12)]); line([(12,3),(12,21)])
            for points in ([(6,9),(3,12),(6,15)],[(18,9),(21,12),(18,15)],[(9,6),(12,3),(15,6)],[(9,18),(12,21),(15,18)]): line(points)
        elif 'scan' in lower or 'hopping' in lower or 'experiments' in lower:
            for x,y in ((5,5),(12,5),(19,5),(5,12),(12,12),(19,12),(5,19),(12,19),(19,19)):
                painter.drawEllipse(QtCore.QPointF(x,y),1.4,1.4)
            if 'adaptive' in lower: line([(5,5),(12,12),(19,5),(19,19)])
            elif 'combinatorial' in lower: line([(3,22),(12,22),(21,22)])
            else: line([(5,5),(19,5),(19,12),(5,12),(5,19),(19,19)])
        elif 'cv' in lower:
            line([(3,18),(6,7),(12,4),(19,10),(21,17),(15,20),(8,15),(3,18)])
        elif 'i-t' in lower or 'current' in lower:
            line([(3,18),(7,18),(8,4),(10,11),(13,15),(17,17),(21,18)])
        elif 'position' in lower:
            line([(3,20),(3,4)]); line([(3,20),(21,20)]); line([(5,16),(10,16),(10,11),(15,11),(15,6),(21,6)])
        elif 'approach' in lower:
            line([(8,3),(16,3),(12,15),(8,3)]); line([(12,16),(12,19)]); line([(4,22),(20,22)])
        else:
            painter.drawEllipse(QtCore.QPointF(12,12),9,9); line([(7,12),(10,16),(17,8)])
        painter.end(); icon.addPixmap(pixmap)
    return icon
