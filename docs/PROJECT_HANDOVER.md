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

## Normal-direction closed-hour OB + S/R (2026-10-29 New York)

New separate `order_block_sr_1h_close`, display **Order Block + S/R · 1h · Bar Close**, explicitly requested without Volume MA. Uses the shared completed-hour observer/evaluator with normal direction: bullish OB formation/supply breakout or S/R resistance crossing opens Long; bearish formation/demand breakdown or support crossing opens Short. Independent branches, matching sources share one order, conflicting sources skip. Both Major/Minor enabled. No volume, SMA, VWAP, RSI or strength entry filter. Raw uploaded source and existing intrabar Volume MA/inverted closed strategies unchanged.

Shared closed engine now selects inversion only for `order_block_sr_1h_inverted_close`; new profile keeps source direction. Separate strategy fingerprints/cursors, fixed 60-minute source candles, one observation per completed candle, startup/restart/gap baselines, 30s execution queue, final latest-closed-bar guard, incremental hourly history and no volume recheck. No forming-candle entry. Actual-side stops/TP/trailing/selected opposite-signal action and all account/period protections remain; no additional SMA/OB exit. Chart timeframe is display-only. Settings/History/Analysis/activation/status distinguish normal from inverted. No list, leverage or dollar-risk changes or automatic strategy activation. Changing profile retains flat/reconciled requirement.

Validation: 480 unit/API tests pass, including fixed 1h/unfiltered normalization, all four OB kinds and Major/Minor S/R preserving direction, inverted-mode regression checks, zero-volume qualifying signals, conflicts, simulated no-entry-before-close, both actual-side fills/protection/history, one trade per completed bar and no volume-recheck calls, chart-view independence and unchanged saved risk/list settings. Browser validation recorded after completion below. No live orders or server changes performed.

Desktop/mobile synthetic smoke passed normal Long/Short description, fixed-hour setting, no entry filters or SMA exit, save/reload, preserved risk and activation label; no JS errors/overflow or activation requests. JS syntax and diff checks pass.

## Closed-hour long-scan continuity correction (2026-10-29)

User runner report: status OK for all 220 saved symbols, 430.9039s full scan, all recovery baselines under normal closed-hour strategy. Initial baselining can be legitimate, but the shared closed observer's inherited 300s intrabar freshness cap would repeatedly invalidate consecutive scans at this duration and rebuild closed analysis rather than reuse cache. Dashboard OK is source-data health, not proof an entry-qualified transition will pass continuity.

Only the normal/inverted CLOSED-hour observer now allows consecutive same-session/cycle observations up to one hour apart. The candle itself is completed/immutable. Adjacent completed-candle identity remains mandatory for emitting: startup/restart, skipped scan cycles, >1h interruption and missed-hour catch-up baseline rather than replay; exact same closed candle never emits twice. Final latest-closed-hour context and 30s observed-signal queue expiry remain. Intrabar observation caps unchanged. This does not guarantee prompt fills or optimize scans to a fixed duration. Smaller saved lists still reduce work; no selections/risk/strategy changed automatically.

Continuous cached same-bar status drops stale startup-baseline/source-entry wording. `/api/runner` closed-profile per-symbol reports now include `sampling.continuous`, `gap`, `limit`, and `closed_bar`. Tests reproduce 431s consecutive scans crossing the close in both directions, single emission, cached same-bar reuse, restart/missed-cycle/hour-gap suppression. Validation: 483 unit/API tests passed; normal worker integration checks reported continuity; diff/compile checks pass. No frontend change, live orders or deployment.

### 2026-10-29 — CLOSED 1h permission → intrabar 1m OB/SR entry and close-only exit

