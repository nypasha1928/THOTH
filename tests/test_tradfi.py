import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from pydantic import ValidationError
from thoth.models import Settings,TestnetSettings
from thoth.tradfi import TRADFI_SYMBOLS
from thoth.runner import Runner
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from thoth.verification import strategy_fingerprint
from test_testnet import Exchange
from test_order_block_sr import quiet,ob_rows,NOW


def hourly(data,now):
    start=int(now)//3600*3600
    return [{**r,'time':start-(len(data)-i-1)*3600} for i,r in enumerate(data)]


def hourly_analysis(symbol,cfg,history,now,**kwargs):
    assert cfg.timeframe=='60'
    return [dict(time=int(now)//3600*3600-3600,levels={'long_resistance_major':dict(value=101,pivot_time=0)})],len(history)-1


class TradFiTests(unittest.TestCase):
    def test_profile_and_independent_lists_preserve_existing_selection(self):
        old=Settings(symbols=['ETHUSDT','SOXLUSDT'])
        self.assertEqual(old.saved_symbol_lists['main'],['ETHUSDT','SOXLUSDT'])
        self.assertEqual(len(old.saved_symbol_lists['tradfi']),294)
        new=Settings(**{**old.model_dump(),'strategy':'order_block_sr_1h','timeframe':'D',
            'active_symbol_list':'tradfi','symbols':['SOXLUSDT'],'major':False,'minor':False,
            'daily_loss_pct':35,'coin_pnl_trailing_enabled':True,'weekly_equity_trailing_enabled':True})
        self.assertEqual(new.timeframe,'60');self.assertTrue(new.major and new.minor)
        self.assertFalse(new.sma_exit_enabled or new.sma_enabled or new.sma_7_50_filter_enabled)
        self.assertTrue(new.coin_pnl_trailing_enabled and new.weekly_equity_trailing_enabled)
        self.assertEqual(new.daily_loss_pct,35)
        self.assertEqual(new.saved_symbol_lists['main'],old.symbols)
        self.assertEqual(new.saved_symbol_lists['tradfi'],['SOXLUSDT'])
        self.assertNotEqual(strategy_fingerprint(new),strategy_fingerprint(old))
        self.assertEqual(Settings(strategy='order_block_sr_intrabar',timeframe='60').timeframe,'240')
        for lists in ({'bad':[]},{'tradfi':['SOXLUSDT','SOXLUSDT']},{'tradfi':['SOXLUSDT.P']}):
            with self.assertRaises(ValidationError):Settings(saved_symbol_lists=lists)

    def test_expanded_catalogue_forex_new_labels_and_permanent_exclusions(self):
        import json
        self.assertEqual(len(TRADFI_SYMBOLS),294)
        self.assertEqual(len(TRADFI_SYMBOLS),len(set(TRADFI_SYMBOLS)))
        self.assertTrue({'EURUSDUSDT','GBPUSDUSDT','USDJPYUSDT','SECZUSDT','POLYMARKETUSDT','MAGSUSDT','CVNAUSDT','VUSDT'}.issubset(TRADFI_SYMBOLS))
        self.assertFalse(any(s.endswith('NEW') for s in TRADFI_SYMBOLS))
        self.assertFalse({'EIDOUSDT','EQTUSDT','STMUSDT'}&set(TRADFI_SYMBOLS))
        catalogue=json.loads((Path(__file__).resolve().parents[1]/'watchlist.json').read_text())
        stocks={r['symbol'] for r in catalogue if r.get('asset_group')=='tradfi' and r['selectable']}
        self.assertEqual(stocks,set(TRADFI_SYMBOLS))
        cfg=TestnetSettings(strategy='order_block_sr_1h',active_symbol_list='tradfi',symbols=list(TRADFI_SYMBOLS))
        self.assertEqual(len(cfg.symbols),294);self.assertEqual(cfg.timeframe,'60')

    def test_curated_leader_lists_are_distinct_and_cover_tradfi_categories(self):
        import json
        from thoth.market_leaders import report
        data=report();catalogue=json.loads((Path(__file__).resolve().parents[1]/'watchlist.json').read_text())
        known={r['symbol'] for r in catalogue if r['selectable']}
        for group in ('main_leaders','tradfi_leaders'):
            self.assertEqual(len(data[group]),50);self.assertEqual(len(set(data[group])),50)
            self.assertTrue(set(data[group])<=known)
            self.assertFalse({'EIDOUSDT','EQTUSDT','STMUSDT'}&set(data[group]))
        self.assertEqual({key:len(value) for key,value in data['tradfi_groups'].items()},{'stocks':35,'etfs':8,'commodities':4,'forex':3})
        cfg=Settings(active_symbol_list='tradfi_leaders',symbols=data['tradfi_leaders'])
        self.assertEqual(cfg.saved_symbol_lists['tradfi_leaders'],data['tradfi_leaders'])
        self.assertEqual(len(cfg.saved_symbol_lists['tradfi']),294)

    def test_old_installation_migrates_custom_main_symbols_without_replacement(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            service=TestnetSupervisor(Path(tmp)/'live.sqlite')
            state=service.load();state['config']['symbols']=['AVAXUSDT','HBARUSDT']
            state['config'].pop('saved_symbol_lists');state['config'].pop('active_symbol_list')
            with service.db() as db:db.execute('UPDATE supervisor SET payload=? WHERE id=1',(json.dumps(state),))
            migrated=service.load()['config']
            self.assertEqual(migrated['symbols'],['AVAXUSDT','HBARUSDT'])
            self.assertEqual(migrated['saved_symbol_lists']['main'],migrated['symbols'])
            self.assertEqual(migrated['saved_symbol_lists']['tradfi'],TRADFI_SYMBOLS)

    def test_hourly_sr_volume_first_and_no_replay_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Settings(strategy='order_block_sr_1h',symbols=['SOXLUSDT'])
            runner=Runner(SimpleNamespace(path=str(Path(tmp)/'live.sqlite')));runner.analysis=hourly_analysis
            for cycle,price,passing,expected in [(1,100,False,None),(2,102,False,None),(3,102,True,'Long'),(4,102,True,None)]:
                now=NOW+cycle*5;runner.scan_cycle=cycle
                report=runner.combined_order_block_sr_observation('SOXLUSDT',cfg,hourly(quiet(now,price,passing),now),now)
                self.assertEqual(report['signal'],expected,report)
                self.assertEqual(set(report['volume_checks']),{'60'})
                if expected:self.assertEqual(report['entry_context']['order_block_bar'],int(now)//3600*3600)
            restarted=Runner(SimpleNamespace(path=str(Path(tmp)/'live.sqlite')));restarted.analysis=hourly_analysis;restarted.scan_cycle=1
            self.assertIsNone(restarted.combined_order_block_sr_observation('SOXLUSDT',cfg,hourly(quiet(NOW+30,102),NOW+30),NOW+30)['signal'])

    def test_worker_uses_hourly_data_for_both_branches_and_final_volume_check(self):
        for branch in ('ob','sr'):
            for final_pass in (True,False):
                with self.subTest(branch=branch,final_pass=final_pass),tempfile.TemporaryDirectory() as tmp:
                    now=[NOW];trigger=[False];calls=[];exchange=Exchange(lambda:now[0]);exchange.prices['SOXLUSDT']=100.
                    service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
                    cfg=TestnetSettings(strategy='order_block_sr_1h',symbols=['SOXLUSDT'],active_symbol_list='tradfi',dedicated_account=True,daily_loss_pct=90)
                    service.configure(cfg);service.activate(service.confirmation);feed=FeedAdapter(service)
                    def fetch(symbol,interval,limit=1000):
                        calls.append((interval,limit));data=ob_rows(now[0],trigger[0]) if branch=='ob' else quiet(now[0],102 if trigger[0] else 100)
                        if limit==20 and not final_pass:data[-1]['volume']=1
                        return hourly(data,now[0])[-limit:]
                    runner=Runner(feed,fetch=feed.fetch)
                    if branch=='sr':runner.analysis=hourly_analysis
                    with patch.object(exchange,'candles',side_effect=fetch),patch('thoth.runner.time.time',side_effect=lambda:now[0]),patch('thoth.testnet.observe_exit',side_effect=AssertionError('No SMA exit')):
                        runner.cycle(paced=False);self.assertFalse(exchange.orders)
                        now[0]+=5;trigger[0]=True;runner.cycle(paced=False)
                    self.assertTrue(all(interval=='60' for interval,_ in calls),calls)
                    self.assertIn(('60',20),calls,(runner.health,service.state['events'][-4:]))
                    self.assertEqual(bool(exchange.orders),final_pass)
                    self.assertEqual(service.load()['config']['saved_symbol_lists']['tradfi'],['SOXLUSDT'])
                    if final_pass:
                        order=next(iter(exchange.orders.values()));exchange.fill(order['orderLinkId']);service.monitor()
                        owner=service.state['owned']['SOXLUSDT'];self.assertEqual(owner['strategy'],'order_block_sr_1h')
                        self.assertEqual(owner['entry_timeframe'],'60');self.assertEqual(owner['exit_timeframe'],'none')

    def test_permanent_exclusions_clean_saved_state_and_do_not_delete_ownership(self):
        import json
        from thoth.tradfi import EXCLUDED_SYMBOLS
        with tempfile.TemporaryDirectory() as tmp:
            service=TestnetSupervisor(Path(tmp)/'live.sqlite')
            state=service.load();state['config']['symbols']=['SOXLUSDT',*sorted(EXCLUDED_SYMBOLS)]
            state['config']['saved_symbol_lists']={'main':['BTCUSDT','EIDOUSDT'],'tradfi':list(state['config']['symbols'])}
            for name in ('pending_signals','reversals','entry_gates'):state[name]={'EIDOUSDT':{'fixture':True}}
            state['owned']={'EIDOUSDT':{'fixture':'preserve managed position'}}
            with service.db() as db:db.execute('UPDATE supervisor SET payload=? WHERE id=1',(json.dumps(state),))
            cleaned=service.load();self.assertEqual(cleaned['config']['symbols'],['SOXLUSDT'])
            self.assertEqual(cleaned['config']['saved_symbol_lists']['main'],['BTCUSDT'])
            self.assertEqual(cleaned['config']['saved_symbol_lists']['tradfi'],['SOXLUSDT'])
            self.assertIn('EIDOUSDT',cleaned['owned'])
            for name in ('pending_signals','reversals','entry_gates'):self.assertEqual(cleaned[name],{})
            cfg=TestnetSettings(**state['config']);self.assertEqual(cfg.symbols,['SOXLUSDT'])
            service.state=cleaned;service.save()
            with service.db() as db:persisted=json.loads(db.execute('SELECT payload FROM supervisor WHERE id=1').fetchone()[0])
            self.assertEqual(persisted['config']['symbols'],['SOXLUSDT'])
            # Even a stale caller cannot place an entry for a removed symbol.
            service._entry('EIDOUSDT','Long',NOW,'fixture',cfg)
            self.assertIsNone(service.client)
