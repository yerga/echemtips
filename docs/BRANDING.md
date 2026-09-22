# eChemTips branding

The shared mark represents a pipette, meniscus and sample surface. The artwork
uses the application's teal palette with a white pipette and mint contact.

The packaged source asset is `echemtips/assets/echemtips-logo.png`. It is used
in the control/analysis sidebars and their Qt window icons. The window titles
are **eChemTips — Instrument Control** and **eChemTips — Data Analysis**.
Qt's application display name and version are also set consistently.

The original PNG was generated with the built-in image-generation tool; two
transparent drafts were discarded because of background artifacts. The final
opaque version was copied unchanged into the project. Qt scales it at runtime.
No external asset directory or generation service is needed to run the app.

## Final generation prompt

> Create a minimal flat two-color app logo for eChemTips. IMPORTANT: fully
> opaque square canvas filled uniformly edge-to-edge with SOLID teal #0F8B83.
> Do not generate transparency or an alpha cutout. No rounded corners, no
> gradients, no shading, no texture, no mockup. Draw ONLY a bold simple white
> laboratory pipette tip tapering vertically downward above a small pale mint
> meniscus and a white horizontal sample line. The pipette and sample line
> should form a centered compact strong emblem inside generous margins on all
> four sides. Thick white geometric strokes, recognisable at 32 pixels. No
> lettering, no borders, no black areas. Clean flat filled background must
> cover every pixel except the white and mint emblem. Single square
> professional scientific desktop-app icon.

## Optional next steps — not implemented

1. Contextual window titles with the selected method, simulation/hardware mode
   and running/paused state; analysis could show the opened recording name.
2. Packaged Windows/macOS launchers, native icon formats and installation
   shortcuts for reliable taskbar/Dock/Finder identity instead of Python's.
3. Native File/Experiment/View/Help menus and discoverable keyboard shortcuts.
4. An About dialog with version, acknowledgements, documentation and support links.
5. Opt-in desktop notifications for completion/errors; no notification action
   should trigger motion or resume an experiment.
6. Taskbar/Dock progress for scans, with clear paused/error states where supported.
7. File-open integration: drag-and-drop, recent recordings and optional file
   association. Avoid claiming all CSV files; a dedicated format is preferable.
8. A consistent experiment-navigation icon set and clearer empty plot/map states.
9. Exportable publication-style plot/map images with units, legends and metadata.

These require separate selection and validation. A Qt icon is not an installed
application bundle: when launched through Python, operating-system grouping,
Dock names and launcher icons may still reflect Python. This change installs
no shortcuts, file associations, startup services, tray icons or notifications.