- New independent strategy ID `ob_sr_hour_gate`, UI **Order Block + S/R · 1h Close → 1m Entry / Exit**. Existing normal/inverted closed-hour profiles remain available and unchanged. Selecting a watchlist is separate from selecting a strategy; **Main · Market Leaders 50** works with this profile, and loading/saving it preserves the original Main and full TradFi buckets. No automatic list, live setting, leverage or activation changes.
- First stage uses the latest CLOSED 1h Order Block or Major/Minor S/R event to arm that coin, with no order and no direction restriction. Both higher families can supply permission. Startup/restart/missed-hour baselines do not replay earlier signals; the existing completed-hour continuity correction still applies. Signals during entry pauses/blocks are consumed without arming.
- Independent confirmation worker checks armed coins (and owned coins even when entries/gates are paused/locked). It bootstraps 1m history, then reuses bounded overlap/checkpoints. Raw uploaded OB zone/event formulas are unchanged: bullish formation or supply break → Long; bearish formation or demand break → Short. Major/Minor S/R is independent. Both entry and opposite-signal exit use fresh forming-minute observations, without waiting for close. NO Volume MA, SMA 200/7/50, RSI, VWAP or additional strength entry filters. Conflicting fresh minute signals skip rather than choose a side.
- Hour permission alone never queues an order. The first minute check/new arm epoch establishes a baseline; pre-existing events are not replayed. A subsequent NEW minute event chooses either direction. Same-direction signals do nothing while a position is open; opposite minute signals CLOSE ONLY and never queue a reversal. Minute entries retain the 30s execution expiry and must still belong to the current minute at the final entry check.
- Reuses the earlier staged permission lifecycle: permission persists across closes; positive settled P&L resets gate losses; configured consecutive settled losses lock that coin for the configured cooldown, then require a NEW closed-hour permission. Settings enforce per-coin loss scope and close-only opposite action. Hour observations continue during cooldown so new post-cooldown closes can be observed continuously. Existing stops, enabled TP/trailing and daily/3-day/weekly/overall risk controls remain. No SMA exit is added.
- History/alerts retain `htf:60:` permission provenance and `htf:1:` actual trigger provenance; entry/exit timeframe metadata is `1`. Chart timeframe remains display-only; strategy descriptions and two-stage permission status explain both stages. Runner reports hourly sampling plus separate minute-worker timings/ages.
- Smaller watchlists reduce work; no fixed fill latency or profitability guarantee. Compare actual hourly `scan_seconds`, confirmation `max_check_age_seconds`/round estimate, and recorded entry diagnostics after deploying and selecting a list. No production access or orders were used in validation.
- Validation: 491 unit/API tests passed (`/tmp/hour-gate-tests.log`); full synthetic desktop/mobile browser smoke passed (`/tmp/hour-gate-browser.log`), including strategy save/reload, disabled close-only direction control, no-filter settings, permission display, unchanged risk fields, and activation label without activating orders. Added actual runner/supervisor simulation of hour-close arming, opposite-to-hour minute entry, verified protective stop, opposite minute close without reversal, paused/locked-gate exit, all four live OB types, Major/Minor minute S/R both directions, restart/dedup, new-arm baseline, loss-lock/rearm, and API Main-50 selection preserving other lists. Uploaded Pine bytes remain unchanged.

### 2026-10-29 — direct intrabar minute OB/SR with hourly entry limits and a timed window

- New ID `ob_sr_minute_hourly`, UI **Order Block + S/R · 1m · Once per Hour**. Independent from the earlier closed-hour permission → minute strategy: no 1h permission and no staged gate. All four uploaded Order Block event families OR Major/Minor S/R independently supply normal-direction entries on the FORMING minute. No Volume MA, SMA, RSI, VWAP or additional strength filter. Existing zone formulas and Pine files are unchanged.
- Final clarified rules: each coin may enter at most ONCE per fixed UTC hour; saved **Maximum Positions** caps BOTH concurrent managed positions and TOTAL new entries that hour. Thus a saved limit of 10 permits up to 10 eligible entries on different coins, not necessarily simultaneous. A close does not refund either hourly allowance. Counts reset at the next UTC hour; a NEW minute signal is required, with no catch-up/automatic hourly order. No signal/risk permission means no guaranteed hourly entry.
- New Settings select `minute_entry_window_minutes`: exactly 5, 10 or 15; default **5** per user. New submissions are allowed only in `[hour_start, hour_start + selected_minutes)`. Source trackers continue advancing outside the window without dispatching unowned-coin entry alerts/queues. Final execution checks revalidate the window, quota, and fresh current-minute signal before reserving/submitting; old queues cannot become new-hour entries. Remaining minutes still monitor exits and all account protections.
- Opposite fresh intrabar 1m OB/SR closes actual Long/Short positions ONLY, without reversal or a candle-close wait. Same-direction signals do nothing while open. Exit handling runs before hourly entry restrictions, including while entries are paused and after the window/allowance is exhausted. No separate SMA exit. Stops, enabled TP/trailing, daily/3-day/weekly equity controls, loss scope/cooldowns, leverage and all watchlists remain as saved. Maximum Positions is not automatically changed to 10; it uses the operator's actual saved value.
- Per-coin reservations persist in supervisor state and each journal entry carries its UTC window. Journal recovery counts closed/accepted/uncertain intents too, so early closure, settings/list changes or restart cannot refund a consumed allowance. A definite exchange submission rejection releases its reservation; an uncertain submission retains it pending reconciliation. Existing execution serialization prevents simultaneous signals from overbooking. Entry checks still verify exchange size/leverage/protection rules.
- UI shows the per-hour count/limit and whether the entry window is closed or allowance exhausted. Window selection, strategy label, History/Analysis, chart references, and live activation label are registered; chart timeframe remains view-only. Main · Market Leaders 50 remains available without replacing the full saved lists. Execution remains sampled, not tick-by-tick; no fixed latency promise or production activation was made.
- Validation: **500 unit/API tests passed** (`/tmp/minute-hour-tests.log`), including real worker/supervisor simulated minute entry, protected fill and opposite exit outside the entry window; per-coin and total hourly limits across early closes, journal recovery and restart; new-hour fresh entries; definite rejection versus uncertain submission; all four raw OB types with zero volume; 5/10/15 window boundaries; stale checks crossing the cutoff; API preservation of risk/list settings and chart timeframe independence. Full desktop/mobile browser smoke passed (`/tmp/minute-hour-browser.log`): default 5-minute select, changing/saving/reloading 10 minutes, forced close-only/minute timeframe, total hourly count status, risk preservation, and activation label without enabling orders. Python compilation, JS syntax and diff checks passed; generated fixture screenshots were restored.

