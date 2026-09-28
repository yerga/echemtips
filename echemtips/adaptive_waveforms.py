"""Surface-program objectives with explicit cycle/branch/time semantics."""
from dataclasses import asdict, replace
from pathlib import Path
import numpy as np


def score_cv(times, potential, current, params, sensitivity):
    """Select one complete cycle and chronological leg, never combine branches."""
    from .analysis_core import AnalysisDataset, NumericRows, extract_cv_cycles, AnalysisError
    from .analysis_tools import Selection
    from .analysis_frames import cv_leg
    from .adaptive import score_lsv
    if len(times)<4 or len(times)!=len(potential) or len(times)!=len(current):
        return dict(valid=False,reason='Missing CV samples')
    if not np.isfinite(np.column_stack((times,potential,current))).all() or np.any(np.diff(times)<=0):
        return dict(valid=False,reason='Invalid CV samples')
    if np.max(np.abs(np.asarray(current)*sensitivity))>=9.8:
        return dict(valid=False,reason='Current input near ADC clipping')
    columns=('elapsed_s','voltage1_v','current1_na')
    data=AnalysisDataset(Path('adaptive.csv'),columns,NumericRows(columns,np.column_stack((times,potential,current))),
                         {'parameters':asdict(params)})
    cycles=extract_cv_cycles(data)
    if len(cycles)!=params.cycles:
        return dict(valid=False,reason='Incomplete or ambiguous CV cycle sequence')
    selected=cycles[params.objective_cycle-1]
    try:
        rows=cv_leg(data,Selection('Objective',selected.rows,'surface'),params.objective_segment)
    except AnalysisError:
        return dict(valid=False,reason='Incomplete CV segment')
    endpoints=[(params.cv_start_v,params.cv_vertex1_v),(params.cv_vertex1_v,params.cv_vertex2_v),(params.cv_vertex2_v,params.cv_start_v)]
    start,end=endpoints[params.objective_segment]
    result=score_lsv(rows.matrix[:,1],rows.matrix[:,2],replace(params,cv_start_v=start,cv_vertex1_v=end),sensitivity)
    if result['valid']: result['reason']=f'Complete CV; cycle {params.objective_cycle}, segment {params.objective_segment+1}'
    return result


def score_it(times, potential, current, stages, params, sensitivity):
    """Score a signed-current median in one time window of one complete cycle.

    Uses acquired phase tags, so equal consecutive potential levels remain
    distinguishable. Missing phases, short holds and clipping are not trained on.
    """
    if not len(times)==len(potential)==len(current)==len(stages):
        return dict(valid=False,reason='Missing I–t phase tags or samples')
    t,e,i=np.asarray(times),np.asarray(potential),np.asarray(current)
    if len(t)<3 or not all(np.isfinite(v).all() for v in (t,e,i)) or np.any(np.diff(t)<=0):
        return dict(valid=False,reason='Missing or invalid I–t samples')
    if np.max(np.abs(i*sensitivity))>=9.8: return dict(valid=False,reason='Current input near ADC clipping')
    edges=[0]+[j for j in range(1,len(stages)) if stages[j]!=stages[j-1]]+[len(stages)]
    expected=params.it_steps()
    if len(edges)-1!=len(expected): return dict(valid=False,reason='Incomplete I–t phase sequence')
    dt=float(np.median(np.diff(t))); selected=[]
    cycle_time=0.
    for n,((v,duration,label),a,b) in enumerate(zip(expected,edges[:-1],edges[1:])):
        if n%3==0: cycle_time=0.
        observed=(t[b] if b<len(t) else t[b-1]+dt)-t[a]
        if stages[a]!='it:'+label or abs(observed-duration)>max(.02,3*dt,duration*.03) or np.max(np.abs(e[a:b]-v))>.005:
            return dict(valid=False,reason='Incomplete or mismatched I–t hold')
        local=cycle_time+t[a:b]-t[a]
        if n//3+1==params.objective_cycle:
            selected.extend(i[a:b][(local>=params.objective_start_s)&(local<params.objective_end_s)])
        cycle_time+=duration
    if len(selected)<3: return dict(valid=False,reason='Fewer than three samples in objective time window')
    median=float(np.median(selected)); mad=float(np.median(np.abs(np.asarray(selected)-median)))
    return dict(valid=True,reason=f'Complete I–t; cycle {params.objective_cycle}',objective_na=abs(median),
                signed_current_na=median,window_mad_na=mad,
                quality_warning='Noisy objective window' if mad>max(abs(median),.005) else '')
