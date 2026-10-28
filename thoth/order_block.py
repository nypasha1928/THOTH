"""Causal port of the uploaded Salem Order Block signal/zone rules.

Trading profile: RSI/VWAP/SMA7/50/volume/strength filters off; inverted SMA200
on. Raw uploaded source is preserved separately. Supply/demand break alerts
bypass SMA200 in the original; filter_breaks is an explicit override: supply break ABOVE SMA200, demand break BELOW.
Forming candles must run on a copy of the previous CLOSED state (Pine rollback).
"""
from copy import deepcopy
from .indicator_stream import validate
from .timeframes import interval_seconds

LABELS={'bullish_formed':'Bullish Order Block formed','bearish_formed':'Bearish Order Block formed',
        'supply_breakout':'Breakout Above Supply','demand_breakdown':'Breakdown Below Demand'}
SIDES={'bullish_formed':'Long','bearish_formed':'Short','supply_breakout':'Long','demand_breakdown':'Short'}


def entry_volume(rows,interval='240'):
    """Mandatory standalone entry gate; raw box construction remains independent."""
    from .sr_volume import volume_pass
    duration=interval_seconds(interval);label='1h' if interval=='60' else '4h'
    if len(rows)>=20:
        try:times=[float(row['time']) for row in rows[-20:]]
        except (KeyError,ValueError,TypeError):raise ValueError('Invalid '+label+' volume history') from None
        if any(current-previous!=duration for previous,current in zip(times,times[1:])):
            raise ValueError('Contiguous '+label+' volume history required')
    return volume_pass(rows,20)