### 2026-10-29 — PAXG two-stage re-entry correction and CLOSED-minute exits

- Applies specifically to ob_sr_hour_gate (**Order Block + S/R · 1h Close → 1m Entry / Exit**). User activity proved one 08:00 hourly arm supplied entries at 08:03 and 08:13 after an 08:12 close; the latter loss triggered the saved two-loss cooldown. Root cause was persistent arming after closure. This profile now uses SINGLE-USE hourly permission: reserve/consume before submitting its first entry; any stop, trailing, opposite-signal, winning or losing close keeps that permission consumed. Another entry requires a NEW signal from a LATER CLOSED hourly candle, not merely a clock rollover or another minute signal.
- Arm ID/time and permission closed bar are journalled; consumed state and last-used bar persist. Entry dispatch AND final execution enforce consumption. Definite submission rejection can restore the unused arm; an uncertain outcome retains consumption. Older installed armed gates are checked against existing strategy/symbol journal submissions (including legacy timing/permission metadata), so a restart cannot reuse a permission that already placed/closed a trade.
- Consecutive settled losses still accumulate ACROSS distinct hourly permissions. Winning closure resets loss count but never refunds the arm. The existing configured loss limit (user currently 2), cooldown (60m), stops, targets, trailing and daily/period/account controls are unchanged. Older Volume Delta and S/R→5m OB gate lifecycle, direct minute/hourly-window strategy and other profiles are unchanged.
- Entry stays intrabar on a NEW 1m OB or S/R signal in either direction. Opposite-signal EXIT now requires the latest COMPLETED 1m candle: unfiltered OB formation/supply break/demand break OR closed Major/Minor S/R. A forming-minute opposite signal cannot exit. Only a closed candle ending AFTER the recorded entry can close; exit is close-only, with no reverse order. Paused entries/consumed gates still allow closed-minute exits. Signal alerts and activity explicitly say CLOSED 1m for these exits.
- Closed exit detector uses separate durable rollback-safe OB checkpoints and existing fetched minute rows (no additional market requests), with confirmed S/R analysis. It ignores the forming row, emits once per adjacent completed minute, baselines on startup/restart/gaps and reports source errors. No SMA or volume filter is introduced. Existing positions of this strategy adopt the closed-minute exit behavior on deployment.
- Alert label bug: browser formatter previously labelled every timeframe other than 240 as Daily, mislabelling BOTH htf:60: and htf:1: sources. Correct mapping now distinguishes 1-minute, 5-minute, 1-hour, 4-hour and Daily. The supplied “Daily S/R” toast was consistent with that display bug; actual arming/fetches use closed 60-minute sources and minute confirmations. Existing alert records render correctly without rewriting their provenance/history. New arm messages explicitly say CLOSED 1h and the permission panel displays USED.
- Validation: **509 unit/API tests passed** (/tmp/hour-once-full-tests.log); full desktop/mobile synthetic browser smoke passed (/tmp/hour-once-browser.log), including exact source labels for 1m/1h/4h/Daily, consumed-permission display and closed-minute exit description. New tests reproduce PAXG re-entry after early close, reject rearming from the same closed bar or mere hour rollover, permit a genuinely later closed-hour signal, preserve losses across distinct arms, block reuse after winning trailing close, migrate/restart old armed journal state, distinguish definite rejection from uncertain submission, enforce no forming/stale/pre-entry closed exit, confirm all four raw OB types only after minute close, confirm closed S/R both sides, and avoid repeated/restart/missed-minute replay. Existing direct-minute strategy regressions remain passing. Pine source files unchanged; generated browser screenshots restored before commit. No production connection, order, activation or risk-setting changes were made.

