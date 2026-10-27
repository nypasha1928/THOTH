import json
import os
from pathlib import Path

# This single-purpose server must create private state, journals and receipts.
os.umask(0o077)
from contextlib import asynccontextmanager

from .runner import Runner
from .auth import guard, PUBLIC_HOST, PASSWORD
from .connection import Connection
from .production import ProductionSupervisor,ProductionClient
from .testnet_feed import FeedAdapter
from .verification import reference_status
from .indicator import evaluate
from urllib.error import URLError

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .market import candles, sma, quote, chart_history
from .models import Settings, TestnetSettings, TestnetActivation
from .timeframes import INTERVALS
from .trailing_observation import TrailingObservation,ObservationSettings
from .push_notifications import PushNotifications

ROOT = Path(__file__).resolve().parent.parent
live_feed = None
live_connection = Connection(ProductionClient,environment='live')


@asynccontextmanager
async def lifespan(app):
    global live_feed
    phone_alerts.start()
    trailing_observation.start()
    live.trailing_observer=trailing_observation
    live.start()
    adapter=FeedAdapter(live)
    live_feed=Runner(adapter,fetch=adapter.fetch)
    live_feed.start()
    try:
        yield
    finally:
        live_feed.stop()
        live.stop()
        trailing_observation.stop()
        phone_alerts.stop()


