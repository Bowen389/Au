"""Automatic PAPER ledger only: historic next-open replay, today's delayed public quote.

No exchange credentials and no real orders. Never change an already booked hypothetical fill.
"""
import argparse
import datetime as dt
import json
import math
from pathlib import Path

import pandas as pd
import requests

from data import fetch_daily, load
from engine import COST, guard, run
from strategy import features, signal

STATE = Path('paper_portfolio.json')
LEDGER = Path('paper_fills.csv')
META = Path('signal_latest.json')
TEXT = Path('signal_latest.txt')
CONFIG = Path('chosen.json')
QUOTE_API = 'https://data-api.binance.vision/api/v3/ticker/price'


def save_json(path,obj):
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def strategy(f):
    p=json.loads(CONFIG.read_text(encoding='utf-8'))
    if (p.get('family'),p.get('a'),p.get('b')) != ('ema_cross',10,50):
        raise RuntimeError('chosen.json 已改变：需要人工审查规则和报告后才能继续运行模拟盘')
    return signal(f,p)


def quote():
    r=requests.get(QUOTE_API,params={'symbol':'PAXGUSDT'},timeout=20)
    r.raise_for_status()
    price=float(r.json()['price'])
    if not math.isfinite(price) or price<=0:
        raise RuntimeError('无效的最新报价')
    return price,dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def save_fills(fills):
    cols=['execution_date','signal_date','price_source','price','signed_usdt','fee_usdt','units_after','cash_after','equity_after','target','note']
    pd.DataFrame(fills,columns=cols).to_csv(LEDGER,index=False,float_format='%.8f')


def bootstrap(amount,start,f):
    if STATE.exists():
        raise SystemExit('模拟账本已存在；禁止覆盖。请备份后另建全新账本，勿混合不同起始资金。')
    first=pd.Timestamp(start)
    if first < f.date.iloc[1] or first>f.date.iloc[-1]:
        raise SystemExit('起始日需早于最新完整K线，且有上一日信号')
    ss=strategy(f)
    _, trace=run(f,ss,start,str(f.date.iloc[-1].date()),detail=True,initial=amount)
    rows=trace.loc[trace.trade_usd.abs()>=.01]
    fills=[]
    for _,x in rows.iterrows():
        fills.append(dict(execution_date=x['date'],signal_date=str((pd.Timestamp(x['date'])-pd.Timedelta(days=1)).date()),
                          price_source='历史下一日开盘价（回测假设）',price=float(x['open']),
                          signed_usdt=float(x.trade_usd),fee_usdt=abs(float(x.trade_usd))*COST,
                          units_after=float(x.units),cash_after=float(x.cash),equity_after=float(x.equity),
                          target=float(x.target),note='history-replayed; no real fill'))
    last=trace.iloc[-1]
    s=dict(mode='PAPER ONLY; NOT EXCHANGE ACCOUNT',start_date=start,start_equity=amount,
           cash=float(last.cash),units=float(last.units),risk_peak=float(last.risk_peak),
           halted=bool(last.halted),cooldown=int(last.cooldown),locked=bool(last.locked),
           last_execution_date=last['date'],last_signal_date=str((pd.Timestamp(last['date'])-pd.Timedelta(days=1)).date()),
           last_price=float(last.close),last_equity=float(last.equity),last_target=float(last.target),
           last_cap=0. if bool(last.locked or last.halted) else 1.,fills=fills)
    save_json(STATE,s); save_fills(fills)
    print(f'模拟盘回填：{start} 至 {last["date"]}，{amount:,.2f} USDT → {last.equity:,.2f} USDT；历史模拟成交 {len(fills)} 笔。没有真实下单。')
    return s


def execute(s,prev,weight,price,date,source,mark_price,note=''):
    """Prior complete close for signal/risk; trade at next open or latest public quote."""
    if date<=s['last_execution_date']:
        raise ValueError('已经模拟过该日，拒绝重复记账')
    last_equity=s['cash']+s['units']*float(prev.close)
    peak=max(s['risk_peak'],last_equity)
    cap,halted,cooldown,just_locked,peak=guard(last_equity,peak,s['start_equity']*.70,
                s['halted'],s['cooldown'],bool(prev.close>prev.sma200 and prev.mom20>0))
    s.update(risk_peak=peak,halted=halted,cooldown=cooldown,locked=s['locked'] or just_locked)
    target=min(float(weight),cap)
    if s['locked']: target=0.
    before=s['cash']+s['units']*price
    current=s['units']*price/before if before>0 else 0.
    need=abs(target-current)>=.10-1e-9 or (target==0 and s['units']>1e-12) or (current==0 and target>0)
    usd=0.
    if need and before>0:
        delta=target*before-s['units']*price
        if delta>0:usd=min(delta,s['cash']/(1+COST))
        else:usd=-min(-delta,s['units']*price)
        if abs(usd)<.01:usd=0.
    if usd:
        fee=abs(usd)*COST
        s['units']+=usd/price
        s['cash']-=usd+fee
        if abs(s['units'])<1e-10:s['units']=0.
        if abs(s['cash'])<1e-8:s['cash']=0.
        s['fills'].append(dict(execution_date=date,signal_date=str(prev.date.date()),
                               price_source=source,price=price,signed_usdt=usd,fee_usdt=fee,
                               units_after=s['units'],cash_after=s['cash'],
                               equity_after=s['cash']+s['units']*mark_price,
                               target=target,note=note or 'paper only; no real fill'))
    s['last_execution_date']=date
    s['last_signal_date']=str(prev.date.date())
    s['last_price']=mark_price
    s['last_target']=target
    s['last_cap']=cap
    s['last_equity']=s['cash']+s['units']*mark_price
    return usd,target,cap


