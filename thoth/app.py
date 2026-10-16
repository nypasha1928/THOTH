import json
import os
from pathlib import Path

# This single-purpose server must create private state, journals and receipts.
os.umask(0o077)
from contextlib import asynccontextmanager

from .runner import Runner
from .auth import guard, PUBLIC_HOST, PASSWORD
from .connection import Connection
from .testnet import TestnetSupervisor
from .production import ProductionSupervisor,ProductionClient
from .testnet_feed import FeedAdapter
from .verification import reference_status
from .bybit_testnet import TestnetClient
from .indicator import evaluate
from urllib.error import URLError

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .engine import Engine
from .market import candles, sma
from .models import Settings, PaperOrder, Marks, TestnetSettings, TestnetActivation

ROOT = Path(__file__).resolve().parent.parent
runner = None
testnet_feed = None
live_feed = None
connection = Connection()
live_connection = Connection(ProductionClient,environment='live')


@asynccontextmanager
async def lifespan(app):
    global runner, testnet_feed, live_feed
    runner = Runner(engine)
    testnet.start()
    adapter = FeedAdapter(testnet)
    testnet_feed = Runner(adapter,fetch=adapter.fetch)
    testnet_feed.start()
    live.start()
    live_adapter=FeedAdapter(live)
    live_feed=Runner(live_adapter,fetch=live_adapter.fetch)
    live_feed.start()
    runner.start()
    try:
        yield
    finally:
        runner.stop()
        testnet_feed.stop()
        testnet.stop()
        live_feed.stop()
        live.stop()