### 2026-10-29 — native Pine Bounce/Rejection ONLY for the two-stage minute S/R branch

- User explicitly scoped this change to the 1m ENTRY / EXIT of ob_sr_hour_gate. CLOSED 1h permission remains the existing Order Block OR S/R BREAKOUT logic. All four Order Block events are unchanged at both stages. Other strategies, including the direct 1m/hour-window profile, retain their previous S/R logic.
- Located the original rules in reference/support_resistance.pine, lines 629–637 and 656–664. Ported their exact predicates to thoth/sr_bounce.py: support low <= level AND close > level AND close > previous BAR close; resistance high >= level AND close < level AND close < previous BAR close. There is no open-side/candle-color rule or distance/percentage threshold. Uses the existing enabled Major/Minor and long-/short-term families; no volume/SMA/RSI/VWAP filter added. Original Pine bytes untouched.
- Minute entries still evaluate the forming candle with close[1] from the preceding completed minute, retaining fresh-sample/known-pivot/startup-gap baselines and one source decision per minute. Minute S/R breakouts no longer enter this profile. Closed-minute exits use the SAME native predicates on the completed row, its preceding row's close, and that row's confirmed current S/R levels; raw breakout reports from analysis no longer drive exits. OB and S/R remain independent with existing conflict handling.
- Single-use hourly permission, later-closed-hour rearming, closed-minute opposite close-only exits, protective stops, trailing, loss cooldowns and all account risk settings remain intact. No model setting or default risk change. Source fingerprint includes the new module and the minute S/R tracker has a distinct key so old breakout observations cannot replay as new bounce events.
- New source provenance sr_bounce:/sr_rejection: renders explicitly as S/R Bounce / S/R Rejection in backend History/Analysis and browser alerts, including 1-minute prefixes. This profile's minute chart S/R references now display native bounce/rejection conditions (closed or explicitly provisional), while hourly and other chart timeframes keep their prior reference behavior. Display replay remains independent of live permission/orders.
- Validation: **517 unit/API tests passed** (/tmp/minute-bounce-full-tests.log), plus full desktop/mobile browser smoke (/tmp/minute-bounce-browser.log). Tests cover all eight level families, inclusive touches and strict level/previous-close comparisons, no invented open requirement, no breakout-only trigger, intrabar baseline/dedup/pivot recovery, native closed exits, scope isolation, genuine hourly breakout permission, protected short entry from minute resistance rejection and closed-minute support-bounce exit, retained one-use/cooldown controls, accurate chart/source labels and unchanged settings. Python/JS syntax and diff checks passed. Generated fixture images restored. No production access, orders, activation or live settings changes.

## 2026-10-29 — Major-only S/R signals across every strategy

Operator requested exclusion of both long-term and short-term Minor S/R signals, especially the standalone Order Block + S/R · 1h · Bar Close profile. Minor support/resistance no longer qualifies an entry, opposite-signal exit/reversal, or higher-timeframe permission. Major support/resistance from both pivot periods remains available. Order Block formations/breaks, RSI/SMA/Volume Delta logic, timeframe/close confirmation, opposite-action choice and all money/risk controls retain their existing behavior.

Settings defaults and reload/save normalization force minor=false, including profiles that formerly forced both families on. Legacy minor-only selections migrate to major selection so loading does not fail. Shared batch/stream breakout, sampled intrabar breakout, native minute bounce/rejection and persisted volume qualification also reject Minor independently of the settings flag; model_copy/legacy setups cannot re-enable them. Native pivot construction and promotion are preserved, as Major classification relies on that state. Runner source copies and chart references use Major only. UI keeps Minor unchecked/disabled across strategy changes. Existing History and stored source labels are not rewritten.

Source fingerprints invalidate previous calculation caches; deployment retains normal manual entry activation after restart. This change does not remove historical losses or per-coin blocks, change protection on existing positions, or submit orders. Unit/API regressions cover legacy settings for all profiles, all four excluded minor level types, retained major counterparts, volume setup recovery and historical source labels. Existing simulated integration fixtures now use retained major levels where they test entry/exit mechanics.

