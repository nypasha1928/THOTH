"""Testnet-only execution building blocks; not connected to the app's paper runner.

No production hostname is accepted. Never retry a submission blindly after a
transport failure: reconcile by the original orderLinkId instead.
"""
import hashlib
import hmac
import json
import time
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class BybitError(RuntimeError):
    def __init__(self, message='', code=None):
        super().__init__(message)
        self.code=code


class AmbiguousOrder(BybitError):
    """Submission may have reached Bybit; reconcile before further action."""


def dec(value):
    return Decimal(str(value))


def grid(value, step, rounding=ROUND_DOWN):
    return (dec(value)/dec(step)).to_integral_value(rounding=rounding)*dec(step)


def order_payload(symbol, side, notional, price, instrument, signal_id):
    if instrument.get('symbol')!=symbol or instrument.get('status')!='Trading':
        raise ValueError('Instrument is not trading')
    if instrument.get('contractType')!='LinearPerpetual' or instrument.get('settleCoin')!='USDT':
        raise ValueError('Only USDT linear perpetuals are supported')
    if side not in ('Long','Short') or dec(price)<=0 or dec(notional)<=0:
        raise ValueError('Invalid order sizing or side')
    lot=instrument['lotSizeFilter']
    qty=grid(dec(notional)/dec(price),lot['qtyStep'])
    if qty<dec(lot['minOrderQty']) or qty*dec(price)<dec(lot['minNotionalValue']):
        raise ValueError('Requested size is below exchange minimum; size will not be increased')
    if qty>dec(lot['maxMktOrderQty']):
        raise ValueError('Requested size exceeds exchange market maximum')
    link='thoth-'+hashlib.sha256(signal_id.encode()).hexdigest()[:28]
    return dict(category='linear',symbol=symbol,side='Buy' if side=='Long' else 'Sell',
                orderType='Market',qty=format(qty,'f'),positionIdx=0,reduceOnly=False,orderLinkId=link)


def stop_price(side, entry, qty, loss_dollars, tick):
    if side not in ('Long','Short') or min(dec(entry),dec(qty),dec(loss_dollars),dec(tick))<=0:
        raise ValueError('Invalid dollar stop inputs')
    raw=dec(entry)+(-1 if side=='Long' else 1)*dec(loss_dollars)/dec(qty)
    if raw<=0:raise ValueError('Dollar stop implies non-positive price')
    # Round toward entry so price rounding does not enlarge the intended loss.
    result=grid(raw,tick,ROUND_UP if side=='Long' else ROUND_DOWN)
    if (side=='Long' and result>=dec(entry)) or (side=='Short' and result<=dec(entry)):
        raise ValueError('Dollar stop is smaller than one price tick')
    return format(result,'f')


class TestnetClient:
    BASE='https://api-testnet.bybit.com'

    def __init__(self, key='', secret='', armed=False, transport=urlopen):
        self.key,self.secret,self.armed,self.transport=key,secret,armed,transport

    def request(self, method, path, params, private=False):
        if not path.startswith('/v5/') or method not in ('GET','POST'):
            raise ValueError('Unsupported Bybit request')
        encoded=urlencode(sorted(params.items())) if method=='GET' else json.dumps(params,separators=(',',':'))
        headers={'Content-Type':'application/json'}
        if private:
            if not self.key or not self.secret:raise BybitError('Testnet credentials missing')
            timestamp=str(int(time.time()*1000))
            message=timestamp+self.key+'5000'+encoded
            headers.update({'X-BAPI-API-KEY':self.key,'X-BAPI-TIMESTAMP':timestamp,'X-BAPI-RECV-WINDOW':'5000',
                'X-BAPI-SIGN':hmac.new(self.secret.encode(),message.encode(),hashlib.sha256).hexdigest()})
        url=self.BASE+path+('?' + encoded if method=='GET' and encoded else '')
        req=Request(url,data=encoded.encode() if method=='POST' else None,headers=headers,method=method)
        with self.transport(req,timeout=10) as response:body=json.load(response)
        if body.get('retCode')!=0:raise BybitError(f"Bybit rejected request ({body.get('retCode')}): {body.get('retMsg')}",code=body.get('retCode'))
        return body['result']

    def instrument(self,symbol):
        rows=self.request('GET','/v5/market/instruments-info',dict(category='linear',symbol=symbol))['list']
        if len(rows)!=1:raise BybitError('Instrument not found')
        return rows[0]

    def reconcile(self,symbol,link):
        rows=self.request('GET','/v5/order/realtime',dict(category='linear',symbol=symbol,orderLinkId=link),private=True)['list']
        found=next((r for r in rows if r['orderLinkId']==link),None)
        if found:return found
        history=self.request('GET','/v5/order/history',dict(category='linear',symbol=symbol,orderLinkId=link),private=True)['list']
        return next((r for r in history if r['orderLinkId']==link),None)

    def submit(self,payload):
        if not self.armed:raise BybitError('Testnet execution is not armed')
        if not payload.get('orderLinkId') or payload.get('category')!='linear':
            raise ValueError('An idempotency identifier and linear category are required')
        existing=self.reconcile(payload['symbol'],payload['orderLinkId'])
        if existing:return {'state':'reconciled','order':existing}
        try:
            accepted=self.request('POST','/v5/order/create',payload,private=True)
        except BybitError as exc:
            if exc.code in (10014,110072):
                raise AmbiguousOrder('Duplicate execution identity; reconcile before continuing') from exc
            raise  # Other exchange rejections are definitive.
        except (OSError,TimeoutError,json.JSONDecodeError) as exc:
            raise AmbiguousOrder('Submission outcome unknown; reconcile original orderLinkId before continuing') from exc
        return {'state':'accepted_not_filled','order':accepted}

    def attach_stop(self,symbol,side,entry,qty,dollars,instrument):
        if not self.armed:raise BybitError('Testnet execution is not armed')
        stop=stop_price(side,entry,qty,dollars,instrument['priceFilter']['tickSize'])
        return self.request('POST','/v5/position/trading-stop',dict(category='linear',symbol=symbol,
            tpslMode='Full',positionIdx=0,stopLoss=stop,slTriggerBy='MarkPrice'),private=True)

    def candles(self,symbol,interval):
        rows=self.request('GET','/v5/market/kline',dict(category='linear',symbol=symbol,interval=interval,limit=1000))['list']
        return sorted([dict(time=int(r[0])//1000,open=float(r[1]),high=float(r[2]),low=float(r[3]),close=float(r[4]),volume=float(r[5])) for r in rows],key=lambda r:r['time'])

    def close_payload(self,symbol,side,qty,identifier):
        if side not in ('Long','Short') or dec(qty)<=0:raise ValueError('Invalid close')
        return dict(category='linear',symbol=symbol,side='Sell' if side=='Long' else 'Buy',
                    orderType='Market',qty=str(qty),reduceOnly=True,positionIdx=0,
                    orderLinkId='thoth-'+hashlib.sha256(identifier.encode()).hexdigest()[:28])