app = FastAPI(title='THOTH paper trading desk', version='0.3.0', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', '[::1]', 'testserver'] + ([PUBLIC_HOST] if PUBLIC_HOST else []))
engine = Engine(os.environ.get('THOTH_DB', str(ROOT / 'data' / 'paper.sqlite')))
testnet = TestnetSupervisor(Path(engine.path).with_name('testnet.sqlite'),
    enabled=os.environ.get('THOTH_ENABLE_TESTNET')=='1',password_configured=bool(PASSWORD))
live = ProductionSupervisor(Path(engine.path).with_name('live.sqlite'),enabled=os.environ.get('THOTH_ENABLE_LIVE')=='1',password_configured=bool(PASSWORD))


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
    return {'status':'ok','version':'0.3.0'}


@app.get('/api/state')
def state():
    return engine.snapshot()


@app.put('/api/settings')
def settings(config: Settings):
    return engine.configure(config)


@app.post('/api/control/{action}')
def control(action: str):
    return engine.control(action)


@app.post('/api/paper/orders')
def order(order: PaperOrder):
    return engine.order(order)


@app.post('/api/paper/marks')
def marks(marks: Marks):
    return engine.marks(marks.prices)


@app.get('/api/readiness')
def readiness():
    paper_cfg=engine.snapshot()['settings']
    testnet_state=testnet.snapshot()
    account=connection.status()
    live_state=live.snapshot()
    live_reference=reference_status(live_state['config'],Path(engine.path).parent/'validation')
    live_healthy=bool(live_state['last_monitor'] and __import__('time').time()-live_state['last_monitor']<15)
    return {'paper_available':True,'live_ready':bool(live_state['entries'] and live_state['status']=='running' and live_healthy and live_reference['verified']), 'live_reference':live_reference,'live_reason':'Live activation requires reference comparison, actual testnet acceptance, secure credentials and explicit server/operator activation',
        'public_host_configured':bool(PUBLIC_HOST),'authentication_configured':bool(PASSWORD),
        'testnet_server_enabled':testnet.enabled,'testnet_credentials_configured':account['configured'],
        'testnet_last_monitor':testnet_state['last_monitor'],
        'paper_reference':reference_status(paper_cfg,Path(engine.path).parent/'validation'),
        'testnet_reference':reference_status(testnet_state['config'],Path(engine.path).parent/'validation')}


def selected_exchange(request):
    return live if request.url.path.startswith('/api/live') else testnet


@app.get('/api/testnet')
@app.get('/api/live')
def exchange_status(request: Request):
    selected=selected_exchange(request)
    result=selected.snapshot()
    feed=live_feed if selected is live else testnet_feed
    result['feed_health']=feed.health if feed else None
    return result


@app.put('/api/testnet/settings')
@app.put('/api/live/settings')
def exchange_settings(request: Request,config: TestnetSettings):
    return selected_exchange(request).configure(config)


@app.post('/api/testnet/activate')
@app.post('/api/live/activate')
def exchange_activate(request: Request,activation: TestnetActivation):
    try:return selected_exchange(request).activate(activation.confirmation)
    except ValueError:raise
    except Exception:raise HTTPException(502,'Account preflight unavailable; inspect networking and credential configuration') from None


@app.post('/api/testnet/pause')
@app.post('/api/live/pause')
def exchange_pause(request: Request):
    return selected_exchange(request).pause()


@app.post('/api/testnet/close-all')
@app.post('/api/live/close-all')
def exchange_close_all(request: Request):
    try:return selected_exchange(request).close_all()
    except ValueError:raise
    except Exception:raise HTTPException(502,'Close operation unavailable; check Bybit and reconcile positions') from None


@app.post('/api/testnet/baseline')
@app.post('/api/live/baseline')
def exchange_baseline(request: Request):
    return selected_exchange(request).establish_baseline()


@app.get('/api/connection')
def account_connection(environment: str='testnet'):
    if environment not in ('testnet','live'):raise HTTPException(422,'Unsupported connection environment')
    return (live_connection if environment=='live' else connection).status()


@app.post('/api/connection/verify')
def verify_connection(environment: str='testnet'):
    if environment not in ('testnet','live'):raise HTTPException(422,'Unsupported connection environment')
    return (live_connection if environment=='live' else connection).verify()


@app.get('/api/runner')
def runner_status():
    return runner.health if runner else {'status': 'not_started'}


@app.get('/api/watchlist')
def watchlist():
    return json.loads((ROOT / 'watchlist.json').read_text())


@app.get('/api/chart/{symbol}')
def chart(symbol: str, interval: str = '1', environment: str = 'paper'):
    import re
    if not re.fullmatch(r'[A-Z0-9]{2,30}USDT', symbol) or interval not in ('1', 'D') or environment not in ('paper','testnet','live'):
        raise HTTPException(422, 'Unsupported symbol or interval')
    try:
        rows = TestnetClient().candles(symbol, interval) if environment=='testnet' else candles(symbol, interval)
    except (URLError, TimeoutError, ValueError, KeyError) as exc:
        raise HTTPException(502, 'Bybit market data unavailable; no synthetic fallback used') from exc
    cfg = (testnet if environment=='testnet' else live).snapshot()['config'] if environment in ('testnet','live') else engine.snapshot()['settings']
    history_runner = testnet_feed if environment=='testnet' else live_feed if environment=='live' else runner
    history = history_runner.history(symbol, interval, rows) if history_runner else rows
    # Chart timeframe is independent from the execution setting.
    chart_cfg = Settings(**{**{k:v for k,v in cfg.items() if k in Settings.model_fields}, 'timeframe': interval})
    analysis = [b for b in (history_runner.analysis(symbol,chart_cfg,history,__import__('time').time())[0] if history_runner else evaluate(history,chart_cfg)) if b['time'] >= rows[0]['time']]
    averages = [b for b in sma(history, cfg['sma_length']) if b['time'] >= rows[0]['time']]
    return {'source': 'Bybit TESTNET public API' if environment=='testnet' else 'Bybit LIVE market data' if environment=='live' else 'Bybit public API', 'candles': rows, 'sma': averages,
            'analysis': analysis,
            'indicator_status': 'Experimental S/R port; TradingView parity verification pending',
            'last_candle_closed': False}


app.mount('/', StaticFiles(directory=ROOT / 'web', html=True), name='web')
