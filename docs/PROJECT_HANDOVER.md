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
        self.assertEqual(len(old.saved_symbol_lists['tradfi']),43)
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

Validation: 461 unit/API tests pass, including stale saved selections, both named lists, pending-intent cleanup, ownership preservation, persisted cleaned state and stale-call entry rejection. Diff check passes. No VPS or live exchange mutations performed by Codex.

## Expanded TradFi catalogue with forex (2026-10-28)

Operator supplied 282 normalized unique symbols across the larger paste and explicitly authorized inclusion of EURUSDUSDT, GBPUSDUSDT and USDJPYUSDT. Combined with the earlier list this produces 294 TradFi symbols, 251 additions to the prior 43. Display-only NEW badges stripped; duplicates not added; all USDT suffixes retained. EIDOUSDT/EQTUSDT/STMUSDT remain permanently excluded from catalogue, persisted selections, scans and entry submission. This branch also includes the previously supplied permanent-cleanup fix if not merged separately.

Catalogue membership does not verify exchange tradability/account permissions or liquidity. Same hourly OB OR Major/Minor S/R with mandatory volume gate, stops and risk controls; no automatic leverage changes, orders or activation. Existing saved TradFi subsets do not silently expand: additions become available in the checklist; operator chooses new symbols and saves. Fresh/default lists start at 294. Main catalogue view now separates other TradFi symbols while preserving previously saved Main names; Select all is scoped to active list, avoiding a 491-selectable-symbol all-catalogue request over the unchanged 300-symbol limit. Guard explains oversized drafts without changing the saved selection. More selected symbols can lengthen scanning; expansion is not a latency improvement.

Validation: 462 unit/API tests passed, including all three forex pairs, exact catalogue/default agreement, normalized unique IDs, excluded names absent, full 294-symbol config acceptance and group-scoped API saves. Desktop/mobile synthetic smoke passed group-specific Select all, 294 selections, persistence, Main restoration and no activation/order requests. JS syntax/diff checks passed. No production deployment or real exchange requests.

## Separate curated Market Leaders 50 selections (2026-10-28)

Operator requested Top 50 for BOTH existing Main and TradFi, then clarified underlying-market leaders by volume and market cap rather than Bybit turnover alone. Current stock/crypto market-cap feeds are not accessible in this environment; do NOT describe the implemented lists as verified real-time top-cap or liquidity rankings. No invented cap values, fetched quotes or composite scores. `thoth/market_leaders.py` explicitly defines curated starter selections only, available through authenticated read-only `/api/live/market-leaders`. Main starter contains 50 supported instruments; TradFi starter contains 35 prominent public-company contracts, 8 unleveraged/broad/thematic ETF contracts, 4 commodity contracts (XAU, XAG, CL, BZ) and all 3 requested forex contracts. Company market cap is not applied to commodities/forex or confused with ETF AUM. Selection is not a verified current-cap ordering, portfolio allocation, performance guarantee or automatic strategy filter.

Four independent persisted choices: Main, full TradFi (294), Main Market Leaders 50, TradFi Market Leaders 50. Migration/model defaults add missing leader slots while preserving existing active selections, saved full lists and intentionally edited leader subsets. Switching loads a draft; **Load starter 50 into draft** restores that group's curated starter without saving or activating anything. Existing pause/removal/strategy-change guards and risk controls remain. Strategy and leverage are NOT changed by list loading. Active entries lock list/restoration controls. No ranking network requests or automatic rebalancing in scans. Permanent excluded three are filtered from all four groups.

Validation: 464 unit/API tests pass, including exact category counts, 50 unique supported symbols in each starter, excluded symbols absent, scope persistence, independent original/full-list preservation, read-only endpoint and authentication. Desktop/mobile synthetic smoke passes draft-only restore, both leaders, saved reload and Main/full-TradFi restoration without activation requests. JS/diff checks pass. No real exchange orders or VPS deployment.

## Bounded read-only Bybit 10016 retries (2026-10-28)

