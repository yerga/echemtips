"""Compare planner strategies on synthetic landscapes, not hardware claims.

Run: python scripts/benchmark_adaptive.py --landings 25 --seed 7
All strategies start with the same survey and use the same minimum separation.
"""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from echemtips.adaptive import AdaptiveParameters, propose


def landscape(xy):
    """Return a known smooth background plus localized electrochemical hotspots."""
    xy=np.asarray(xy)/100
    return .01+.1*np.exp(-np.sum((xy-[.67,.62])**2,axis=-1)/.006)+.04*np.exp(-np.sum((xy-[.32,.4])**2,axis=-1)/.025)


def benchmark(landings=25, seed=7):
    """Report best true objective and posterior RMSE for equal landing budgets."""
    p=AdaptiveParameters(region_confirmed=True,max_landings=landings)
    output={}
    candidates=p.candidates()
    for strategy in ('Balanced','Hotspots','Mapping','Random','Space-filling'):
        rng=np.random.default_rng(seed)
        attempts=[]
        for xy in p.survey_points():
            attempts.append({'xy':xy.tolist(),'valid':True,'objective_na':float(landscape(xy)+rng.normal(0,.001))})
        while len(attempts)<landings:
            if strategy in ('Random','Space-filling'):
                distance=np.min(np.linalg.norm(candidates[:,None]-np.array([a['xy'] for a in attempts])[None,:],axis=2),axis=1)
                legal=np.flatnonzero(distance>=p.minimum_spacing_um)
                if not len(legal): break
                selected=rng.choice(legal) if strategy=='Random' else legal[np.argmax(distance[legal])]
                xy=candidates[selected]
            else:
                result=propose(replace(p,strategy=strategy),attempts)
                if result['xy'] is None: break
                xy=np.array(result['xy'])
            attempts.append({'xy':xy.tolist(),'valid':True,'objective_na':float(landscape(xy)+rng.normal(0,.001))})
        posterior=propose(p,attempts)
        output[strategy]={'landings':len(attempts),
                          'best_true_objective_na':float(max(landscape(a['xy']) for a in attempts)),
                          'model_rmse_na':float(np.sqrt(np.mean((np.array(posterior['mean_na'])-landscape(candidates))**2))) if 'mean_na' in posterior else None}
    return {'seed':seed,'note':'Synthetic benchmark only; optimizer superiority is not guaranteed.', 'results':output}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--landings',type=int,default=25)
    parser.add_argument('--seed',type=int,default=7)
    args=parser.parse_args()
    if not 5<=args.landings<=100: parser.error('Choose 5–100 landings for this benchmark.')
    print(json.dumps(benchmark(args.landings,args.seed),indent=2))
