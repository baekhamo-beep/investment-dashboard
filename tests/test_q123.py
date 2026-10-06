import json
from pathlib import Path
import unittest
from unittest.mock import patch
import types
import importlib.util
import numpy as np
import pandas as pd
from q123_engine import Rules, replay, simulate, snapshot

ROOT = Path(__file__).resolve().parents[1]

class EngineTests(unittest.TestCase):
    def fixture(self,n=100):
        index=pd.bdate_range('2020-01-01',periods=n)
        return pd.DataFrame({'close':110.,'sma50':105.,'sma200':100.,
                             'ret63':.11,'ret126':.1,'above_days':10},index=index)

    def run_fixture(self,f,rules=Rules()):
        with patch('q123_engine.indicators',return_value=f):
            return replay(f['close'],f.index[0],rules)

    def test_signal_then_next_open_and_final_pending(self):
        f=self.fixture(2);states,events,pending=self.run_fixture(f)
        self.assertEqual(states.iloc[0]['mode'],'NORMAL')
        self.assertEqual(events[0]['signal_date'],str(f.index[0].date()))
        self.assertEqual(events[0]['execution_date'],str(f.index[1].date()))
        self.assertEqual(states.iloc[1]['mode'],'BOOST')
        states,events,pending=self.run_fixture(f.iloc[:1])
        self.assertEqual(len(events),0);self.assertEqual(pending['to'],'BOOST')

    def test_bear_priority_and_five_complete_recovery_closes(self):
        f=self.fixture(8);f.loc[f.index[0],'close']=90
        f.loc[f.index[1:6],'above_days']=np.arange(1,6)
        states,events,_=self.run_fixture(f)
        self.assertEqual(events[0]['to'],'BEAR')
        self.assertEqual(events[1]['to'],'BOOST')
        self.assertEqual(events[1]['signal_date'],str(f.index[5].date()))
        self.assertEqual(events[1]['execution_date'],str(f.index[6].date()))

    def test_sixtythird_close_exit_and_twentyone_flat_sessions(self):
        f=self.fixture(90);states,events,_=self.run_fixture(f)
        self.assertEqual(events[1]['signal_date'],str(f.index[63].date()))
        self.assertEqual(events[1]['execution_date'],str(f.index[64].date()))
        self.assertEqual(events[2]['signal_date'],str(f.index[85].date()))
        self.assertEqual(events[2]['execution_date'],str(f.index[86].date()))
        self.assertTrue((states.iloc[65:86]['mode']=='NORMAL').all())

    def test_peak_starts_at_entry_close_and_bear_exit_starts_cooldown(self):
        f=self.fixture(5);f.loc[f.index[0],'close']=200
        states,events,_=self.run_fixture(f)
        self.assertEqual(states.iloc[1]['boost_peak'],110)
        self.assertEqual(len(events),1)
        f.loc[f.index[2],'close']=90
        states,events,_=self.run_fixture(f)
        self.assertEqual(events[1]['to'],'BEAR')
        self.assertEqual(states.iloc[3]['cooldown_remaining'],21)

    def test_pullback_and_equality(self):
        f=self.fixture(5);f.loc[f.index[1],'close']=120
        f.loc[f.index[2],'close']=110.4 # exactly -8%; stays above SMA50
        _,events,_=self.run_fixture(f)
        self.assertEqual(events[1]['reason'],'종가 peak 대비 -8%')
        f=self.fixture(3);f['close']=100.;f['ret63']=0.;f['above_days']=0
        states,events,_=self.run_fixture(f)
        self.assertEqual(len(events),0);self.assertEqual(states.iloc[-1]['mode'],'NORMAL')

    def test_execution_uses_new_asset_open_and_old_asset_gap(self):
        f=self.fixture(3)
        prices={t:pd.DataFrame({'open':100.,'close':100.},index=f.index) for t in ['QQQ','QLD','TQQQ']}
        prices['QLD'].loc[f.index[1],'open']=80
        prices['TQQQ'].loc[f.index[1],'open']=50
        prices['TQQQ'].loc[f.index[1:],'close']=60
        with patch('q123_engine.indicators',return_value=f):
            m,curve,_,_,_=simulate(prices,f.index[0],f.index[-1],bps=0)
        self.assertAlmostEqual(curve.iloc[1],96_000_000)
        self.assertEqual(m['orders'],3)
        self.assertEqual(m['tax_liability'],0)

    def test_external_tax_does_not_change_account_and_final_year_nets(self):
        f=self.fixture(3)
        prices={t:pd.DataFrame({'open':100.,'close':100.},index=f.index) for t in ['QQQ','QLD','TQQQ']}
        prices['QLD'].loc[f.index[1],'open']=200
        prices['TQQQ'].loc[f.index[1:],'close']=50
        with patch('q123_engine.indicators',return_value=f):
            m,*_=simulate(prices,f.index[0],f.index[-1],bps=0)
        self.assertEqual(m['final'],100_000_000)
        self.assertEqual(m['tax_liability'],(100_000_000-2_500_000)*.22)
        self.assertEqual(m['liquid_net'],100_000_000) # final loss nets same year's realized gain

    def test_snapshot_valid_json_and_idempotent(self):
        f=self.fixture(3);f['ret63']=0.
        with patch('q123_engine.indicators',return_value=f):
            a=snapshot(f['close'],str(f.index[0].date()));b=snapshot(f['close'],str(f.index[0].date()))
        self.assertEqual(a,b);json.dumps(a,allow_nan=False)

class DataRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prices={t:pd.read_csv(ROOT/f'data/{t.lower()}_price.csv',parse_dates=['date']).set_index('date') for t in ['QQQ','QLD','TQQQ','SPY']}

    def test_recent_transitions_and_current_state(self):
        m,_,state,events,_=simulate(self.prices,'2010-02-11','2026-10-05')
        recent=[(e['signal_date'],e['execution_date'],e['from'],e['to']) for e in events if '2025-03-06'<=e['signal_date']<='2026-05-15']
        self.assertEqual(recent,[('2025-03-06','2025-03-07','NORMAL','BEAR'),
            ('2025-06-24','2025-06-25','BEAR','BOOST'),('2025-09-23','2025-09-24','BOOST','NORMAL'),
            ('2025-10-27','2025-10-28','NORMAL','BOOST'),('2025-11-17','2025-11-18','BOOST','NORMAL'),
            ('2026-03-20','2026-03-23','NORMAL','BEAR'),('2026-04-14','2026-04-15','BEAR','NORMAL'),
            ('2026-05-05','2026-05-06','NORMAL','BOOST')])
        self.assertEqual(state.iloc[-1]['mode'],'NORMAL')
        self.assertEqual(m['orders'],1+2*len(events))

    def test_no_weekends_and_shared_calendar(self):
        dates=self.prices['TQQQ'].index
        self.assertTrue((dates.dayofweek<5).all())
        self.assertNotIn(pd.Timestamp('2016-02-27'),dates)
        for t in self.prices:self.assertTrue(dates.isin(self.prices[t].index).all())

    def test_live_collection_excludes_intraday_and_rejects_gaps(self):
        close=self.prices['QQQ']['close'].copy()
        # The mock provider even returns an intraday row despite the requested cutoff.
        close.loc[pd.Timestamp('2026-10-06')]=9999.
        provider=types.SimpleNamespace(download=lambda *a,**k:pd.DataFrame({'Close':close}))
        spec=importlib.util.spec_from_file_location('fetch_data_test',ROOT/'fetch_data.py')
        module=importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules',{'yfinance':provider}):spec.loader.exec_module(module)
        with patch('pandas.Timestamp.now',return_value=pd.Timestamp('2026-10-06T06:00:00Z')):
            state=module.get_q123_state()
            self.assertEqual(state['as_of'],'2026-10-05')
            self.assertEqual(state['mode'],'NORMAL')
            self.assertAlmostEqual(state['indicators']['close'],756.2)
            self.assertEqual(state['next_session'],'2026-10-06')
            close=close.drop(pd.Timestamp('2026-10-02'))
            with self.assertRaisesRegex(ValueError,'missing/duplicate'):
                module.get_q123_state()

    def test_costs_reduce_returns_and_report_hashes_match(self):
        import hashlib
        r=json.loads((ROOT/'docs/q123_results.json').read_text())
        values=[r['costs'][b]['cagr'] for b in ['0','2.5','5','10','15']]
        self.assertTrue(all(a>b for a,b in zip(values,values[1:])))
        for t,h in r['source_hashes'].items():
            self.assertEqual(hashlib.sha256((ROOT/f'data/{t.lower()}_price.csv').read_bytes()).hexdigest(),h)

if __name__=='__main__':unittest.main()