Operator reports wallet-balance 10016 safety lock and repeated skipped signals over ~4:47–6:44 PM NY. These skipped rows repeat the latched failure reason; they are NOT evidence of a new balance failure per signal or a continuous Bybit outage. Official Bybit error list defines 10016 as Server error (https://github.com/bybit-exchange/docs/blob/master/docs/v5/error.mdx).

Shared authenticated client now treats explicit 10016 on GET like a bounded transient read rejection: at most 3 total attempts, local 0.25s then 0.5s waits, each request rebuilt/signed. Existing common 3-attempt budget remains shared with rate/timestamp cases. POST submissions, stop updates and other writes still execute once; transport failures and unrelated codes unchanged. No new global pacer backoff, API polls, strategy/time-window/selection/risk/leverage changes or retries of ambiguous writes.

If all attempts fail, existing monitoring exception/safety lock remains. Recovering reads after a lock DO NOT automatically restore entries. Deploy retains normal restart pause and manual readiness/activation requirement; previously skipped signals do not replay. Brief in-request backoff can add 0.75s plus request latency to the failed read path. This mitigates transient failures, not a claim Bybit availability is fixed.

Validation: 468 unit/API tests pass, including newly signed wallet retry, bounded exhaustion, no POST/stop/transport/auth retries, recovered full monitor pass retaining existing entry permission and protection, exhausted reads locking entries and later healthy monitoring preserving manual-activation requirement. Diff check passes; no frontend changed, no live account/server operations performed.

## Closed-hour inverted OB + S/R, NO entry filters (2026-10-28 New York)

New separate `order_block_sr_1h_inverted_close`, display **Order Block + S/R · 1h · Inverted · Bar Close**. Operator initially requested volume, then explicitly removed ALL entry filters. Existing intrabar 1h/4h profiles retain their volume rules.

- Evaluate only the latest completed UTC-aligned hourly candle: raw uploaded OB formations/supply/demand wick breaks OR closed-candle Major/Minor S/R cross, independently. Bullish original opens actual Short; bearish original opens actual Long. Agreement gives one entry with original provenance, component/opposing conflicts skip. No volume/SMA/VWAP/RSI/strength entry filter. Raw Pine source untouched. No added SMA/OB exit; selected opposite-signal action retained. Dollar stops, targets, trailing and all account/day/period protections use actual position direction and remain unchanged.
- Runner persists a separate closed-candle observation cursor, baselines startup/restart/source gaps, consumes once per completed candle, and does not evaluate a forming candle as an entry. Continued observations during pauses prevent replay on resume. Freshness and adjacent-candle checks prevent catch-up trades from missed hours. Distinct strategy fingerprint and checkpoints keep existing modes isolated. Full OB replay occurs only on new closed bars; S/R uses incremental causal analysis. Bootstrap hourly history then short overlap refreshes; no volume recheck request for this profile.
- Each emitted signal is observed at the next successful scan after close; exact close execution is NOT promised. Queue/reversal expire after 30 seconds from observation as with short-lived signals. Final entry guard checks the latest CLOSED-hour context; stale/future/rolled-over context cannot submit. Diagnostic `signal_bar_closed_at` / `bar_close_to_qualification_seconds` distinguish close-to-detection delay; price basis explicitly closed candle close.
- Settings lock source timeframe to 1h, show inversion and no filters, preserve risk/leverage/lists, and retain flat/reconciled strategy-switch requirement. Chart timeframe remains display-only; raw arrows/boxes show source direction while History/alerts actual side is inverted. Profile supported in analysis, execution metadata and operator confirmation label. Existing-position management never silently changes profile or risk.

Validation: 476 unit/API tests pass. New tests cover both actual sides, all four OB events, Major/Minor S/R directions, zero-volume entry, conflicts, no forming-candle entry, no repeat/restart replay, stale closed context, actual-side filled protection, hourly-only incremental reads with no volume recheck, chart-view independence and unchanged risk. Full desktop/mobile synthetic smoke passed save/reload, inverted description, fixed 1h, no filters/SMA exit, unchanged risk and activation label; JS syntax/diff checks passed. No live orders, production deployment or entry activation performed.
