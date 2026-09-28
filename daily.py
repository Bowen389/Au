"""Manual-execution only daily signal and account ledger. NEVER places exchange orders."""
import argparse
import datetime as dt
import json
import math
from pathlib import Path

from data import fetch_daily, load
from engine import COST, guard
from strategy import features, signal

STATE = Path('portfolio.json')
OUTPUT = Path('signal_latest.txt')
META = Path('signal_latest.json')
CHOSEN = Path('chosen.json')


def write_json(path, obj):
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')


def get_state():
    if not STATE.exists():
        raise SystemExit('未初始化：先运行 python daily.py --init 10000 （单位 USDT）')
    return json.loads(STATE.read_text(encoding='utf-8'))


def positive(x):
    val=float(x)
    if not math.isfinite(val) or val<=0: raise argparse.ArgumentTypeError('must be positive and finite')
    return val


def main():
    ap=argparse.ArgumentParser(description='PAXGUSDT 日线建议；实际交易须手工确认并记账')
    g=ap.add_mutually_exclusive_group()
    g.add_argument('--init',type=positive,help='初始化 USDT 现金，禁止覆盖已有账本')
    g.add_argument('--record',action='store_true',help='记录本日真实成交：--usd 正买负卖，--price 实际成交价')
    g.add_argument('--set',action='store_true',help='校准现金与 PAXG 数量（不是出入金）')
    ap.add_argument('--usd',type=float); ap.add_argument('--price',type=positive)
    ap.add_argument('--fee-usdt',type=float,help='实际交易费用 USDT；省略时以回测单边0.15%%代替')
    ap.add_argument('--cash',type=float); ap.add_argument('--units',type=float)
    ap.add_argument('--offline',action='store_true',help='仅用本地已收盘历史数据；演示/调试')
    a=ap.parse_args()
    if a.init is not None:
        if STATE.exists(): raise SystemExit('账本已存在；拒绝意外重置，请先备份并手动移除 portfolio.json')
        write_json(STATE,dict(start_equity=a.init,cash=a.init,units=0.,risk_peak=a.init,
                     halted=False,cooldown=0,locked=False,risk_date=None,risk_cap=1.,
                     last_trade_signal=None,pending=None,trades=[]))
        print(f'初始化成功：现金 {a.init:.2f} USDT；PAXG 0；未下单。');return
    s=get_state()
    if a.record:
        p=s.get('pending')
        if not p or s.get('last_trade_signal')==p['date']:
            raise SystemExit('没有尚未记录的今日建议；请先运行 python daily.py')
        if a.usd is None or a.price is None or a.usd==0 or not (-1e9<a.usd<1e9):
            raise SystemExit('需要有效非零 --usd（正买负卖）与正数 --price')
        if p['suggested_usd']==0 or a.usd*p['suggested_usd']<=0:
            raise SystemExit('成交方向与建议不一致；需要校准请用 --set')
        fee=abs(a.usd)*COST if a.fee_usdt is None else a.fee_usdt
        if not (0<=fee<1e9):raise SystemExit('手续费必须为有限非负金额')
        if a.usd>0 and a.usd+fee>s['cash']+1e-7: raise SystemExit('现金不足（含费用）')
        if a.usd<0 and -a.usd/a.price>s['units']+1e-10: raise SystemExit('PAXG 余额不足')
        if a.usd>0:
            future_equity=s['cash']+s['units']*a.price-fee
            future_units=s['units']+a.usd/a.price
            if future_equity<=0 or future_units*a.price/future_equity>p['target']+0.05:
                raise SystemExit('买入后仓位超出信号目标（加5个百分点误差）；拒绝记账')
        s['cash']-=a.usd+fee
        s['units']+=a.usd/a.price
        if abs(s['cash'])<1e-7:s['cash']=0.
        if abs(s['units'])<1e-10:s['units']=0.
        s['last_trade_signal']=p['date'];s['pending']=None
        s['trades'].append(dict(signal_date=p['date'],recorded_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                                notional_usdt=a.usd,price=a.price,fee_usdt=fee,fee_is_assumed=a.fee_usdt is None,
                                units_after=s['units'],cash_after=s['cash']))
        write_json(STATE,s)
        print(f'已记录真实成交: {"买入" if a.usd>0 else "卖出"} {abs(a.usd):.2f} USDT @ {a.price:.2f}，{ "估计" if a.fee_usdt is None else "实际" }费用 {fee:.2f} USDT；未调用交易所下单');return
    if a.set:
        if a.cash is None or a.units is None or not math.isfinite(a.cash) or not math.isfinite(a.units) or a.cash<0 or a.units<0:
            raise SystemExit('--set 必须同时指定非负 --cash 和 --units；不用于入金/出金')
        s['cash']=a.cash;s['units']=a.units;s['pending']=None
        write_json(STATE,s);print('已校准余额；风控峰值未重置。出入金需要重新计算权益基准，不能直接 --set。');return
    df=load(freshness=False) if a.offline else fetch_daily()
    # In online mode, reject stale data rather than sending old signals labelled as new.
    if not a.offline: load(freshness=True)
    f=features(df)
    p=json.loads(CHOSEN.read_text(encoding='utf-8'))
    if (p.get('family'),p.get('a'),p.get('b')) != ('ema_cross',10,50):
        raise SystemExit('实盘参数不是已审定的 EMA10/50；请人工复核研究与通知文案后再更新部署')
    desired=float(signal(f,p).iloc[-1]); row=f.iloc[-1]
    date=str(row.date.date())
    equity=s['cash']+s['units']*float(row.close)
    if s['risk_date']!=date:
        if s['risk_date'] and date<s['risk_date']:
            raise SystemExit('数据日期早于已处理的信号，拒绝倒退更新账户风控')
        peak=max(float(s['risk_peak']),equity)
        cap,halted,cooldown,locked,peak=guard(equity,peak,.70*s['start_equity'],
                                                s['halted'],s['cooldown'],
                                                bool(row.close>row.sma200 and row.mom20>0))
        s.update(risk_date=date,risk_peak=peak,risk_cap=cap,halted=halted,
                 cooldown=cooldown,locked=s['locked'] or locked)
    target=min(desired,float(s['risk_cap']))
    if s['locked']:target=0.
    held=s['units']*float(row.close)
    current=held/equity if equity>0 else 0.
    need=(abs(target-current)>=.10-1e-9 or (target==0 and s['units']>1e-12) or (current==0 and target>0))
    trade=target*equity-held if need else 0.
    if trade>0:trade=min(trade,s['cash']/(1+COST))
    else:trade=max(trade,-held)
    if abs(trade)<1:trade=0.
    already=s.get('last_trade_signal')==date
    if already:trade=0.
    action='买入' if trade>0 else '卖出' if trade<0 else '无需交易'
    title=f'{action} {abs(trade):,.0f} USDT｜PAXGUSDT｜信号日 {date}' if trade else f'{action}｜PAXGUSDT｜信号日 {date}'
    if s['locked']:title='‼ 账户已触及起始权益 -30% 红线，停止新交易｜'+title
    if a.offline:title='[离线预览] '+title
    text='\n'.join([
        title,
        '仅提示，不自动下单；请在信号日收盘后的下一交易时段核对实时 PAXGUSDT 价格及交易所余额。',
        f'数据: Binance 公开 PAXGUSDT UTC 日K，最后完整K线 {date}（北京时间次日 08:00 收盘）',
        f'算法: EMA10 > EMA50 则目标100%持有，否则0%；最新 EMA10={row.ema10:,.2f} EMA50={row.ema50:,.2f}',
        f'收盘价 {row.close:,.2f} USDT/PAXG；当前账面现金 {s["cash"]:,.2f} USDT，PAXG {s["units"]:.6f}；权益约 {equity:,.2f} USDT',
        f'仓位: 当前 {current:.1%} → 目标 {target:.1%}；风控仓位上限 {s["risk_cap"]:.0%}；本地风险周期回撤 {1-equity/s["risk_peak"]:.1%}',
        f'建议按历史收盘价估算 {action} {abs(trade):,.2f} USDT；实际成交价/金额须按当前行情重算。',
        '执行后必须记录实际成交：python daily.py --record --usd <正买入/负卖出金额> --price <实际成交价>；不成交就不要记账。',
        '⚠ 回测假设单边 0.15% 费用、次日开盘成交；实盘价格/滑点/跳空可能更差，30% 是风控目标不是保证。',
        '⚠ PAXG 为黄金支持的代币，不等同伦敦现货金；USDT、代币发行方、交易所和流动性均有额外风险。',
        '如未收到新信号或数据不完整，暂停交易并查看 GitHub Actions 日志。',
        '本日已记过成交；不重复建议下单。' if already else '',
    ])+'\n'
    # Persist signal identity; repeated runs before trade update pending (never auto-book holdings).
    s['pending']=None if already or trade==0 else dict(date=date,suggested_usd=round(trade,2),close=float(row.close),target=target)
    write_json(STATE,s)
    OUTPUT.write_text(text,encoding='utf-8')
    write_json(META,dict(title=title,date=date,action=action,suggested_usd=round(trade,2),
                         target=target,current=current,close=float(row.close),offline=a.offline,already_recorded=already))
    print(text)


if __name__=='__main__':
    main()
