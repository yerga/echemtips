"""Shared, nonmodal About dialog and help links for both desktop applications."""
import platform
import sys

from PySide6 import QtCore, QtGui, QtWidgets

from . import __version__
from .branding import logo_label


DOCUMENTATION_URL = "https://yerga.github.io/echemtips/"
SUPPORT_URL = "https://github.com/yerga/echemtips/issues"
REPOSITORY_URL = "https://github.com/yerga/echemtips"
WEC_URL = "https://warwick.ac.uk/fac/sci/chemistry/research/unwin/electrochemistry/wec-spm/"
PAPER_URL = "https://doi.org/10.1021/acselectrochem.5c00354"


def support_details() -> str:
    """Return concise version diagnostics without paths or instrument identifiers."""
    return (f"eChemTips {__version__}\nPython {sys.version.split()[0]}\n"
            f"Qt {QtCore.qVersion()}\nSystem: {platform.system()} {platform.release()} "
            f"({platform.machine()})")


class AboutDialog(QtWidgets.QDialog):
    """Show project identity, attribution and user-initiated help links."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About eChemTips")
        self.setModal(False)
        self.resize(560, 480)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)
        heading = QtWidgets.QHBoxLayout()
        heading.addWidget(logo_label(64))
        title = QtWidgets.QLabel(f"<h2>eChemTips</h2>Version {__version__}")
        heading.addWidget(title, 1)
        layout.addLayout(heading)
        self.text = QtWidgets.QTextBrowser()
        self.text.setOpenExternalLinks(True)
        self.text.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.text.setHtml(
            "<p>Instrument control and data analysis for scanning electrochemical "
            "cell microscopy (SECCM).</p>"
            "<h3>Acknowledgements</h3>"
            "<p>Built on the workflows and FPGA host protocol of Warwick Electrochemical "
            "Scanning Probe Microscopy (WEC-SPM). We thank its developers at the "
            "University of Warwick and their collaborators for this foundation. "
            "eChemTips is an independent project, not an official Warwick release.</p>"
            f'<p><a href="{WEC_URL}">WEC-SPM and original software requests</a><br>'
            f'<a href="{PAPER_URL}">A Look inside a Flexible Open-Source Scanning '
            'Electrochemical Probe Microscope</a></p>'
            "<p>Made possible by the Python, NumPy, SciPy, PySide6, PyQtGraph and "
            "NI FPGA Python communities, and everyone testing and contributing to eChemTips.</p>"
            "<h3>Documentation and support</h3>"
            f'<p><a href="{DOCUMENTATION_URL}">User and developer documentation</a><br>'
            f'<a href="{SUPPORT_URL}">Report a bug or request a feature</a><br>'
            f'<a href="{REPOSITORY_URL}">Source code and contributions</a></p>'
            f'<p>eChemTips is released under the <a href="{REPOSITORY_URL}/blob/main/LICENSE">'
            'MIT license</a>. Dependencies and the original WEC-SPM software have their own licenses.</p>'
        )
        layout.addWidget(self.text, 1)
        actions = QtWidgets.QHBoxLayout()
        self.copy_button = QtWidgets.QPushButton("Copy version details")
        self.copy_button.setToolTip("Copy app, Python, Qt and operating-system versions for a support report.")
        self.copy_button.clicked.connect(self.copy_details)
        actions.addWidget(self.copy_button)
        actions.addStretch(1)
        close = QtWidgets.QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.close)
        actions.addWidget(close)
        layout.addLayout(actions)

    def copy_details(self):
        """Copy a minimal support summary only when explicitly requested."""
        QtWidgets.QApplication.clipboard().setText(support_details())
        self.copy_button.setText("Copied")


def install_help_menu(window):
    """Install consistent Help actions without interrupting acquisition or analysis."""
    menu = window.menuBar().addMenu("Help")
    for title, url in (("Documentation", DOCUMENTATION_URL), ("Report an issue…", SUPPORT_URL)):
        action = menu.addAction(title)
        action.triggered.connect(lambda _checked=False, target=url: QtGui.QDesktopServices.openUrl(QtCore.QUrl(target)))
    menu.addSeparator()
    about = menu.addAction("About eChemTips…")
    about.setMenuRole(QtGui.QAction.MenuRole.AboutRole)

    def show_about():
        """Reuse a single nonmodal dialog per application window."""
        dialog = getattr(window, "_about_dialog", None)
        if dialog is None:
            dialog = AboutDialog(window)
            window._about_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    about.triggered.connect(show_about)
    return menu
