"""Qt-free, bounded map-frame preparation with explicit cycle/segment semantics."""
from dataclasses import dataclass
import numpy as np
from .analysis_core import AnalysisError, _target_index

LEG_NAMES = ("Start → Vertex 1", "Vertex 1 → Vertex 2", "Vertex 2 → Start")

def cv_targets(dataset):
    """Return the saved waveform, in its recorded polarity convention."""
    p = dataset.metadata.get("parameters") or {}
    try:
        return tuple(float(p[a] if a in p else p[b]) for a,b in
                     (("cv_start_v","start_v"),("cv_vertex1_v","vertex1_v"),("cv_vertex2_v","vertex2_v")))
    except (KeyError, TypeError, ValueError):
        raise AnalysisError("CV waveform metadata is required; use Set CV program first.")

def leg_labels(dataset):
    """Label the chronological segments with the saved endpoint potentials."""
    try:
        s,a,b = cv_targets(dataset)
        return [f"{name} ({low:+g} → {high:+g} V)" for name,low,high in zip(LEG_NAMES,(s,a,b),(a,b,s))]
    except AnalysisError:
        return list(LEG_NAMES)

def cv_leg(dataset, selection, leg):
    """Slice one of three chronological legs; shared vertex samples are retained."""
    s,a,b = cv_targets(dataset)
    rows = selection.rows
    e = rows.matrix[:, rows.columns.index("voltage1_v")]
    if not len(e): raise AnalysisError("Empty CV cycle")
    bounds=[0]; previous=s
    tolerance=max(.005,abs(a-b)*.03)
    for target in (a,b,s):
        direction=int(np.sign(target-previous))
        end=_target_index(e, bounds[-1], len(e)-1, target, direction, 1e-6, tolerance)
        if end is None: raise AnalysisError("Incomplete CV segment")
        bounds.append(end); previous=target
    return rows[bounds[leg]:bounds[leg+1]+1]

def interpolate(x, y, frames):
    """Interpolate adjacent valid points without extrapolation or bridging NaNs."""
    x,y=np.asarray(x),np.asarray(y)
    output=np.full(len(frames),np.nan)
    if len(x)<2 or not np.isfinite(x).all(): return output
    if x[-1]<x[0]: x,y=x[::-1],y[::-1]
    if np.any(np.diff(x)<-1e-8): return output
    # Equal potential/time samples are represented by the last value at that coordinate.
    keep=np.r_[np.diff(x)>0,True]; x,y=x[keep],y[keep]
    if len(x)<2: return output
    index=np.searchsorted(x,frames,side="right")-1
    index=np.clip(index,0,len(x)-2)
    valid=(frames>=x[0])&(frames<=x[-1])&np.isfinite(y[index])&np.isfinite(y[index+1])
    output[valid]=y[index[valid]]+(frames[valid]-x[index[valid]])/(x[index[valid]+1]-x[index[valid]])*(y[index[valid]+1]-y[index[valid]])
    return output

def it_surface_rows(dataset, selection):
    """Infer a complete I–t program from a unique initial→pulse transition.

    Legacy files have no phase timestamps. Require distinct initial/pulse levels,
    validate every nonzero hold and stationary Z, and reject ambiguous matches.
    Time origin is inferred to within the acquisition interval, not an FPGA tag.
    """
    p=dataset.metadata.get("parameters") or {}
    try:
        levels=[float(p[k]) for k in ("initial_potential_v","step_potential_v","return_potential_v")]
        holds=[float(p[k]) for k in ("initial_hold_s","step_hold_s","return_hold_s")]
        cycles=int(p.get("cycles",1))
    except (KeyError,TypeError,ValueError): return None
    if not np.isfinite(levels+holds).all() or not 1<=cycles<=10000: return None
    if min(holds)<0 or holds[0]<=0 or holds[1]<=0 or abs(levels[0]-levels[1])<.005: return None
    rows=selection.rows; m,c=rows.matrix,rows.columns
    t=m[:,c.index("elapsed_s")]; e=m[:,c.index("voltage1_v")]
    if len(t)<3 or not np.isfinite(t).all() or np.any(np.diff(t)<=0): return None
    dt=float(np.median(np.diff(t))); total=sum(holds)*cycles
    edges=np.flatnonzero((np.abs(e[:-1]-levels[0])<.005)&(np.abs(e[1:]-levels[1])<.005))+1
    matches=[]
    for edge in edges:
        origin=t[edge]-holds[0]
        if origin<t[0]-dt or origin+total>t[-1]+dt: continue
        valid=True; cursor=origin
        for level,duration in zip(levels*cycles,holds*cycles):
            if duration>0:
                mask=(t>=cursor+dt)&(t<cursor+duration-dt)
                if not mask.any() or not np.all(np.abs(e[mask]-level)<.005): valid=False; break
            cursor+=duration
        if not valid: continue
        start,end=np.searchsorted(t,[origin,origin+total])
        if "z_um" in c:
            z=m[start:end,c.index("z_um")]
            if not np.isfinite(z).all() or np.ptp(z)>.5: continue
        matches.append((rows[start:end],origin))
    return matches[0] if len(matches)==1 else None