Validation: 520 unit/API tests passed (/tmp/major-only-final-tests.log), desktop/mobile browser smoke passed (/tmp/major-only-browser3.log), JavaScript syntax and git diff checks passed. No production access or live orders.

## 2026-10-30 — Closed-hour candle stretch and execution distance (observations only)

For the standalone normal `order_block_sr_1h_close` profile, record a signal-candle snapshot when a valid signal is confirmed. No new entry/exit filters, rejection rules, delays, strategy/timeframe/risk changes, or extra exchange requests. The two-stage/minute and inverted profiles are unchanged. Optional diagnostic calculation failures yield unavailable measurements rather than blocking signals or ownership recovery.

Measurements: signal high-low range divided by Wilder ATR14 (including the signal candle; first TR is high-low, needs 14 consecutive hourly bars); upper wick for Long/lower wick for Short as percent of candle range; seconds from hour close to qualification; each contributing S/R level or OB boundary's distance at signal close and actual entry fill. Record every contributing reference separately: S/R current confirmed level; broken supply high; broken demand low; formation demand upper/supply lower edge explicitly marked formed-block edge, not a broken level. Multiple broken zones remain separate references. Directional reference distance positive means beyond the boundary in the signal direction; a wick break closing back inside can be negative. No single source/nearest boundary is guessed from a mixed signal.

Persist the snapshot through entry context, exact execution-link metadata, owned-position recovery and retained closed-trade history. Fill-to-signal-close percent positive means worse price for the actual Long/Short; negative means better. Close-to-fill-observed time uses the first saved fill observation, with fill confirmation as fallback; it is not claimed as the exchange's exact fill timestamp. Signal Alerts join only the exact execution link and update fill measurements, including watched floating alerts. History/Copy all trades include measurements; missing historical data stays Not recorded, with no candle reconstruction or fabricated zeros. Expandable per-row details keep multiple-source measurements readable. Measurements do not predict exhaustion and do not change order decisions.

Validation: 523 unit/API tests passed (/tmp/stretch-final-tests.log), including normal closed-hour simulated entries/fills/settlements and alert/history persistence; full desktop/mobile browser smoke passed (/tmp/stretch-browser.log), including expanded multi-source distances, floating summaries and History copy export. JavaScript syntax and git diff checks passed. No production deployment, account changes, activation or live orders.

## 2026-10-30 — OB strength/ATR History comparison only

Operator requested a History column to inspect whether a strength/ATR rule would pass earlier trades. They delegated the trial cutoff choice. Use a provisional minimum **0.25 ATR14**, editable on History; this is an illustrative starting benchmark, not calibrated from the observed winners/losses. No live filter, settings write, entry/exit change or replayed orders. Existing strategy and risk settings remain unchanged.

Extend standalone normal closed-hour signal snapshots (version 2) with full high/low, unrounded block width, width/ATR14 and basis for each contributing OB reference. Formation events use the newly formed block; supply/demand breaks use each actual broken zone's boundaries as they existed at the signal, not a guessed formation width. A broken/overlapped zone can have different width than at original formation. ATR14 is the existing signal-candle Wilder ATR, not ATR reconstructed at zone origin. Major S/R references do not receive fake OB strength.

History adds `OB strength / ATR · trial check`, and Copy all trades exports the selected trial minimum, measurements and per-reference PASS/BELOW MINIMUM results. Blank/invalid minimum gives no verdict; equality passes; comparisons use full stored precision. Input is page-local, causes no API settings request and never changes live execution. Multiple blocks get separate checks; no invented overall take/skip verdict. Mixed Major S/R + OB entries explicitly note that an OB failure alone does not eliminate an independently qualifying S/R entry. This is not a causal replay of the original Pine strength filter, which changes the zone queue and later break opportunities.

Historical limitation: earlier snapshot versions/export files saved only one edge per block, not both boundaries. Original width cannot be recovered reliably from those records, so they show `Not recorded`, not zero or a guessed pass/fail. This update does not claim to backfill all retained historical trades. New complete snapshots persist through journal, ownership recovery, Alerts and closed History using the existing pipeline, with no extra market requests. Tiny-priced coin widths retain precision; cross-coin ratios are invariant under currency rescaling. No Pine script or underlying Order Block signal logic is modified.

Validation: 525 unit/API tests passed (/tmp/strength-final-tests.log), full desktop/mobile browser smoke passed (/tmp/strength-browser.log), including editable 0.25 trial comparison, equality-pass/above-minimum failure, unknown historical boundaries, mixed-source caveat, clipboard export and no settings writes from the trial input. JavaScript syntax and diff checks passed. Operator reiterated History-only scope; no live filter or production action.
