"""Next-open execution, explicit friction, actual cash/units accounting, prior-close drawdown controls."""
import numpy as np
import pandas as pd

INITIAL = 10000.0
COST = 0.0015  # assumed 0.10% fee + 0.05% slippage EACH side


def guard(equity, peak, hard_floor, halted, cooldown, trend_ok):
    """Uses only preceding candle's marked equity and close-derived trend."""
    if equity <= hard_floor:
        return 0., True, 0, True, peak
    dd = 1 - equity / max(peak, 1e-9)
    if halted and cooldown >= 10 and trend_ok:
        halted, peak, dd = False, equity, 0.  # local risk-cycle peak, NOT performance peak
    if dd >= .25:
        halted, cooldown = True, 0
    if halted:
        return 0., True, cooldown + 1, False, peak
    cap = 0.25 if dd >= .22 else 0.5 if dd >= .18 else 0.75 if dd >= .12 else 1.0
    return cap, False, 0, False, peak


def run(f, desired, start, end, risk=True, cost=COST, detail=False, initial=INITIAL):
    """Isolated account from start, flat initial cash. Signal day t-1 trades day t OPEN.

    The stop is next-open, not intraday; cannot guarantee a strict 30% drawdown bound.
    """
    dates = f.date.to_numpy()
    idxs = np.flatnonzero((dates >= np.datetime64(start)) & (dates <= np.datetime64(end)))
    if not len(idxs) or idxs[0] < 1:
        raise ValueError('not enough data / missing prior signal day')
    cash, qty, peak, performance_peak = initial, 0., initial, initial
    halted = locked = False; cooldown = 0; trades = 0; turnover = 0.; exposure = 0.
    equity_hist = []; records = []
    opens = f.open.to_numpy(); closes = f.close.to_numpy(); sma200 = f.sma200.to_numpy()
    mom20 = f.mom20.to_numpy(); weights = desired.to_numpy(dtype=float)
    for i in idxs:
        prev = i-1
        last_close = closes[prev]
        prev_equity = cash + qty * last_close
        peak = max(peak, prev_equity)
        cap = 1.0
        if risk:
            cap, halted, cooldown, just_locked, peak = guard(
                prev_equity, peak, initial*.70, halted, cooldown,
                bool(last_close > sma200[prev] and mom20[prev] > 0))
            locked = locked or just_locked
        if locked: cap = 0.
        target = min(float(weights[prev]), cap)
        open_price = opens[i]
        open_equity = cash + qty*open_price
        current = qty*open_price/open_equity if open_equity > 0 else 0.
        trade_usd = 0.
        if (abs(target-current) >= .10-1e-9 or (target == 0 and qty > 1e-12) or (current == 0 and target > 0)) and open_equity > 0:
            delta = target*open_equity - qty*open_price
            if delta > 0:
                trade_usd = min(delta, cash/(1+cost))
                if trade_usd > 0.01:
                    qty += trade_usd/open_price
                    cash -= trade_usd*(1+cost)
            else:
                trade_usd = -min(-delta, qty*open_price)
                if trade_usd < -0.01:
                    qty += trade_usd/open_price
                    cash -= trade_usd*(1-cost)  # negative USD: proceeds LESS the fee/slippage
            if abs(trade_usd) >= 0.01:
                trades += 1
                turnover += abs(trade_usd)/open_equity
        equity = cash + qty*closes[i]
        performance_peak = max(performance_peak, equity)
        exposure += (qty*closes[i]/equity) if equity > 0 else 0.
        equity_hist.append(equity)
        if detail:
            records.append(dict(date=str(pd.Timestamp(dates[i]).date()), equity=equity,
                                dd=equity/performance_peak-1, trade_usd=trade_usd,
                                close=float(closes[i]), open=float(open_price), target=target,
                                units=qty, cash=cash, risk_peak=peak, cooldown=cooldown,
                                locked=locked, halted=halted))
    values = np.array([initial, *equity_hist]); ret = np.diff(values)/values[:-1]
    dd = values/np.maximum.accumulate(values)-1
    years = len(idxs)/365.0
    cagr = (values[-1]/initial)**(1/years)-1 if values[-1] > 0 else -1
    sharpe = (ret.mean()/ret.std(ddof=1)*np.sqrt(365)) if len(ret)>1 and ret.std(ddof=1)>0 else 0.
    result = dict(start=str(pd.Timestamp(dates[idxs[0]]).date()), end=str(pd.Timestamp(dates[idxs[-1]]).date()),
                  final=round(float(values[-1]), 2), cagr=round(float(cagr), 6), mdd=round(float(-dd.min()), 6),
                  sharpe=round(float(sharpe), 4), trades=trades, turnover=round(turnover/years, 2),
                  exposure=round(exposure/len(idxs), 4), locked=locked)
    return (result, pd.DataFrame(records)) if detail else result
