# CV views and map movies

Analysis never changes the instrument or source files. Smoothing, when enabled
in the analysis toolbar, also feeds maps and movies; the MP4 recipe records it.

## CV plots

The CV tab offers **i vs E**, **E vs t**, and **i vs t**. Use the existing tree
to select hops/cycles or overlay them. Time starts at zero for each selected
cycle; it is not global recording time or time since initial approach. Only
waveform-validated complete cycles appear. Display overlays remain capped at
50 traces; exported separated CV data retain every complete cycle.

## Hop-map potential and cycle selection

Choose **CV at potential**, Current 1/2, a potential, a chronological segment,
and a cycle number **within each hop**. The default is cycle 1, not a mixture of
all cycles. The explicit Average complete cycles option averages available
complete cycles per hop; missing cycles are never replaced by zeros.

For Start −0.2 V → Vertex 1 +0.6 V → Vertex 2 −0.4 V → Start −0.2 V:

| Segment | Range | Example |
| --- | --- | --- |
| Start → Vertex 1 | −0.2 to +0.6 V | Includes +0.2 V, not −0.3 V |
| Vertex 1 → Vertex 2 | +0.6 to −0.4 V | Includes +0.2 V and −0.3 V |
| Vertex 2 → Start | −0.4 to −0.2 V | Includes −0.3 V, not +0.2 V |

This resolves the ambiguity of calling both the first and third legs
“increasing”. Labels show the actual recorded voltages, without assuming that
increasing recorded potential is anodic for every instrument convention.
Adjacent samples are interpolated within the chosen leg, never extrapolated.
A missing leg or potential outside its recorded range produces a blank cell.

## Map movie workflow

1. Open a scan recording with physical `scan_grid` metadata.
2. Select **Map movie** and Current 1 or Current 2.
3. Choose the frame axis:
   - **CV potential:** current map as potential progresses along one selected leg.
   - **CV time:** current map against time from the selected CV cycle's start.
   - **I–t time:** current map against time from the surface potential program's start.
4. For CVs, choose the per-hop cycle or explicit cycle average; potential movies
   additionally select a leg. Time movies cover the whole cycle.
5. Set the number of evenly spaced frames. **Every Nth frame** skips positions
   on this grid (1 keeps all frames). Preparation is bounded to 20 million
   hop/frame values; reduce frame count for very large maps.
6. Open **Movie options…** for palette, playback/export timing, colour limits,
   and explicit exclusions. Enter failed hop numbers as shown to the operator,
   e.g. `2, 5`; internally `scan_pixel` is zero-based.
7. Click **Prepare frames**, then Play/Pause, scrub the slider, or enable Loop.
   Changes to data/segment/cycle/exclusions invalidate prepared frames; prepare
   again. Palette, timing and colour-limit edits do not reprocess the recording.
8. **Output FPS** is the encoder frame rate. **Hold per frame** controls how long
   each map stays visible, rounded to a whole number of encoded frames (minimum
   one). For example, 20 FPS and 100 ms hold writes each map twice. The preview
   uses the same effective interval; the status shows it. A busy GUI may play
   more slowly, but the exported MP4 has deterministic timing.
9. Choose **Save MP4…**. Export is background, cancellable, H.264/yuv420p at
   960×720 with physical axes, frame coordinate, cycle and labelled current scale.
   Existing output is replaced only after encoding succeeds. A `.mp4.json`
   sidecar preserves the source, selections, frame axis, smoothing and colour recipe.

If FFmpeg is already installed and on PATH, nothing else is needed. Otherwise,
from the project environment install `python -m pip install ".[movies]"` or
`python -m pip install imageio-ffmpeg` for a portable encoder. Preparation and
playback work without this optional dependency. It is not installed automatically.

## Colour ranges and data quality

- **Auto:** one fixed range for the complete prepared movie after exclusions.
  With enough observations, extreme values beyond eight scaled median absolute
  deviations are omitted from range estimation, followed by 1st/99th percentiles.
  This suppresses isolated spikes; it is not a scientifically validated landing classifier.
- **Dynamic:** the finite minimum and maximum of each individual frame. Colours
  across different frames therefore do not represent the same absolute current.
- **Manual:** fixed user-entered minimum/maximum in nA. For example, ±5 pA is
  −0.005 to +0.005 nA. Limits only change colours, never measured values.

Incomplete CV cycles, explicitly failed/contact-false pixels when metadata
provides those flags, and user-excluded hops are omitted. Missing values remain
blank/grey. Auto limits cannot guarantee removal of all bad landings; inspect
individual hops and exclude questionable ones before interpreting a movie.

### Legacy I–t alignment limitation

Older CSV/JSON files lack explicit surface-phase timestamps. I–t movies infer
program start from a distinct initial→pulse transition and validate the full
saved initial/pulse/return sequence and durations. If Z is present, it must
remain within 0.5 µm over that program. Ambiguous, incomplete, all-equal-potential,
or zero-initial/zero-pulse-hold programs are rejected, not silently treated as
surface data. Timing is approximate to the acquisition interval. This is a
conservative waveform check, not proof of physical droplet contact.
