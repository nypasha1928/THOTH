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

ROOT = Path(__file__).resolve().parent.parent
live_feed = None
live_connection = Connection(ProductionClient,environment='live')


@asynccontextmanager
async def lifespan(app):
    global live_feed
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


app = FastAPI(title='THOTH live trading desk', version='0.4.0', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', '[::1]', 'testserver'] + ([PUBLIC_HOST] if PUBLIC_HOST else []))
# Preserve the existing live.sqlite location and journals on upgrades.
# THOTH_DB remains a compatibility locator; no simulation database is opened.
DATA = Path(os.environ.get('THOTH_DB',str(ROOT/'data'/'live.sqlite'))).parent
live = ProductionSupervisor(DATA/'live.sqlite',enabled=os.environ.get('THOTH_ENABLE_LIVE')=='1',password_configured=bool(PASSWORD),allow_unverified=os.environ.get('THOTH_ALLOW_UNVERIFIED_LIVE')=='1')
trailing_observation=TrailingObservation(DATA/'trailing-observation.sqlite',DATA/'live.sqlite')


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
def trade_analysis(symbol: str | None = Query(None,pattern=r'^[A-Z0-9]{1,30}USDT$'),strategy: str | None = Query(None,pattern=r'^(sr_breakout|sma_cross|sma_7_50|sr_sma_7_50|sr_rsi_daily|sr_hourly_intrabar|rsi_hourly_intrabar|unknown)$'),time_basis: str=Query('entry',pattern=r'^(entry|exit)$'),source: str | None=Query(None,max_length=512)):
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
def chart(symbol: str, interval: str = '1', environment: str = 'live', lookback: int = Query(1000,ge=1000,le=5000), include_rsi: bool=False):
    import re
    if not re.fullmatch(r'[A-Z0-9]{1,30}USDT', symbol) or interval not in INTERVALS or environment!='live':
        raise HTTPException(422, 'Unsupported symbol or interval')
    try:
        rows = chart_history(symbol,interval,lookback,'live') if lookback>1000 else candles(symbol, interval)
    except (URLError, TimeoutError, ValueError, KeyError) as exc:
        raise HTTPException(502, 'Bybit market data unavailable; no synthetic fallback used') from exc
    cfg = live.snapshot()['config']
    history_runner = live_feed if lookback==1000 else None
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
    display_strategy='sr_breakout' if cfg['strategy'] in ('sr_hourly_intrabar','rsi_hourly_intrabar','sr_rsi_daily') else cfg['strategy']
    chart_cfg = Settings(**{**{k:v for k,v in cfg.items() if k in Settings.model_fields}, 'strategy':display_strategy,'timeframe': interval}).model_copy(update={'timeframe':interval})
    analysis = [b for b in (history_runner.analysis(symbol,chart_cfg,history,__import__('time').time())[0] if history_runner else evaluate(history,chart_cfg)) if b['time'] >= rows[0]['time']]
    sr_analysis=analysis
    if display_strategy!='sr_breakout':
        sr_cfg=chart_cfg.model_copy(update={'strategy':'sr_breakout'})
        sr_analysis=[bar for bar in (history_runner.analysis(symbol,sr_cfg,history,__import__('time').time())[0] if history_runner else evaluate(history,sr_cfg)) if bar['time']>=rows[0]['time']]
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
    averages = [b for b in sma(history, 200 if cfg['strategy']=='sma_cross' else cfg['sma_length']) if b['time'] >= rows[0]['time']]
    return {'source': 'Bybit LIVE market data', 'candles': rows, 'sma': averages,
            'analysis': analysis,'sr_analysis':sr_analysis,'chart_signal_strategy':display_strategy,'rsi_analysis':rsi_analysis,'rsi_status':('Fixed RSI Long/Short thresholds; levels do not adapt. Eight reference crossing types; no ML history window required. Arrows are not proof of an order.' if cfg.get('rsi',{}).get('calculation_mode')=='fixed' else 'Uploaded Pine array behavior: ML thresholds unavailable in the uploaded source; static RSI crossings are available. Chart arrows can include unfiltered crossings; trading uses the saved entry rules and filters.' if cfg.get('rsi',{}).get('calculation_mode')=='uploaded_pine' else 'Corrected ML RSI; eight reference crossing types. Arrows can include filtered-out crossings, not live trade evidence. '+('ML window ready' if rsi_analysis and rsi_analysis[-1]['ml_ready'] else 'Partial ML window / warming up')),
            'indicator_status': 'S/R reference arrows: confirmed candle-close breakouts on the displayed timeframe; not historical live intrabar triggers. E/X marks recorded trades' if cfg['strategy'] in ('sr_hourly_intrabar','rsi_hourly_intrabar','sr_rsi_daily') else 'Confirmed SMA 7/50 crossover; independent comparison pending' if cfg['strategy']=='sma_7_50' else 'Independent S/R or SMA 7/50 entries; conflicting signals skipped' if cfg['strategy']=='sr_sma_7_50' else 'Confirmed SMA 7/200 crossover; independent comparison pending' if cfg['strategy']=='sma_cross' else 'Experimental S/R port; TradingView parity verification pending',
            'history_notice':history_notice,'last_candle_closed': False,'requested_bars':lookback,'available_bars':len(rows),'history_exhausted':len(rows)<lookback,'historical_replay':lookback>1000}


app.mount('/', StaticFiles(directory=ROOT / 'web', html=True), name='web')
