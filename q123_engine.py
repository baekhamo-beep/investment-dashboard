"""Q123 canonical v2. Signals at completed close, execution at next session open."""
from dataclasses import dataclass
import numpy as np
import pandas as pd

ASSETS = {'BEAR': 'QQQ', 'NORMAL': 'QLD', 'BOOST': 'TQQQ'}
VERSION = 'Q123_CANONICAL_V2'

@dataclass(frozen=True)
class Rules:
    momentum: float = .10
    hold: int = 63
    pullback: float = .08
    recovery: int = 5
    cooldown: int = 21

def indicators(close):
    f = pd.DataFrame({'close': close})
    f['sma50'] = close.rolling(50).mean()
    f['sma200'] = close.rolling(200).mean()
    f['ret63'] = close.pct_change(63, fill_method=None)
    f['ret126'] = close.pct_change(126, fill_method=None)
    above = close > f.sma200
    f['above_days'] = above.groupby((~above).cumsum()).cumsum().astype(int)
    return f

def replay(close, start, rules=Rules()):
    """Initial NORMAL at start open; missing warmup is an error, never an estimate.

    Entry session counts as BOOST day 1. Peak starts at that session's close.
    After exit at session E open, E+1 .. E+21 are flat in TQQQ.
    A signal at E+21 close can therefore execute at E+22 open.
    BEAR has priority; exiting BOOST into BEAR also starts cooldown.
    """
    f = indicators(close)
    start_i = f.index.searchsorted(pd.Timestamp(start))
    if start_i >= len(f) or f.iloc[start_i][['sma200', 'ret126']].isna().any():
        raise ValueError('200-session QQQ warmup required')
    mode, pending, entry, peak, exit_i = 'NORMAL', None, None, None, None
    rows, events = [], []
    for i in range(start_i, len(f)):
        day, r = f.index[i], f.iloc[i]
        if pending:
            old, mode = mode, pending['to']
            events.append({**pending, 'execution_date': str(day.date()),
                           'from_asset': ASSETS[old], 'to_asset': ASSETS[mode]})
            if old == 'BOOST':
                exit_i, entry, peak = i, None, None
            if mode == 'BOOST':
                entry, peak = i, float(r['close'])
            pending = None
        if mode == 'BOOST':
            peak = max(peak, float(r['close']))
        held = i - entry + 1 if entry is not None else 0
        remaining = max(0, exit_i + rules.cooldown - i) if exit_i is not None else 0
        alignment = r['close'] > r.sma50 > r.sma200
        boost = alignment and r.ret63 >= rules.momentum and r.ret126 >= 0 and remaining == 0
        target, reason = mode, 'HOLD'
        if r['close'] < r.sma200:
            target, reason = 'BEAR', 'QQQ < SMA200'
        elif mode == 'BEAR':
            if r.above_days >= rules.recovery and alignment:
                target = 'BOOST' if boost else 'NORMAL'
                reason = 'SMA200 위 5거래일 + 정배열' + (' + BOOST 조건' if boost else '')
        elif mode == 'BOOST':
            if r['close'] < r.sma50:
                target, reason = 'NORMAL', 'QQQ < SMA50'
            elif r['close'] / peak - 1 <= -rules.pullback:
                target, reason = 'NORMAL', f'종가 peak 대비 -{rules.pullback*100:g}%'
            elif held >= rules.hold:
                target, reason = 'NORMAL', f'BOOST {rules.hold}거래일'
        elif boost:
            target, reason = 'BOOST', '정배열 + RET63 ≥ 10% + RET126 ≥ 0'
        if target != mode:
            pending = {'signal_date': str(day.date()), 'from': mode, 'to': target, 'reason': reason}
        rows.append({'date': day, 'mode': mode, 'asset': ASSETS[mode],
                     'target_mode': target, 'target_asset': ASSETS[target], 'reason': reason,
                     'boost_days': held, 'boost_peak': peak,
                     'boost_drawdown': float(r['close']/peak-1) if peak else None,
                     'cooldown_remaining': remaining, 'above_days': int(r.above_days)})
    states = pd.DataFrame(rows).set_index('date')
    return states, events, pending

def simulate(prices, start, end, bps=5, rules=Rules(), asset=None, initial=100_000_000):
    """Price returns only. No contributions/dividends/FX; fractional shares.

    Annual realized P/L is netted then 22% above 2.5m is booked externally.
    Current-year accrual is reported as liability; it is not a payment record.
    Final liquidation uses the remaining allowance in the same tax year.
    """
    q = prices['QQQ']['close'].loc[:end]
    states, events, pending = replay(q, start, rules)
    days = states.index
    cost = bps / 10000
    equity, realized, orders = [], {}, 1
    value, units, basis, held_asset = float(initial), None, None, None
    for day in days:
        target = asset or states.loc[day, 'asset']
        if units is None or held_asset != target:
            if units is not None:
                proceeds = units * prices[held_asset].loc[day, 'open'] * (1-cost)
                realized[day.year] = realized.get(day.year, 0) + proceeds - basis
                value, orders = proceeds, orders + 2
            basis = value
            units = value / (prices[target].loc[day, 'open'] * (1+cost))
            held_asset = target
        value = units * prices[held_asset].loc[day, 'close']
        equity.append(value)
    curve = pd.Series(equity, index=days)
    yrs = (days[-1] - days[0]).days / 365.25
    cagr = (value / initial)**(1/yrs)-1
    # Include initial NAV so entry fees can be part of drawdown.
    levels = np.r_[initial, curve.to_numpy()]
    mdd = float((levels / np.maximum.accumulate(levels)-1).min())
    tax = sum(max(0, gain-2_500_000)*.22 for gain in realized.values())
    final_gain = value*(1-cost)-basis
    final_year = days[-1].year
    old_liability = max(0, realized.get(final_year, 0)-2_500_000)*.22
    new_liability = max(0, realized.get(final_year, 0)+final_gain-2_500_000)*.22
    liquidation_delta = new_liability-old_liability
    net = value-tax
    liquid_net = value*(1-cost)-tax-liquidation_delta
    metrics = {'start': str(days[0].date()), 'end': str(days[-1].date()),
               'cagr': cagr, 'mdd': mdd, 'calmar': cagr/abs(mdd) if mdd < 0 else None, 'final': float(value),
               'orders': orders if not asset else 1, 'transitions': len(events) if not asset else 0,
               'tax_liability': tax, 'net': net, 'net_cagr': (net/initial)**(1/yrs)-1 if net>0 else None,
               'liquidation_tax_delta': liquidation_delta, 'liquid_net': liquid_net,
               'annual_realized': realized}
    return metrics, curve, states, events, pending

def snapshot(close, start='2010-02-11', rules=Rules()):
    states, events, pending = replay(close, start, rules)
    last = {k: (None if pd.isna(v) else v) for k,v in states.iloc[-1].to_dict().items()}
    last.update({'version': VERSION, 'as_of': str(states.index[-1].date()),
                 'signal_date': pending['signal_date'] if pending else None,
                 'execution': 'NEXT_SESSION_OPEN' if pending else None,
                 'last_transition': events[-1] if events else None})
    return last
