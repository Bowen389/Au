"""Binance public market-data only: PAXGUSDT, daily UTC candles."""
import datetime as dt
import time
from pathlib import Path

import pandas as pd
import requests

API = 'https://data-api.binance.vision/api/v3/klines'
SYMBOL = 'PAXGUSDT'
START_MS = int(dt.datetime(2020, 8, 28, tzinfo=dt.timezone.utc).timestamp() * 1000)
COLUMNS = ['date', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_volume',
           'trades', 'taker_base', 'taker_quote', 'ignore']


def fetch_daily(path='paxg_daily.csv', now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        raise ValueError('now must be timezone-aware UTC')
    today = now.astimezone(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end_ms = int(today.timestamp() * 1000) - 1  # exclude today's still-forming UTC candle
    rows = []
    since = START_MS
    session = requests.Session()
    while since <= end_ms:
        for retry in range(5):
            try:
                resp = session.get(API, params=dict(symbol=SYMBOL, interval='1d',
                                   startTime=since, endTime=end_ms, limit=1000), timeout=25)
                resp.raise_for_status()
                chunk = resp.json()
                if not isinstance(chunk, list):
                    raise RuntimeError(str(chunk)[:300])
                break
            except (requests.RequestException, ValueError, RuntimeError):
                if retry == 4:
                    raise
                time.sleep(min(2 ** retry, 12))
        if not chunk:
            break
        rows.extend(chunk)
        new_since = int(chunk[-1][0]) + 86400000
        if new_since <= since:
            raise RuntimeError('pagination made no progress')
        since = new_since
    if not rows:
        raise RuntimeError('no candles fetched')
    df = pd.DataFrame(rows, columns=COLUMNS)
    df['date'] = pd.to_datetime(df['date'], unit='ms', utc=True).dt.tz_localize(None)
    for c in ['open', 'high', 'low', 'close', 'volume']:
        df[c] = pd.to_numeric(df[c], errors='raise')
    df = df[['date', 'open', 'high', 'low', 'close', 'volume']]
    validate(df, today)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, float_format='%.8f')
    return df


def validate(df, today=None, freshness=False):
    if df.empty or df.date.isna().any() or df.date.duplicated().any() or not df.date.is_monotonic_increasing:
        raise ValueError('empty/duplicate/unsorted dates')
    if not df.date.diff().iloc[1:].eq(pd.Timedelta(days=1)).all():
        raise ValueError('missing UTC daily candles')
    if (df[['open', 'high', 'low', 'close']] <= 0).any().any() or (df.volume < 0).any():
        raise ValueError('invalid nonpositive price / negative volume')
    if (df.high < df[['open', 'close', 'low']].max(axis=1)).any() or (df.low > df[['open', 'close', 'high']].min(axis=1)).any():
        raise ValueError('inconsistent OHLC')
    if today is not None:
        today = pd.Timestamp(today).tz_localize(None)
        if df.date.iloc[-1] >= today:
            raise ValueError('unclosed candle included')
        if freshness and df.date.iloc[-1] != today - pd.Timedelta(days=1):
            raise ValueError(f'stale data: latest closed candle {df.date.iloc[-1].date()}, expected {(today-pd.Timedelta(days=1)).date()}')
    return df


def load(path='paxg_daily.csv', freshness=False, now=None):
    df = pd.read_csv(path, parse_dates=['date'])
    today = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return validate(df, today, freshness=freshness)


if __name__ == '__main__':
    df = fetch_daily()
    print(f'{len(df)} closed daily UTC bars, {df.date.iloc[0].date()}–{df.date.iloc[-1].date()}')
