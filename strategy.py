"""Causal daily-close features and long/cash-only PAXG target allocations."""
from itertools import product

import numpy as np
import pandas as pd


def features(df):
    f = df.copy()
    c = f.close
    for n in [20, 50, 100, 150, 200]:
        f[f'sma{n}'] = c.rolling(n, min_periods=n).mean()
    for n in [10, 20, 50, 100, 200]:
        f[f'ema{n}'] = c.ewm(span=n, adjust=False, min_periods=n).mean()
    for n in [20, 55, 100, 200]:
        f[f'break{n}'] = f.high.shift(1).rolling(n, min_periods=n).max()
    for n in [20, 60, 120]:
        f[f'mom{n}'] = c.pct_change(n)
    r = c.pct_change()
    f['vol20'] = r.rolling(20, min_periods=20).std() * np.sqrt(365)
    tr = pd.concat([f.high-f.low, (f.high-c.shift()).abs(), (f.low-c.shift()).abs()], axis=1).max(axis=1)
    f['atr14'] = tr.rolling(14, min_periods=14).mean()
    d = c.diff()
    for n in [2, 14]:
        up = d.clip(lower=0).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
        dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
        f[f'rsi{n}'] = (100 - 100/(1+up/dn.replace(0, np.nan))).fillna(50)
    macd = f.ema10 - f.ema50
    f['macd'] = macd
    f['macd_hist'] = macd - macd.ewm(span=9, adjust=False, min_periods=9).mean()
    # 6 dimensionless trend components inspired by reference BTC repository; no future prices
    z = pd.concat([(c-f.ema20)/f.atr14, (f.ema20-f.ema50)/f.atr14,
                   (c-f.sma200)/(3*f.atr14), f.mom20/(f.vol20*np.sqrt(20/365)),
                   f.mom60/(f.vol20*np.sqrt(60/365)), f.macd_hist/f.atr14], axis=1)
    f['strength'] = np.tanh(z / 2).mean(axis=1)
    return f


def candidates():
    yield dict(family='buy_hold', tag='buy_hold')
    for n in [50, 100, 150, 200]:
        yield dict(family='price_sma', n=n, tag=f'price_sma_{n}')
    for a,b in [(10,50),(20,50),(20,100),(50,100),(50,200)]:
        yield dict(family='ema_cross', a=a, b=b, tag=f'ema_{a}_{b}')
    for n in [20,55,100,200]:
        yield dict(family='breakout', n=n, tag=f'breakout_{n}')
    for n in [20,60,120]:
        for ma in [0,200]:
            yield dict(family='momentum', n=n, ma=ma, tag=f'mom_{n}_ma{ma}')
    for buy,sell in [(10,60),(20,70),(30,70)]:
        for trend in [0,200]:
            yield dict(family='rsi14', buy=buy, sell=sell, trend=trend, tag=f'rsi14_{buy}_{sell}_ma{trend}')
    for buy,sell in [(5,60),(10,70),(15,80)]:
        yield dict(family='rsi2', buy=buy, sell=sell, tag=f'rsi2_{buy}_{sell}')
    for k in [0.6,1.0,1.5]:
        for tv in [0.10,0.20,0.30]:
            yield dict(family='continuous', k=k, tv=tv, tag=f'cont_{k}_{tv}')
    for n in [2,3,4]:
        for tv in [0.0,0.15,0.25]:
            yield dict(family='vote', n=n, tv=tv, tag=f'vote_{n}_{tv}')
    for n in [20,55,100]:
        for tv in [0.10,0.20]:
            yield dict(family='break_vol', n=n, tv=tv, tag=f'breakvol_{n}_{tv}')
    for ma in [100,200]:
        for tv in [0.10,0.20]:
            yield dict(family='vol_trend', ma=ma, tv=tv, tag=f'voltrend_{ma}_{tv}')


def signal(f, p):
    """Return desired fraction for each close (order executed at NEXT daily open)."""
    fam = p['family']; c = f.close
    if fam == 'buy_hold':
        s = pd.Series(1., index=f.index)
    elif fam == 'price_sma':
        s = (c > f[f'sma{p["n"]}']).astype(float)
    elif fam == 'ema_cross':
        s = (f[f'ema{p["a"]}'] > f[f'ema{p["b"]}']).astype(float)
    elif fam in ['breakout', 'break_vol']:
        # Enter on close above PREVIOUS n-day high, hold until close below SMA50 (stateful)
        entry = (c > f[f'break{p["n"]}']).fillna(False).to_numpy()
        exit_ = (c < f.sma50).fillna(True).to_numpy()
        state = False; vals = []
        for en, ex in zip(entry, exit_):
            if ex: state = False
            elif en: state = True
            vals.append(float(state))
        s = pd.Series(vals, index=f.index)
    elif fam == 'momentum':
        s = ((f[f'mom{p["n"]}'] > 0) & ((p['ma']==0) | (c > f.sma200))).astype(float)
    elif fam in ['rsi2', 'rsi14']:
        r = f[f'rsi{2 if fam == "rsi2" else 14}']
        buy = p['buy']; sell = p['sell']
        is_trend = (c > f.sma200) if p.get('trend') == 200 or fam == 'rsi2' else pd.Series(True, index=f.index)
        state = False; vals=[]
        for rv, trend in zip(r, is_trend):
            if not trend or rv >= sell: state = False
            elif rv <= buy: state = True
            vals.append(float(state))
        s = pd.Series(vals, index=f.index)
    elif fam == 'continuous':
        s = (f.strength * p['k']).clip(0,1)
    elif fam == 'vote':
        votes = (c>f.sma200).astype(int)+(f.ema20>f.ema50).astype(int)+(f.mom60>0).astype(int)+(f.macd_hist>0).astype(int)
        s = (votes >= p['n']).astype(float)
    elif fam == 'vol_trend':
        s = (c > f[f'sma{p["ma"]}']).astype(float)
    else:
        raise ValueError(fam)
    tv = p.get('tv',0.0)
    if tv:
        s = s * (tv/f.vol20.replace(0,np.nan)).clip(upper=1).fillna(0)
    # don't trade before a complete 200-day warm-up; 5% steps cut dust trading
    s = ((s.clip(0,1)/0.05).round()*0.05).clip(0,1)
    return s.where(f.sma200.notna(), 0).fillna(0)
