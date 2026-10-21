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

ROOT = Path(__file__).resolve().parent.parent
live_feed = None
live_connection = Connection(ProductionClient,environment='live')


@asynccontextmanager
async def lifespan(app):
    global live_feed
    live.start()
    adapter=FeedAdapter(live)
    live_feed=Runner(adapter,fetch=adapter.fetch)
    live_feed.start()
    try:
        yield
    finally:
        live_feed.stop()
        live.stop()


app = FastAPI(title='THOTH live trading desk', version='0.4.0', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', '[::1]', 'testserver'] + ([PUBLIC_HOST] if PUBLIC_HOST else []))
# Preserve the existing live.sqlite location and journals on upgrades.
# THOTH_DB remains a compatibility locator; no simulation database is opened.
DATA = Path(os.environ.get('THOTH_DB',str(ROOT/'data'/'live.sqlite'))).parent
live = ProductionSupervisor(DATA/'live.sqlite',enabled=os.environ.get('THOTH_ENABLE_LIVE')=='1',password_configured=bool(PASSWORD),allow_unverified=os.environ.get('THOTH_ALLOW_UNVERIFIED_LIVE')=='1')


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


@app.get('/api/live/history')
def trade_history(symbol: str | None = Query(None,pattern=r'^[A-Z0-9]{2,30}USDT$'),limit: int = Query(50,ge=1,le=100),offset: int = Query(0,ge=0)):
    return live.history(symbol,limit,offset)


@app.get('/api/live/analysis')
def trade_analysis(symbol: str | None = Query(None,pattern=r'^[A-Z0-9]{2,30}USDT$'),strategy: str | None = Query(None,pattern=r'^(sr_breakout|sma_cross|sr_hourly_intrabar|unknown)$')):
    return live.analysis(symbol,strategy)


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
    if not re.fullmatch(r'[A-Z0-9]{2,30}USDT',symbol) or environment!='live':
        raise HTTPException(422,'Unsupported symbol or environment')
    try:return quote(symbol,environment)
    except Exception:raise HTTPException(502,'Current Bybit price unavailable') from None


@app.get('/api/chart/{symbol}')
def chart(symbol: str, interval: str = '1', environment: str = 'live', lookback: int = Query(1000,ge=1000,le=5000)):
    import re
    if not re.fullmatch(r'[A-Z0-9]{2,30}USDT', symbol) or interval not in INTERVALS or environment!='live':
        raise HTTPException(422, 'Unsupported symbol or interval')
    try:
        rows = chart_history(symbol,interval,lookback,'live') if lookback>1000 else candles(symbol, interval)
    except (URLError, TimeoutError, ValueError, KeyError) as exc:
        raise HTTPException(502, 'Bybit market data unavailable; no synthetic fallback used') from exc
    cfg = live.snapshot()['config']
    history_runner = live_feed if lookback==1000 else None
    history = history_runner.history(symbol, interval, rows) if history_runner else rows
    # Chart timeframe is independent from the execution setting.
    chart_cfg = Settings(**{**{k:v for k,v in cfg.items() if k in Settings.model_fields}, 'timeframe': interval}).model_copy(update={'timeframe':interval})
    analysis = [b for b in (history_runner.analysis(symbol,chart_cfg,history,__import__('time').time())[0] if history_runner else evaluate(history,chart_cfg)) if b['time'] >= rows[0]['time']]
    averages = [b for b in sma(history, 200 if cfg['strategy']=='sma_cross' else cfg['sma_length']) if b['time'] >= rows[0]['time']]
    return {'source': 'Bybit LIVE market data', 'candles': rows, 'sma': averages,
            'analysis': analysis,
            'indicator_status': 'Confirmed S/R levels only; sampled intrabar entries cannot be reconstructed from historical OHLC' if cfg['strategy']=='sr_hourly_intrabar' else 'Confirmed SMA 7/200 crossover; independent comparison pending' if cfg['strategy']=='sma_cross' else 'Experimental S/R port; TradingView parity verification pending',
            'last_candle_closed': False,'requested_bars':lookback,'available_bars':len(rows),'history_exhausted':len(rows)<lookback,'historical_replay':lookback>1000}


app.mount('/', StaticFiles(directory=ROOT / 'web', html=True), name='web')
