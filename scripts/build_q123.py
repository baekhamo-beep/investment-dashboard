"""Rebuild all Q123 artifacts: python scripts/build_q123.py [--source-dir DIR]."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd
import exchange_calendars as xc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from q123_engine import Rules, VERSION, simulate, snapshot

def prepare(source):
    sessions = xc.get_calendar('XNYS', start='2008-01-01', end='2027-01-01').sessions.tz_localize(None)
    audit, frames = {}, {}
    for ticker in ['QQQ', 'QLD', 'TQQQ', 'SPY']:
        raw = pd.read_csv(source / f'{ticker.lower()}2010~.csv', encoding='utf-8-sig', dtype=str)
        dates = pd.to_datetime(raw['날짜'].str.replace(' ', '', regex=False))
        f = pd.DataFrame({'date': dates, 'open': pd.to_numeric(raw['시가'].str.replace(',', '', regex=False)),
                          'close': pd.to_numeric(raw['종가'].str.replace(',', '', regex=False))})
        mask = f.date.isin(sessions)
        removed = f.loc[~mask].date.dt.strftime('%Y-%m-%d').tolist()
        f = f.loc[mask].set_index('date').sort_index()
        if f.index.duplicated().any() or (f <= 0).any().any() or f.isna().any().any():
            raise ValueError(f'{ticker}: invalid/duplicate data')
        frames[ticker] = f
        audit[ticker] = {'removed_non_sessions': removed, 'rows': len(f)}
    common = frames['TQQQ'].index
    for t, f in frames.items():
        if not common.isin(f.index).all():
            raise ValueError(f'{t}: missing ETF session; do not forward fill')
    expected = sessions[(sessions >= common[0]) & (sessions <= common[-1])]
    if not expected.equals(common):
        raise ValueError('TQQQ calendar gaps')
    warm = pd.read_csv(ROOT/'data/qqq_warmup_source.csv', parse_dates=['Date']).set_index('Date')
    warm = warm.loc[warm.index < frames['QQQ'].index[0], ['Open','Close']].rename(columns=str.lower)
    warm = warm.loc[warm.index.isin(sessions)]
    frames['QQQ'] = pd.concat([warm, frames['QQQ']])
    for t, f in frames.items():
        f.rename_axis('date').to_csv(ROOT/f'data/{t.lower()}_price.csv', float_format='%.8f')
    return audit

def avg(results):
    fields = ['cagr','mdd','calmar','final','orders','transitions','tax_liability','net','net_cagr','liquid_net']
    return {k: float(np.mean([r[k] for r in results])) for k in fields} | {'worst_mdd': min(r['mdd'] for r in results)}

def build(source=None):
    audit = prepare(Path(source)) if source else json.loads((ROOT/'docs/q123_results.json').read_text())['data_audit']
    prices = {t: pd.read_csv(ROOT/f'data/{t.lower()}_price.csv', parse_dates=['date']).set_index('date')
              for t in ['QQQ','QLD','TQQQ','SPY']}
    end = '2026-05-15'
    starts = ['2010-02-11'] + [f'{y}-01-01' for y in range(2011,2022)]
    runs = [simulate(prices,s,end) for s in starts]
    bench = {'Q123': avg([r[0] for r in runs])}
    for t in prices:
        bench[t] = avg([simulate(prices,s,end,asset=t)[0] for s in starts])
    costs = {str(b): avg([simulate(prices,s,end,bps=b)[0] for s in starts]) for b in [0,2.5,5,10,15]}
    transitions = Counter()
    for r in runs:
        transitions.update(e['from_asset']+' → '+e['to_asset'] for e in r[3])
    windows = [('2010-02-11','2013-12-31','2014-01-01','2017-12-31'),
               ('2010-02-11','2017-12-31','2018-01-01','2021-12-31'),
               ('2014-01-01','2021-12-31','2022-01-01',end)]
    wf = [{'train':simulate(prices,a,b)[0], 'oos':simulate(prices,c,d)[0]} for a,b,c,d in windows]
    configs = [('BASE',Rules()),('EXIT_6',Rules(pullback=.06)),('EXIT_10',Rules(pullback=.10)),
               ('HOLD_42',Rules(hold=42)),('HOLD_84',Rules(hold=84)),
               ('MOM_8',Rules(momentum=.08)),('MOM_12',Rules(momentum=.12))]
    sensitivity = {name: avg([simulate(prices,s,end,rules=rules)[0] for s in starts]) for name,rules in configs}
    # Resample already realized strategy returns. This is path uncertainty, not signal re-optimization.
    curve = runs[0][1]
    returns = curve.pct_change().dropna().to_numpy()
    rng, samples, block = np.random.default_rng(20261006), [], 21
    for _ in range(500):
        indices = rng.integers(0,len(returns),size=int(np.ceil(len(returns)/block)))
        path = np.concatenate([returns[(i+np.arange(block))%len(returns)] for i in indices])[:len(returns)]
        levels = np.r_[1.,np.cumprod(1+path)]
        cagr = levels[-1]**(365.25/(curve.index[-1]-curve.index[0]).days)-1
        mdd = (levels/np.maximum.accumulate(levels)-1).min()
        samples.append([cagr,mdd,cagr/abs(mdd)])
    samples = np.array(samples)
    latest = simulate(prices,starts[0],str(prices['TQQQ'].index[-1].date()))
    report = {'version': VERSION, 'method': {'return_type':'PRICE_RETURN', 'dividends':False,
              'fx':False,'initial_krw':100_000_000,'contributions':False,'fractional_shares':True,
              'bps_per_order':5,'starts':starts,'end':end,'initial_mode':'NORMAL',
              'aggregation':'arithmetic mean of scenario metrics; Calmar is mean of individual ratios',
              'bootstrap':{'seed':20261006,'block':21,'draws':500,'method':'circular blocks of realized strategy returns'},
              'tax':'external annual liability estimate: net realized gain less 2.5m then 22%; fixed FX; not tax filing'},
              'data_audit':audit,'benchmark':bench,'scenarios':[r[0] for r in runs], 'costs':costs,
              'transition_means':{k:v/len(runs) for k,v in sorted(transitions.items())},
              'windows':wf,'sensitivity':sensitivity,
              'bootstrap':{'cagr':float(samples[:,0].mean()),'mdd':float(samples[:,1].mean()),
                'calmar':float(samples[:,2].mean()),'p_cagr_15':float((samples[:,0]>=.15).mean()),
                'p_mdd_65':float((samples[:,1]<-.65).mean())},
              'latest_metrics':latest[0], 'events':latest[3],
              'source_hashes':{t:hashlib.sha256((ROOT/f'data/{t.lower()}_price.csv').read_bytes()).hexdigest() for t in prices}}
    (ROOT/'docs/q123_results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    live = snapshot(prices['QQQ']['close'])
    ind = __import__('q123_engine').indicators(prices['QQQ']['close']).iloc[-1]
    live['indicators'] = {k:float(ind[k]) for k in ['close','sma50','sma200','ret63','ret126']}
    live['price_basis'] = 'unadjusted close; supplied Investing CSV'
    cal = xc.get_calendar('XNYS', start='2008-01-01', end='2027-01-01')
    next_day = cal.next_session(pd.Timestamp(live['as_of']))
    live['next_session'] = str(next_day.date())
    live['next_open_utc'] = cal.session_open(next_day).isoformat()
    (ROOT/'docs/q123_state.json').write_text(json.dumps(live,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    pd.DataFrame(latest[3]).to_csv(ROOT/'docs/q123_transitions.csv',index=False)
    from render_q123 import render
    render()
    print(json.dumps({'benchmark':bench,'latest':live,'recent':[e for e in latest[3] if e['signal_date']>='2025-03-01']},ensure_ascii=False,indent=2))

if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--source-dir'); args=p.parse_args(); build(args.source_dir)