@dataclass
class MapFrames:
    """Frames × visited hops; NaNs mark missing/invalid observations."""
    axis: np.ndarray
    values: np.ndarray
    pixels: list
    coordinates: dict
    recipe: dict
    omitted: int = 0

    def limits(self, mode="Auto", frame=0, manual=None):
        """Return robust movie-wide, per-frame, or validated manual colour limits."""
        if mode=="Manual":
            if manual is None or not np.isfinite(manual).all() or manual[0]>=manual[1]:
                raise AnalysisError("Manual colour limits must be finite with minimum < maximum.")
            return tuple(manual)
        values=self.values[frame] if mode=="Dynamic" else self.values
        values=values[np.isfinite(values)]
        if not len(values): return (0.,1.)
        if mode=="Auto" and len(values)>=10:
            median=np.median(values); mad=np.median(np.abs(values-median))
            if mad>0:
                trimmed=values[np.abs(values-median)<=8*1.4826*mad]
                if len(trimmed): values=trimmed
            elif np.count_nonzero(values==median)>=.8*len(values):
                values=values[values==median]
            low,high=np.percentile(values,[1,99])
        else: low,high=float(values.min()),float(values.max())
        if low==high:
            delta=max(abs(low)*.01,1e-6); low-=delta; high+=delta
        return float(low),float(high)

    def points(self, frame):
        """Return finite map cells for one frame; missing data remain absent."""
        return [dict(scan_pixel=pixel,x_um=self.coordinates[pixel][0],y_um=self.coordinates[pixel][1],
                     value=float(value),samples=1)
                for pixel,value in zip(self.pixels,self.values[frame]) if np.isfinite(value)]

def prepare_frames(dataset, selections, *, channel="current1_na", kind="CV potential",
                   cycle=1, leg=0, axis=None, count=120, stride=1, excluded=(), cancelled=lambda:False):
    """Prepare frames once off-thread; reject unmatched hops and limit memory.

    A cycle number is per hop, not the global position in the cycle list. `None`
    averages all complete cycles. Exclusions are zero-based scan_pixel IDs.
    """
    grid=(dataset.metadata.get("scan_grid") or {}).get("pixels",[])
    coordinates={int(p["scan_pixel"]):(float(p["x_um"]),float(p["y_um"])) for p in grid}
    if not coordinates or not all(np.isfinite(v).all() for v in coordinates.values()):
        raise AnalysisError("Finite physical scan_grid coordinates are required.")
    prepared=[]; excluded=set(excluded)
    excluded.update(int(p["scan_pixel"]) for p in grid if p.get("contact_detected") is False
                    or str(p.get("status","")).lower() in {"failed","aborted","no_contact"})
    for selection in selections:
        if cancelled(): raise AnalysisError("Cancelled")
        if selection.pixel not in coordinates or selection.pixel in excluded: continue
        if kind.startswith("CV") and cycle is not None and selection.cycle!=cycle: continue
        rows=selection.rows; origin=None
        if kind=="CV potential":
            try: rows=cv_leg(dataset,selection,leg)
            except AnalysisError: continue
            x=rows.matrix[:,rows.columns.index("voltage1_v")]
        elif kind=="CV time":
            x=rows.matrix[:,rows.columns.index("elapsed_s")]; x=x-x[0]
        elif kind=="I–t time":
            found=it_surface_rows(dataset,selection)
            if found is None: continue
            rows,origin=found
            x=rows.matrix[:,rows.columns.index("elapsed_s")]-origin
        else: raise AnalysisError("Unknown frame axis")
        y=rows.matrix[:,rows.columns.index(channel)]
        if len(x)>1: prepared.append((selection.pixel,x,y))
    if not prepared: raise AnalysisError("No validated surface data for this cycle/segment. I–t needs a distinct initial→pulse step and a complete stationary program.")
    if axis is None:
        if not 2<=count<=2000 or not 1<=stride<=100: raise AnalysisError("Use 2–2000 frames and a stride of 1–100.")
        low=min(float(np.min(x)) for _,x,_ in prepared); high=max(float(np.max(x)) for _,x,_ in prepared)
        if kind=="CV potential":
            s,a,b=cv_targets(dataset); low,high=((s,a),(a,b),(b,s))[leg]
        axis=np.linspace(low,high,int(count))[::int(stride)]
    axis=np.asarray(axis,dtype=float)
    pixels=sorted({p for p,_,_ in prepared})
    if not np.isfinite(axis).all() or not len(axis) or len(axis)*len(pixels)>20_000_000:
        raise AnalysisError("Invalid or oversized movie; reduce frame count (maximum 20 million hop/frame values).")
    sums=np.zeros((len(axis),len(pixels))); counts=np.zeros_like(sums,dtype=np.int32)
    columns={p:i for i,p in enumerate(pixels)}
    for pixel,x,y in prepared:
        if cancelled(): raise AnalysisError("Cancelled")
        values=interpolate(x,y,axis); valid=np.isfinite(values); column=columns[pixel]
        sums[valid,column]+=values[valid]; counts[valid,column]+=1
    values=np.full_like(sums,np.nan); np.divide(sums,counts,out=values,where=counts>0)
    if not np.isfinite(values).any(): raise AnalysisError("No samples cross the requested frame range.")
    result = MapFrames(axis,values,pixels,coordinates,
        dict(channel=channel,kind=kind,cycle=cycle,leg=leg,excluded_pixels=sorted(excluded),
             source=str(dataset.path),polarity=(dataset.metadata.get("settings") or {}).get("polarity_convention","unspecified"),
             smoothing=dataset.metadata.get("analysis_processing")), len(coordinates)-len(pixels))
    result.auto_limits=result.limits("Auto")
    return result