def daily(f,s,live_price,quote_time):
    ss=strategy(f)
    now=dt.datetime.now(dt.timezone.utc)
    today=str(now.date())
    last=str(f.date.iloc[-1].date())
    if f.date.iloc[-1].date()!=now.date()-dt.timedelta(days=1):
        raise RuntimeError(f'行情过期，期待昨日已收盘UTC日K，得到 {last}')
    if today<s['last_execution_date']:raise RuntimeError('模拟账本的执行日期晚于今天')
    missed=[]
    # If Actions missed dates, replay only then-available historical opens, prominently labelled.
    for i in range(1,len(f)):
        day=str(f.date.iloc[i].date())
        if s['last_execution_date']<day<=last:
            usd,_,_=execute(s,f.iloc[i-1],ss.iloc[i-1],float(f.iloc[i]['open']),day,
                             '历史下一日开盘价（漏跑补账）',float(f.iloc[i]['close']),
                             note='missed-day reconstruction; no real fill')
            missed.append(day)
    if today>s['last_execution_date']:
        usd,target,cap=execute(s,f.iloc[-1],ss.iloc[-1],live_price,today,
                               '执行时币安公开报价（模拟价）',live_price)
        already=False
    else:
        usd=0.;already=True
        # Do not move the account's holdings or retroactively change any booked fill.
        target=float(s.get('last_target',ss.iloc[-1]))
        cap=float(s.get('last_cap',0. if s['locked'] else 1.))
    save_json(STATE,s);save_fills(s['fills'])
    action='模拟买入' if usd>0 else '模拟卖出' if usd<0 else '模拟持有'
    title=f'{action} {abs(usd):,.0f} USDT｜PAXGUSDT｜信号日 {last}' if usd else f'{action}｜PAXGUSDT｜信号日 {last}'
    position=s['units']*live_price
    equity=s['cash']+position
    msg='\n'.join([
        '【纯模拟 / 非真实成交】'+title,
        f'当前模拟本金基准 {s["start_equity"]:,.2f} USDT；从 {s["start_date"]} 开始；当前模拟资产 {equity:,.2f} USDT',
        f'完整UTC日线信号：{last}，EMA10 {f.iloc[-1].ema10:,.2f} / EMA50 {f.iloc[-1].ema50:,.2f}；基础目标 {ss.iloc[-1]:.0%}；风控后目标 {target:.0%}',
        f'本次假设成交金额：{usd:+,.2f} USDT；成交后模拟现金 {s["cash"]:,.2f} USDT、PAXG {s["units"]:.8f}（按报价约 {position:,.2f} USDT）',
        f'模拟成交参考价 {live_price:,.2f} USDT/PAXG，报价请求完成时间 {quote_time}；单边假设成本 0.15%。',
        f'已记模拟成交 {len(s["fills"])} 笔；风控周期峰值 {s["risk_peak"]:,.2f}；是否暂停 {s["halted"] or s["locked"]}。',
        '若当日重复运行，仅更新展示报价，不再重复记模拟成交。' if already else '',
        '⚠ 漏跑日已按历史开盘价补模拟账（不是当时可执行订单）：'+', '.join(missed) if missed else '',
        '⚠ 历史回填按次日UTC开盘价、当天自动记账用信号发送时公开报价；两者口径不同，不等于实际成交。',
        '⚠ 此账户是模拟账本；无交易所 API 密钥，没有真实委托、资金或持仓；30% 风控阈值不是损失保证。',
    ])+'\n'
    TEXT.write_text(msg,encoding='utf-8')
    save_json(META,dict(title=title,date=last,action=action,suggested_usd=round(usd,2),
                        estimated_equity=round(equity,2),quote=live_price,paper=True))
    print(msg)


def main():
    ap=argparse.ArgumentParser(description='模拟盘：自动回填、自动算金额、自动记假设成交；绝不真实下单')
    ap.add_argument('--init',type=float,metavar='USDT',help='仅首次，创建历史回填模拟账本')
    ap.add_argument('--start',default='2026-02-01',help='历史模拟起始日，默认2026-02-01')
    ap.add_argument('--offline',action='store_true',help='用本地完整K线并模拟在下一天开盘价成交，仅供测试')
    args=ap.parse_args()
    if args.init is not None and (not math.isfinite(args.init) or args.init<=0):
        ap.error('--init 必须为正数有限 USDT')
    raw=load(freshness=True) if args.offline else fetch_daily()
    f=features(raw)
    if args.init is not None:
        bootstrap(args.init,args.start,f)
        return
    if not STATE.exists():
        raise SystemExit('缺少 paper_portfolio.json；先运行 python paper.py --init 10000 --start 2026-02-01')
    s=json.loads(STATE.read_text(encoding='utf-8'))
    if s.get('mode')!='PAPER ONLY; NOT EXCHANGE ACCOUNT':raise RuntimeError('模拟账本标志损坏；拒绝运行')
    if args.offline:
        # closed bar only; as-if next open is not available without accessing a partial candle
        p=float(f.iloc[-1].close)
        qt='离线演示：上一完整日线收盘价（不是实时可执行报价）'
    else:p,qt=quote()
    daily(f,s,p,qt)


if __name__=='__main__':main()
