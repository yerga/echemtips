"""Shared application identity and packaged eChemTips artwork."""

from functools import lru_cache
from importlib.resources import files

from PySide6 import QtCore, QtGui, QtWidgets

from . import __version__


@lru_cache(maxsize=1)
def application_icon() -> QtGui.QIcon:
    """Load the packaged logo after QApplication exists, independent of cwd."""
    image = QtGui.QPixmap()
    resource = files("echemtips").joinpath("assets", "echemtips-logo.png")
    if not image.loadFromData(resource.read_bytes(), "PNG"):
        raise RuntimeError("The packaged eChemTips logo could not be decoded.")
    icon = QtGui.QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256, 512):
        icon.addPixmap(image.scaled(size, size, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                    QtCore.Qt.TransformationMode.SmoothTransformation))
    return icon


def configure_application_identity(app: QtWidgets.QApplication) -> None:
    """Set shared Qt identity without changing native installation settings."""
    app.setApplicationName("eChemTips")
    app.setApplicationDisplayName("eChemTips")
    app.setOrganizationName("eChemTips")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(application_icon())


def logo_label(size: int = 36) -> QtWidgets.QLabel:
    """Create an accessible logo widget without showing an unparented window."""
    widget = QtWidgets.QLabel()
    widget.setObjectName("applicationLogo")
    widget.setAccessibleName("eChemTips logo: pipette, meniscus and surface")
    widget.setFixedSize(size, size)
    widget.setPixmap(application_icon().pixmap(size, size))
    return widget
