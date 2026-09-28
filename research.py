"""Reproducible train-only selection, untouched 2024+ holdout, anchored yearly walk-forward."""
import json
from pathlib import Path

import pandas as pd

from data import load
from engine import run, COST
from strategy import features, candidates, signal

TRAIN_START = '2021-09-28'
TRAIN_END = '2023-12-31'
TEST_START = '2024-01-01'
OUT = Path('results')


def score(m):
    # predefined risk-adjusted objective, calibrated exclusively on training period
    if m['mdd'] > .22 or m['exposure'] < .12 or m['locked']:
        return -1000
    return m['cagr'] - .55*m['mdd'] + .007*m['sharpe'] - .0003*m['turnover']


def main():
    OUT.mkdir(exist_ok=True)
    raw = load(); f = features(raw)
    end = str(raw.date.iloc[-1].date())
    rows = []; cache = {}
    for p in candidates():
        s = signal(f,p); cache[p['tag']] = (p,s)
        m = run(f,s,TRAIN_START,TRAIN_END)
        rows.append(dict(tag=p['tag'],family=p['family'],params=json.dumps(p,ensure_ascii=False),
                         **{f'train_{k}':v for k,v in m.items()}, score=score(m)))
    table = pd.DataFrame(rows).sort_values('score',ascending=False)
    if table.iloc[0]['score'] == -1000:
        raise RuntimeError('no strategy met the predeclared training risk/exposure criteria')
    winner = str(table.iloc[0]['tag']); p,s = cache[winner]
    champions = table.drop_duplicates('family').sort_values('score',ascending=False)
    oos=[]
    for _, row in champions.iterrows():
        config, s0 = cache[row['tag']]
        tm=run(f,s0,TEST_START,end)
        oos.append(dict(tag=row['tag'],family=row['family'],train_score=row['score'],
                        train_cagr=row['train_cagr'],train_mdd=row['train_mdd'],
                        **{f'test_{k}':v for k,v in tm.items()}))
    oos = pd.DataFrame(oos)
    # raw B&H and identical circuit breaker baseline (no fee advantage given to selected strategy)
    bh = cache['buy_hold'][1]
    baselines={k:run(f,bh,TEST_START,end,risk=r) for k,r in [('buy_hold_raw',False),('buy_hold_guarded',True)]}
    selected_test, curve = run(f,s,TEST_START,end,detail=True)
    selected_full = run(f,s,TRAIN_START,end)
    stress = {str(x):run(f,s,TEST_START,end,cost=x) for x in [0., COST, .003, .005]}
    # Anchored annual selection: tune only on data available at Dec 31 before each target year.
    wf=[]
    for year in [2024,2025,2026]:
        cutoff=f'{year-1}-12-31'; stop=min(f'{year}-12-31',end)
        ranked=[]
        for tag,(conf, ss) in cache.items():
            m=run(f,ss,TRAIN_START,cutoff)
            ranked.append((score(m),tag))
        best_score, tag=max(ranked)
        if best_score == -1000: continue
        wf.append(dict(year=year,training_through=cutoff,tag=tag,
                       **{f'test_{k}':v for k,v in run(f,cache[tag][1],f'{year}-01-01',stop).items()}))
    table.to_csv(OUT/'train_candidates.csv', index=False)
    oos.to_csv(OUT/'family_holdout.csv',index=False)
    pd.DataFrame(wf).to_csv(OUT/'annual_walk_forward.csv',index=False)
    curve.to_csv(OUT/'chosen_equity_curve.csv',index=False)
    doc=dict(data=dict(symbol='PAXGUSDT',first=str(raw.date.iloc[0].date()),last=end,bars=len(raw),
                       train_start=TRAIN_START,train_end=TRAIN_END,test_start=TEST_START,
                       candidate_count=len(table), cost_per_side=COST),
             chosen=p,chosen_train=run(f,s,TRAIN_START,TRAIN_END),chosen_test=selected_test,
             chosen_full=selected_full,baselines=baselines,stress=stress,walk_forward=wf)
    (OUT/'summary.json').write_text(json.dumps(doc,indent=2,ensure_ascii=False),encoding='utf-8')
    (Path('chosen.json')).write_text(json.dumps(p,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(doc,ensure_ascii=False,indent=2))
    print('\nFAMILY HOLDOUT\n',oos[['family','tag','train_cagr','train_mdd','test_cagr','test_mdd','test_sharpe','test_trades']].to_string(index=False))


if __name__ == '__main__':
    main()