class OrderBlockState:
    def __init__(self,state=None,interval='5',*,formation_filter=True):
        self.formation_filter=formation_filter
        self.duration=interval_seconds(interval)
        self.interval=interval
        self.s=deepcopy(state) if state else dict(frames=[],closes=[],atr=None,tr=[],buy=[],sell=[],last_time=None,count=0)

    def checkpoint(self):return deepcopy(self.s)

    def step(self,row):
        validate(row)
        s=self.s;frames=s['frames'];current=dict(row)
        if s['last_time'] is not None and row['time']!=s['last_time']+self.duration:
            raise ValueError('Order Block history must contain consecutive selected-timeframe candles')
        op,hi,lo,close=(float(row[k]) for k in ('open','high','low','close'))
        previous=lambda n:frames[-n] if len(frames)>=n else {}
        p,p2,p3=previous(1),previous(2),previous(3)
        tr=max(hi-lo,abs(hi-p['close']),abs(lo-p['close'])) if p else hi-lo
        if s['atr'] is None:
            s['tr'].append(tr)
            if len(s['tr'])==14:s['atr']=sum(s['tr'])/14;s['tr']=[]
        else:s['atr']=(s['atr']*13+tr)/14
        s['closes']=(s['closes']+[close])[-200:]
        sma=sum(s['closes'])/200 if len(s['closes'])==200 else None
        current.update(ceiling=max(hi,p.get('close',hi),p.get('open',hi)),floor=min(lo,p.get('close',lo),p.get('open',lo)),
            buying=close>=op and hi!=lo,selling=close<=op and lo!=hi,
            green=bool(p) and close>op and close>p['close'],red=bool(p) and close<op and close<p['close'],
            higher=bool(p) and hi>p['high'],lower=bool(p) and lo<p['low'])
        c=current
        c['bullish']=c['green'] and c['higher'];c['bearish']=c['red'] and c['lower']
        bull_engulf=bool(p) and p['selling'] and (op<p['close'] or c['lower']) and c['bullish'] and close>p['open'] and s['atr'] is not None and close-op>s['atr']
        bear_engulf=bool(p) and p['buying'] and (op>p['close'] or c['higher']) and c['bearish'] and close<p['open'] and s['atr'] is not None and op-close>s['atr']
        break_high=bool(p) and (p2.get('selling',False) or p['selling']) and c['buying'] and close>p['ceiling']
        break_low=bool(p) and (p2.get('buying',False) or p['buying']) and c['selling'] and close<p['floor']
        white=bool(p3) and p3['bearish'] and p2['buying'] and p['bullish'] and c['bullish'] and close>p3['high']
        black=bool(p3) and p3['bullish'] and p2['selling'] and p['bearish'] and c['bearish'] and close<p3['low']
        c['soaring']=bull_engulf or break_high or white;c['tumbling']=bear_engulf or break_low or black
        reversal=(not p.get('soaring',False) and not p2.get('soaring',False)) if white else (not p.get('tumbling',False) and not p2.get('tumbling',False)) if black else (not p.get('soaring',False) and (p.get('bearish',False) or p2.get('bearish',False))) if break_high else (not p.get('tumbling',False) and (p.get('bullish',False) or p2.get('bullish',False))) if break_low else False
        continuation=(p2.get('bullish',False) and close>p2['high'] and not p.get('bearish',False)) if break_high else (p2.get('bearish',False) and close<p2['low'] and not p.get('bullish',False)) if break_low else False
        engulfing=(bull_engulf or bear_engulf) and (p.get('higher',False) or p.get('lower',False))
        up=c['soaring'] and (reversal or continuation or engulfing) and (not self.formation_filter or (sma is not None and close<sma))
        down=c['tumbling'] and (reversal or continuation or engulfing) and (not self.formation_filter or (sma is not None and close>sma))
        events=[]
        # Head-only, strict wick breaks and FIFO removal order match Pine's while.
        broken_supply=[];broken_demand=[]
        while s['sell'] and s['sell'][0]['high']<hi:broken_supply.append(s['sell'].pop(0))
        while s['buy'] and s['buy'][0]['low']>lo:broken_demand.append(s['buy'].pop(0))
        if broken_supply:events.append('supply_breakout')
        if broken_demand:events.append('demand_breakdown')
        if up or down:
            dac=3 if (white if up else black) else 2 if ((break_high and p2.get('selling',False)) if up else (break_low and p2.get('buying',False))) else 1
            pivot=2 if (white if up else black) else 1 if (p.get('green',False) if up else p.get('red',False)) else 0
            at=lambda n:c if n==0 else previous(n)
            a,b=at(dac),at(pivot)
            if up:
                bottom=min(a['low'],b['low'],p['low'],lo)
                top=a['high'] if b['close']>a['high'] else b['open'] if b['open']>a['open'] else a['open']
                zones=s['buy'];overlap=bool(zones) and zones[0]['high']>=bottom
            else:
                top=max(a['high'],b['high'],p['high'],hi)
                bottom=a['low'] if b['close']<a['low'] else b['open'] if b['open']<a['open'] else a['open']
                zones=s['sell'];overlap=bool(zones) and zones[0]['low']<=top
            if overlap:
                replace=zones[0]['bounced'];zones[0]['bounced']=True
                if replace:zones.pop(0)
                elif up:zones[0]['high']=bottom
                else:zones[0]['low']=top
            zones.insert(0,dict(high=top,low=bottom,time=a['time'],bounced=False))
            events.append('bullish_formed' if up else 'bearish_formed')
        s['frames']=(frames+[current])[-4:];s['last_time']=row['time'];s['count']+=1
        return dict(time=row['time'],close=close,sma200=sma,ready=sma is not None if self.formation_filter else s['count']>=14,events=events,
            broken_supply=broken_supply,broken_demand=broken_demand,
            zones=dict(demand=deepcopy(s['buy'][:23]),supply=deepcopy(s['sell'][:23])))


def select(result,settings,*,formation_filter=True):
    events=[]
    for kind in result['events']:
        if not getattr(settings,kind):continue
        if (kind.endswith('_formed') and formation_filter) or (not kind.endswith('_formed') and settings.filter_breaks):
            average=result['sma200'];price=result['close']
            below=kind in ('bullish_formed','demand_breakdown')
            if average is None or not (price<average if below else price>average):continue
        events.append(dict(kind=kind,side=SIDES[kind]))
    sides={e['side'] for e in events}
    return {**result,'events':events,'signal':next(iter(sides)) if len(sides)==1 else None,
        'conflict':len(sides)>1,'entry_sources':['ob:'+e['kind'] for e in events]}


def reference(rows,settings,now,interval='5',*,formation_filter=True):
    """Read-only causal replay on selected-timeframe data, including a rollback-safe forming bar."""
    state=OrderBlockState(interval=interval,formation_filter=formation_filter);output=[]
    for row in rows:
        if row['time']>now:break
        preview=state if row['time']+state.duration<=now else OrderBlockState(state.checkpoint(),interval=interval,formation_filter=formation_filter)
        output.append(select(preview.step(row),settings,formation_filter=formation_filter))
    return output