app = FastAPI(title='THOTH live trading desk', version='0.4.0', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', '[::1]', 'testserver'] + ([PUBLIC_HOST] if PUBLIC_HOST else []))
# Preserve the existing live.sqlite location and journals on upgrades.
# THOTH_DB remains a compatibility locator; no simulation database is opened.
DATA = Path(os.environ.get('THOTH_DB',str(ROOT/'data'/'live.sqlite'))).parent
live = ProductionSupervisor(DATA/'live.sqlite',enabled=os.environ.get('THOTH_ENABLE_LIVE')=='1',password_configured=bool(PASSWORD),allow_unverified=os.environ.get('THOTH_ALLOW_UNVERIFIED_LIVE')=='1')
trailing_observation=TrailingObservation(DATA/'trailing-observation.sqlite',DATA/'live.sqlite')
phone_alerts=PushNotifications(DATA/'phone-alerts.sqlite',DATA/'live.sqlite',
    'https://'+PUBLIC_HOST if PUBLIC_HOST else 'mailto:operator@localhost')


@app.middleware('http')
async def same_origin(request: Request, call_next):
    allowed={'localhost','127.0.0.1','::1','testserver'}|({PUBLIC_HOST} if PUBLIC_HOST else set())
    if request.url.hostname not in allowed:
        from starlette.responses import JSONResponse
        return JSONResponse({'detail':'Invalid host'},status_code=400)
    # Reject browser cross-origin mutations; no CORS enabled.
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            from starlette.responses import JSONResponse
            return JSONResponse({'detail': 'Cross-origin request rejected'}, status_code=403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            from starlette.responses import JSONResponse
            return JSONResponse({'detail': 'Cross-site request rejected'}, status_code=403)
    response = await guard(request)
    if response is None:
        response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
    return response


@app.exception_handler(ValueError)
async def invalid(request, exc):
    from starlette.responses import JSONResponse
    return JSONResponse({'detail': str(exc)}, status_code=409)


@app.get('/healthz')
def health():
    return {'status':'ok','version':'0.4.0'}


@app.get('/api/phone-alerts')
def phone_alert_status():
    return phone_alerts.status()


@app.post('/api/phone-alerts/enable')
def phone_alert_enable(value: dict):
    return {'id':phone_alerts.register(value)}


@app.post('/api/phone-alerts/disable')
def phone_alert_disable(value: dict):
    phone_alerts.remove(value)
    return {'disabled':True}


@app.post('/api/phone-alerts/test')
def phone_alert_test(value: dict):
    phone_alerts.test(value)
    return {'queued':True}


@app.get('/api/readiness')
def readiness():
    live_state=live.snapshot()
    reference=reference_status(live_state['config'],DATA/'validation')
    healthy=bool(live_state['last_monitor'] and __import__('time').time()-live_state['last_monitor']<15)
    return {'live_ready':bool(live_state['entries'] and live_state['status']=='running' and healthy and (reference['verified'] or live.allow_unverified)),
        'unverified_live_allowed':live.allow_unverified,'live_reference':reference,
        'public_host_configured':bool(PUBLIC_HOST),'authentication_configured':bool(PASSWORD)}


@app.get('/api/live')
def exchange_status(request: Request):
    result=live.snapshot()
    result['feed_health']=live_feed.health if live_feed else None
    return result


@app.get('/api/live/trailing-comparison')
def trailing_comparison(limit: int=Query(50,ge=1,le=100),offset: int=Query(0,ge=0)):
    return trailing_observation.report(limit,offset)


@app.put('/api/live/trailing-comparison/settings')
def trailing_comparison_settings(settings: ObservationSettings):
    return trailing_observation.configure(settings)


@app.get('/api/live/signal-decisions')
def signal_decisions(symbol: str | None = Query(None,pattern=r'^[A-Z0-9]{1,30}USDT$'),limit: int = Query(50,ge=1,le=200)):
    with live.db() as db:
        rows=db.execute('SELECT payload FROM signal_decisions'+(' WHERE symbol=?' if symbol else '')+' ORDER BY id DESC LIMIT ?',
            (symbol,limit) if symbol else (limit,)).fetchall()
    return {'items':[json.loads(row[0]) for row in rows]}


@app.get('/api/live/signal-alerts')
def signal_alerts(symbol: str | None = Query(None,pattern=r'^[A-Z0-9]{1,30}USDT$'),limit: int = Query(50,ge=1,le=100),offset: int = Query(0,ge=0),watch_ids: str | None = Query(None,pattern=r'^\d+(,\d+){0,199}$')):
    from .signal_alerts import read_alerts
    return read_alerts(live.path,symbol,limit,offset,{int(value) for value in watch_ids.split(',')} if watch_ids else set())


@app.get('/api/live/history')
def trade_history(symbol: str | None = Query(None,pattern=r'^[A-Z0-9]{1,30}USDT$'),limit: int = Query(50,ge=1,le=100),offset: int = Query(0,ge=0)):
    return live.history(symbol,limit,offset)


@app.get('/api/live/analysis')
def trade_analysis(symbol: str | None = Query(None,pattern=r'^[A-Z0-9]{1,30}USDT$'),strategy: str | None = Query(None,pattern=r'^(sr_breakout|sma_cross|sma_7_50|sr_sma_7_50|sr_rsi_daily|sr_inverse_intrabar|volume_delta_intrabar|sr_rsi_volume_gate|sr_order_block_gate|sr_volume_intrabar|sma_7_50_intrabar|order_block_intrabar|sr_hourly_intrabar|rsi_hourly_intrabar|unknown)$'),time_basis: str=Query('entry',pattern=r'^(entry|exit)$'),source: str | None=Query(None,max_length=512)):
    return live.analysis(symbol,strategy,time_basis,source)


@app.put('/api/live/settings')
def exchange_settings(request: Request,config: TestnetSettings):
    return live.configure(config)


@app.post('/api/live/activate')
def exchange_activate(request: Request,activation: TestnetActivation):
    try:return live.activate(activation.confirmation)
    except ValueError:raise
    except Exception:raise HTTPException(502,'Account preflight unavailable; inspect networking and credential configuration') from None


@app.post('/api/live/pause')
def exchange_pause(request: Request):
    return live.pause()


@app.post('/api/live/close-all')
def exchange_close_all(request: Request):
    try:return live.close_all()
    except ValueError:raise
    except Exception:raise HTTPException(502,'Close operation unavailable; check Bybit and reconcile positions') from None


@app.post('/api/live/baseline')
def exchange_baseline(request: Request):
    return live.establish_baseline()


@app.get('/api/connection')
def account_connection(environment: str='live'):
    if environment!='live':raise HTTPException(422,'Unsupported connection environment')
    return live_connection.status()


@app.post('/api/connection/verify')
def verify_connection(environment: str='live'):
    if environment!='live':raise HTTPException(422,'Unsupported connection environment')
    return live_connection.verify()


@app.get('/api/runner')
def runner_status():
    return live_feed.health if live_feed else {'status': 'not_started'}


@app.get('/api/watchlist')
def watchlist():
    return json.loads((ROOT / 'watchlist.json').read_text())


@app.get('/api/quote/{symbol}')
def current_quote(symbol: str, environment: str = 'live'):
    import re
    if not re.fullmatch(r'[A-Z0-9]{1,30}USDT',symbol) or environment!='live':
        raise HTTPException(422,'Unsupported symbol or environment')
    try:return quote(symbol,environment)
    except Exception:raise HTTPException(502,'Current Bybit price unavailable') from None


@app.get('/api/chart/{symbol}')
def chart(symbol: str, interval: str = '1', environment: str = 'live', lookback: int = Query(1000,ge=1000,le=5000), include_rsi: bool=False,include_volume_delta: bool=False,include_order_block: bool=False):
    import re
    if not re.fullmatch(r'[A-Z0-9]{1,30}USDT', symbol) or interval not in INTERVALS or environment!='live':
        raise HTTPException(422, 'Unsupported symbol or interval')
    try:
        rows = chart_history(symbol,interval,lookback,'live') if lookback>1000 else candles(symbol, interval)
    except (URLError, TimeoutError, ValueError, KeyError) as exc:
        raise HTTPException(502, 'Bybit market data unavailable; no synthetic fallback used') from exc
    cfg = live.snapshot()['config']
    history_runner = live_feed if lookback==1000 and cfg['strategy']!='volume_delta_intrabar' else None
    history_notice=''
    try:
        history = history_runner.history(symbol, interval, rows) if history_runner else rows
    except ValueError as exc:
        if str(exc) not in ('Market history gap exceeds API window; backfill required',
                            'Candle history contains a gap; no automatic entries'):
            raise
        # A chart is a display replay, not execution continuity. Keep trading
        # checkpoints intact; replay only the fresh, validated public window.
        from .indicator_stream import validate
        for row in rows:validate(row)
        if any(b['time']-a['time']!=INTERVALS[interval] for a,b in zip(rows,rows[1:])):
            raise HTTPException(502,'Bybit chart candles contain a gap; refresh to retry') from exc
        history_runner=None
        history=rows
        history_notice='Saved history gap: chart replay uses the latest available Bybit candles. Trading history recovery is separate.'
    # Display replay is independent of execution. Hourly intrabar entries cannot
    # be reconstructed from OHLC; show labelled S/R reference arrows instead.
    display_strategy='sma_7_50' if cfg['strategy']=='sma_7_50_intrabar' else 'sr_breakout' if cfg['strategy'] in ('sr_hourly_intrabar','rsi_hourly_intrabar','sr_rsi_daily','sr_inverse_intrabar','sr_rsi_volume_gate','sr_order_block_gate','sr_volume_intrabar','order_block_intrabar') else cfg['strategy']
    display_overrides=dict(major=True,minor=True,sma_enabled=False,sma_7_50_filter_enabled=False) if display_strategy=='sr_breakout' else {}
    chart_cfg = Settings(**{**{k:v for k,v in cfg.items() if k in Settings.model_fields}, **display_overrides,'strategy':display_strategy,'timeframe': interval}).model_copy(update={'timeframe':interval})
    analysis = [b for b in (history_runner.analysis(symbol,chart_cfg,history,__import__('time').time())[0] if history_runner else evaluate(history,chart_cfg)) if b['time'] >= rows[0]['time']]
    volume_delta_status='';volume_delta_analysis=[]
    if include_volume_delta or cfg['strategy']=='volume_delta_intrabar':
        from .volume_delta import signal,reference_history
        now=__import__('time').time();v=chart_cfg.volume_delta
        volume_delta_analysis=analysis if cfg['strategy']=='volume_delta_intrabar' else evaluate(history,chart_cfg.model_copy(update={'strategy':'volume_delta_intrabar'}),now)
        try:
            lower=[] if interval=='1' else chart_history(symbol,'1',3000 if interval=='D' else 1000,'live')
            historical=reference_history(rows,lower,interval,now,sma_filter=v.sma_filter,bull_type=v.bull_type,bear_type=v.bear_type,bullish_enabled=v.bullish,bearish_enabled=v.bearish) if interval!='1' else []
            by_time={item['time']:item for item in volume_delta_analysis}
            by_time.update({item['time']:item for item in historical})
            volume_delta_analysis=[by_time[stamp] for stamp in sorted(by_time)]
            current=signal(rows,lower,interval,now,sma_filter=v.sma_filter,bull_type=v.bull_type,bear_type=v.bear_type,
                bullish_enabled=v.bullish,bearish_enabled=v.bearish)
            volume_delta_analysis.append(dict(time=rows[-1]['time'],levels={},sma=None,signal=current['signal'],conflict=False,entry_sources=current['entry_sources']))
            volume_delta_status='Volume Delta · '+current['status']+'; delta '+str(current['delta'])+'; '+(current['signal']+' condition' if current['signal'] else 'no qualifying bullish/bearish signal after selected filters')+('; '+str(len(historical))+' closed reference bars covered by real 1m data' if interval!='1' else '; historical 1m reference conditions available')
        except (ValueError,KeyError,URLError,TimeoutError):volume_delta_status='Current Volume Delta data unavailable; no signal fabricated'
    if cfg['strategy']=='volume_delta_intrabar':analysis=volume_delta_analysis
    sr_cfg=chart_cfg.model_copy(update={'strategy':'sr_breakout','major':True,'minor':True,'sma_enabled':False,'sma_7_50_filter_enabled':False})
    sr_analysis=list(analysis) if sr_cfg==chart_cfg else [bar for bar in (history_runner.analysis(symbol,sr_cfg,history,__import__('time').time())[0] if history_runner else evaluate(history,sr_cfg)) if bar['time']>=rows[0]['time']]
    # Provisional display only: compare forming close to the last closed close
    # against confirmed levels. Do not confirm pivots using an unfinished candle.
    now=__import__('time').time();duration=INTERVALS[interval];start=int(now)//duration*duration
    if history and sr_analysis and history[-1]['time']==start and sr_analysis[-1]['time']==start-duration:
        previous=next((row for row in reversed(history) if row['time']==start-duration),None)
        if previous:
            latest=history[-1];levels=sr_analysis[-1]['levels'];crossed=[]
            for key,level in levels.items():
                value=level['value']
                if 'resistance' in key and previous['close']<=value<latest['close']:crossed.append((key,'Long'))
                if 'support' in key and previous['close']>=value>latest['close']:crossed.append((key,'Short'))
            sides={side for _,side in crossed};signal=next(iter(sides)) if len(sides)==1 else None
            sr_analysis.append(dict(time=start,close=latest['close'],levels=levels,sma=sr_analysis[-1].get('sma'),signal=signal,
                conflict=len(sides)>1,provisional=True,entry_sources=['sr:'+key for key,side in crossed if side==signal]))
    sr_reference_status='S/R chart arrows: all Major/Minor families, without trading entry filters; closed arrows confirmed, forming preview can change. Chart references do not prove a live order.'
    rsi_analysis=[]
    if include_rsi:
        rsi_cfg=Settings(**{k:v for k,v in cfg.items() if k in Settings.model_fields}).model_copy(update={'strategy':'rsi_hourly_intrabar','timeframe':interval})
        now=__import__('time').time()
        if history_runner:
            rsi_analysis=history_runner.analysis(symbol,rsi_cfg,history,now)[0]
            try:rsi_analysis=rsi_analysis+[history_runner.rsi_preview(symbol,rsi_cfg,history,now)]
            except ValueError:pass
        else:
            from .rsi import RSIState
            rsi_state=RSIState(rsi_cfg.rsi)
            for row in history:
                if row['time']<=now:rsi_analysis.append(rsi_state.step(row,INTERVALS[interval]))
        rsi_analysis=[bar for bar in rsi_analysis if bar['time']>=rows[0]['time']]
    order_block_analysis=[];order_block_status=''
    if include_order_block:
        from .order_block import reference
        from .models import OrderBlockSettings
        try:
            standalone=cfg['strategy']=='order_block_intrabar'
            unfiltered=standalone or cfg['strategy']=='sma_7_50_intrabar'
            settings=OrderBlockSettings(filter_breaks=False) if standalone else OrderBlockSettings(bullish_formed=True,bearish_formed=True,supply_breakout=False,demand_breakdown=False,filter_breaks=False) if unfiltered else OrderBlockSettings(**cfg.get('order_block',{}))
            order_block_analysis=reference(history,settings,__import__('time').time(),interval=interval,formation_filter=not unfiltered)
            label={'1':'1m','5':'5m','60':'1h','240':'4h','D':'Daily'}[interval]
            order_block_status=('Unfiltered Order Block '+label+' chart boxes; all four events, NO FILTERS. Live trading uses fixed 4h intrabar signals; chart timeframe is view only' if standalone else 'Unfiltered Order Block '+label+' chart boxes; no SMA/volume/other filters. When enabled, opposite FORMATION only exits the SMA strategy on 4h; block breaks do not exit. Chart timeframe is display only' if unfiltered else 'Order Block '+label+' chart references; SMA 200 uses the displayed timeframe; trading confirmation remains 5m for the S/R → Order Block strategy. History starts at loaded window, not proof of live orders')
        except ValueError as exc:order_block_status='Order Block references unavailable: '+str(exc)
    averages = [b for b in sma(history, 200 if cfg['strategy']=='sma_cross' else cfg['sma_length']) if b['time'] >= rows[0]['time']]
    return {'source': 'Bybit LIVE market data', 'candles': rows, 'sma': averages,
            'analysis': analysis,'sr_reference_status':sr_reference_status,'order_block_analysis':order_block_analysis,'order_block_status':order_block_status,'volume_delta_analysis':volume_delta_analysis,'volume_delta_status':volume_delta_status,'sr_analysis':sr_analysis,'chart_signal_strategy':display_strategy,'rsi_analysis':rsi_analysis,'rsi_status':('Fixed RSI Long/Short thresholds; levels do not adapt. Eight reference crossing types; no ML history window required. Arrows are not proof of an order.' if cfg.get('rsi',{}).get('calculation_mode')=='fixed' else 'Uploaded Pine array behavior: ML thresholds unavailable in the uploaded source; static RSI crossings are available. Chart arrows can include unfiltered crossings; trading uses the saved entry rules and filters.' if cfg.get('rsi',{}).get('calculation_mode')=='uploaded_pine' else 'Corrected ML RSI; eight reference crossing types. Arrows can include filtered-out crossings, not live trade evidence. '+('ML window ready' if rsi_analysis and rsi_analysis[-1]['ml_ready'] else 'Partial ML window / warming up')),
            'indicator_status': '4h Order Block intrabar: Bullish formation / Supply breakout opens Long; Bearish formation / Demand breakdown opens Short. NO FILTERS and no candle-close wait. Historical chart boxes are references, not proof of orders.' if cfg['strategy']=='order_block_intrabar' else '4h SMA 7/50 intrabar Reversal strategy. Historical SMA arrows are candle-close references and cannot reconstruct every sampled live crossover. Optional opposite 4h Order Block formation exit without filters; breaks ignored. SMA Reversal, stops, trailing and account risk remain.' if cfg['strategy']=='sma_7_50_intrabar' else 'S/R-only 4h/D permission, then 5m Order Block confirmation in either direction; SMA 200: formation inverted; supply break above / demand break below. Historical references are not proof of orders.' if cfg['strategy']=='sr_order_block_gate' else 'Two-stage strategy: 4h/Daily permission, then 1m Volume Delta entry in either direction. Historical chart arrows do not reconstruct gate state. E/X marks recorded trades.' if cfg['strategy']=='sr_rsi_volume_gate' else ('Volume Delta 1-minute candle-volume proxy; no VWAP divergence entries; no SMA exit. Historical delta references require complete actual 1-minute data. '+volume_delta_status) if cfg['strategy']=='volume_delta_intrabar' else 'Inverse strategy: underlying S/R arrows show the original direction; actual entries take the opposite side. Minute exits are inverted too: actual Short exits below, actual Long exits above (SMA 7/50).' if cfg['strategy']=='sr_inverse_intrabar' else 'S/R reference arrows: confirmed candle-close breakouts on the displayed timeframe; not historical live intrabar triggers. E/X marks recorded trades' if cfg['strategy'] in ('sr_hourly_intrabar','rsi_hourly_intrabar','sr_rsi_daily','sr_inverse_intrabar','sr_rsi_volume_gate','sr_order_block_gate','sr_volume_intrabar') else 'Confirmed SMA 7/50 crossover; independent comparison pending' if cfg['strategy']=='sma_7_50' else 'Independent S/R or SMA 7/50 entries; conflicting signals skipped' if cfg['strategy']=='sr_sma_7_50' else 'Confirmed SMA 7/200 crossover; independent comparison pending' if cfg['strategy']=='sma_cross' else 'Experimental S/R port; TradingView parity verification pending',
            'history_notice':history_notice,'last_candle_closed': False,'requested_bars':lookback,'available_bars':len(rows),'history_exhausted':len(rows)<lookback,'historical_replay':lookback>1000}


app.mount('/', StaticFiles(directory=ROOT / 'web', html=True), name='web')
