# eChemTips documentation

Choose the path that matches what you are doing.

## WEC-SPM origins

eChemTips is a separate Python host/UI based on the workflows and FPGA protocol
of Warwick Electrochemical Scanning Probe Microscopy (WEC-SPM). To request the
original software, use the form on the
[University of Warwick WEC-SPM page](https://warwick.ac.uk/fac/sci/chemistry/research/unwin/electrochemistry/wec-spm/).
For the instrument and software architecture, see McKelvey et al.,
[A Look inside a Flexible Open-Source Scanning Electrochemical Probe Microscope](https://doi.org/10.1021/acselectrochem.5c00354),
*ACS Electrochemistry* **2026**, *2* (1), 78–91 (online December 4, 2025).
Supporting Information section S7 describes software access. The original
WEC-SPM software/FPGA artifacts remain under their own license terms and are
not distributed or relicensed by this project.

## Use the simulator or operate an experiment

1. Start with the project [README](https://github.com/yerga/echemtips#readme).
2. Follow the [Operator guide](OPERATOR_GUIDE.md) for controls, experiment
   sequences, plots, contact decisions, scan paths, and stopping.
3. Use the [Settings reference](SETTINGS_REFERENCE.md) for every persisted
   option and unit.
4. Use the [Glossary](GLOSSARY.md) when a method, signal, or protocol term is
   unfamiliar.
5. Read the [Recording data format](DATA_FORMAT.md) before building an analysis
   workflow.

## Commission or maintain real hardware

1. Read [Real hardware setup](REAL_HARDWARE_SETUP.md) completely before
   connecting downstream devices.
2. Follow [Calibration and sign conventions](CALIBRATION.md).
3. Keep [Hardware troubleshooting and recovery](TROUBLESHOOTING.md) available
   during staged tests.
4. Use [Hardware integration](HARDWARE_INTEGRATION.md) for the exact bitfile,
   FIFO, register, conversion, startup, and driver boundary.
5. Use [Shared host services](SHARED_HOST_SERVICES.md) for LabVIEW-host
   responsibility mapping and current verification scope.

Automated tests and offline bitfile inspection do not constitute physical
commissioning. Record the exact instrument configuration and evidence using
the commissioning template.

## Develop or review the code

1. Read the [public contribution guide](https://github.com/yerga/echemtips/blob/main/CONTRIBUTING.md).
2. Read the [Architecture and extension guide](ARCHITECTURE.md).
3. Consult [Hardware integration](HARDWARE_INTEGRATION.md) before changing
   protocol code and [Recording data format](DATA_FORMAT.md) before changing
   persisted fields.
4. Run all unit and GUI smoke tests before proposing a change.

## Historical migration notes

These documents preserve design rationale but are not active backlogs:

- [September 2026 hardware-operation gaps](history/2026-09-hardware-operation-gaps.md)
- [September 2026 LabVIEW-parity gaps](history/2026-09-labview-parity-gaps.md)

The private LabVIEW archive and compiled target are deliberately absent from
the public repository. Statements derived from that archive are documented as
protocol-audit results; reproducing the original comparison requires authorized
access to the local legacy material.
