import datetime as dt

import pandas as pd
import pytest

from data import validate
from engine import guard, run
from strategy import features, signal


def toy():
    f=pd.DataFrame(dict(date=pd.to_datetime(['2024-01-01','2024-01-02','2024-01-03']),
                        open=[100.,200.,100.], high=[100.,200.,100.], low=[100.,200.,100.],
                        close=[100.,200.,100.], sma200=[1.,1.,1.], mom20=[1.,1.,1.]))
    return f


def test_next_open_not_same_close():
    f=toy(); weights=pd.Series([1.,0.,0.])
    _,trace=run(f,weights,'2024-01-02','2024-01-03',risk=False,cost=0,detail=True)
    assert trace.iloc[0].units==pytest.approx(50.)  # $10k / NEXT open 200, not close 100
    assert trace.iloc[1].units==pytest.approx(0.)


def test_sell_fee_is_expense():
    f=toy(); f['open']=[100.,100.,100.];f['close']=[100.,100.,100.]
    r=run(f,pd.Series([1.,0.,0.]),'2024-01-02','2024-01-03',risk=False,cost=.0015)
    assert r['final']==pytest.approx(round(10000*(1-.0015)/(1+.0015),2))


def test_halt_and_terminal_red_line():
    cap,halt,cool,locked,peak=guard(7450,10000,7000,False,0,False)
    assert cap==0 and halt and not locked
    assert guard(6900,10000,7000,halt,cool,False)[3] is True
    assert guard(7450,10000,7000,True,10,True)[0]==1


def test_no_duplicate_or_partial_utc_candle():
    df=pd.DataFrame(dict(date=pd.to_datetime(['2024-01-01','2024-01-02']),
                         open=[100,100],high=[100,100],low=[100,100],close=[100,100],volume=[1,1]))
    validate(df,dt.datetime(2024,1,3,tzinfo=dt.timezone.utc),freshness=True)
    with pytest.raises(ValueError,match='unclosed'):
        validate(df,dt.datetime(2024,1,2,tzinfo=dt.timezone.utc))
    with pytest.raises(ValueError,match='duplicate'):
        validate(pd.concat([df,df.iloc[-1:]],ignore_index=True))
    with pytest.raises(ValueError,match='stale'):
        validate(df,dt.datetime(2024,1,4,tzinfo=dt.timezone.utc),freshness=True)


def test_feature_signal_is_causal():
    n=250
    src=pd.DataFrame(dict(date=pd.date_range('2024-01-01',periods=n),
                          open=range(100,100+n), high=range(101,101+n),
                          low=range(99,99+n),close=range(100,100+n),volume=[1]*n))
    cfg=dict(family='ema_cross',a=10,b=50)
    original=signal(features(src),cfg)
    changed=src.copy();changed.loc[240:,'close']*=2
    after=signal(features(changed),cfg)
    assert original.iloc[:240].equals(after.iloc[:240])


def test_halt_cooldown_accumulates_and_rearms():
    # after a >=25% halt the account sits flat below the peak; the cooldown must still count to 10
    halted, cool = False, 0
    for day in range(1, 11):
        cap, halted, cool, locked, peak = guard(7450, 10000, 7000, halted, cool, False)
        assert cap == 0 and halted and not locked and cool == day
    assert guard(7450, 10000, 7000, halted, cool, False)[1] is True   # trend not ok -> stays halted
    cap, halted, cool, locked, peak = guard(7450, 10000, 7000, halted, cool, True)
    assert cap == 1 and not halted and cool == 0 and peak == 7450     # re-armed, risk-cycle peak reset
