import pandas as pd
import pytest

from engine import run
from paper import execute


def state():
    return dict(start_equity=10000.,cash=10000.,units=0.,risk_peak=10000.,
                halted=False,cooldown=0,locked=False,last_execution_date='2026-01-31',fills=[])


def prev(day='2026-01-31'):
    return pd.Series(dict(date=pd.Timestamp(day),close=100.,sma200=90.,mom20=.1))


def test_auto_buy_sell_deducts_fees_and_does_not_repeat():
    s=state()
    usd,target,cap=execute(s,prev(),1.,100.,'2026-02-01','test',100.)
    assert usd==pytest.approx(10000/1.0015)
    assert s['cash']==pytest.approx(0.)
    assert s['units']==pytest.approx(usd/100)
    assert len(s['fills'])==1
    sold,_,_=execute(s,prev('2026-02-01'),0.,100.,'2026-02-02','test',100.)
    assert sold<0 and s['cash']==pytest.approx(10000*(1-.0015)/(1+.0015))
    assert s['units']==pytest.approx(0.)
    assert len(s['fills'])==2
    with pytest.raises(ValueError,match='重复'):
        execute(s,prev('2026-02-01'),0.,100.,'2026-02-02','test',100.)


def test_engine_respects_other_initial_amount():
    f=pd.DataFrame(dict(date=pd.to_datetime(['2024-01-01','2024-01-02','2024-01-03']),
                        open=[100.]*3,close=[100.]*3,sma200=[1.]*3,mom20=[1.]*3))
    r=run(f,pd.Series([1.,0.,0.]),'2024-01-02','2024-01-03',risk=False,initial=20000)
    assert r['final']==pytest.approx(round(20000*(1-.0015)/(1+.0015),2))
