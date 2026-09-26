# SECCM analytical models (advisory)

Open **Analysis > SECCM model calculator** in instrument control or
**Models > SECCM analytical models** in the analysis app. The dialog is nonmodal:
it does not pause the instrument, write to the FPGA, modify thresholds, or
classify landings. No controls are added to experiment setup pages.

The implementation follows Anderson and Edwards, *Analytical Chemistry* (2023),
[DOI: 10.1021/acs.analchem.3c00216](https://doi.org/10.1021/acs.analchem.3c00216),
equations 1–3, 10–11 and Table 1. It needs no extra packages or bitfile changes.

## First calculation

1. Open **Model parameters**. Values are illustrative examples, not calibration.
2. Enter pipette **inner radius**, half-angle, meniscus height, reactant
   concentration and diffusion coefficient. Concentration in mM is numerically
   equal to mol/m³. Radius fields are radii, not diameters.
3. Choose R3 when meniscus height is known, or compare R1/R2. R3 is
   `inner_radius / sin(half_angle) - height / cos(half_angle)`; a nonpositive
   equivalent radius is rejected. This is the paper's approximate geometry.
4. Choose reduction or oxidation. All predictions use IUPAC current polarity.
5. For a kinetic wave, enter formal potential (same reference as the data),
   temperature, physical footprint radius, k⁰ and α. The kinetic model is
   restricted to one-electron reactions with equal reactant/product diffusivities,
   no bulk product and negligible ohmic drop.
6. Return to **Calculate / compare** and press **Calculate**. Changes invalidate
   the export until calculated again.

Optional radius sensitivity evaluates inner radius ± the entered percentage,
holding other quantities fixed. It is **not a statistical confidence interval**.
Save/load parameter presets explicitly; these do not change instrument defaults.
The physical footprint is independent of the map's cosmetic footprint setting.
Meniscus height is not contact Z or measured piezo displacement.

## Models and limits

- **Steady-state i–E:** kinetic/transport wave, not a time-dependent CV simulator.
  It will not predict scan-rate-dependent peaks, reverse-scan memory or multiple
  cycles. Compare suitable individual branches using recording time bounds.
- **Diffusion-limited step i–t:** an ideal step from uniform initial concentration
  to zero reactant surface concentration. It excludes capacitive charging,
  instrument filtering, previous depletion and arbitrary pulse sequences.
- **Limiting current:** displayed for either model; this is faradaic current,
  not the contact spike used by the approach feedback.

The ideal instantaneous transient is singular at zero. The preview starts at a
positive time (10 µs when the entered lower bound is zero); this is a plotting
choice, not a universal model-validity boundary. Geometry and instrument response
matter at early times. Entering a positive averaging interval computes the exact
mean over `[t - interval, t]`, with t the bin end. This models rectangular averaging
only, not the amplifier filter. The first bin may start at zero.

Diffusion-only assumptions require sufficient supporting electrolyte. The current
version does not include the specialized HER/migration expressions, general
catalytic chemistry, fitting, or a dynamic diffusion solver. Differences from
predictions must not be interpreted as failed contact or automatic rejection of
unusual high-activity locations.

## Compare original data

Open a recording first, then open the model dialog in analysis. Choose an original
recording or extracted cycle and narrow the time bounds to the desired measurement
phase. The source name is shown; close and reopen the dialog after loading another
recording. The dialog uses original samples, irrespective of analysis smoothing.

For a step, set **Step origin** to its onset in recording time. Samples at/before
the onset or with averaging bins crossing it are excluded. A previously reactive
hold or CV may invalidate the uniform initial concentration assumption. Review the
actual phase rather than resetting every pulse as a fresh step.

Confirm recorded polarity if missing from metadata. Instrument-native E and i
are inverted together for IUPAC comparison. Unknown conventions are not guessed.
Select the relevant current channel. Missing channels or empty selections produce
an explanatory message. Full-resolution finite samples determine residuals;
display reduction affects rendering only. RMS residual is not a quality verdict.

**Export result** saves a separate JSON containing model version, DOI, SI
parameters, assumptions, source/selection, polarity, time bounds, sensitivity
curves, predictions and optional original currents/residuals. Original CSV and
metadata paths are protected. No source recording is rewritten.

## Validation and manual acceptance

Automated tests check the supporting model report's R2 benchmark (~7.879 pA),
geometry, scaling, polarity, transient bin averages, long-time/kinetic limits,
invalid inputs, original-data preservation, export protection and presets.

Before interpreting experimental results:

1. Confirm the illustrative R2 calculation gives approximately 7.879 pA magnitude.
2. Double concentration: the limiting-current magnitude should double.
3. Switch to oxidation: current becomes positive and the wave direction reverses.
4. Select step i–t: magnitude should decay toward the limiting current.
5. Save/load a preset; confirm it does not alter instrument settings.
6. Compare a known original CV branch; confirm polarity and time scope.
7. Change a parameter; export must stay disabled until recalculated.
8. Open the calculator during a simulated run; acquisition should continue.

Experimental agreement and geometry uncertainty still require independent checks.
Numerical agreement with a published model is not calibration of a real pipette.
