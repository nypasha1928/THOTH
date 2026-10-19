"""Server-sampled trailing rules; every tightened stop is placed on Bybit."""
from decimal import ROUND_UP, ROUND_DOWN
from .bybit_testnet import dec,grid


FIELDS=('trailing_enabled','trailing_activation_basis','trailing_activation_pct','trailing_distance_pct','trailing_distance_basis')


def new_trailing(config,entry,qty,margin,side):
    if not config.get('trailing_enabled',False):return {'enabled':False}
    direction=1 if side=='Buy' else -1
    basis=config.get('trailing_activation_basis','margin')
    reference=dec(entry) if basis=='price' else dec(margin)/dec(qty)
    activation=dec(entry)+direction*reference*dec(config['trailing_activation_pct'])/100
    if not activation.is_finite() or activation<=0:raise ValueError('Trailing activation is unreachable for this position')
    return dict(enabled=True,activation_basis=basis,activation_pct=config['trailing_activation_pct'],distance_pct=config['trailing_distance_pct'],
        distance_basis=config['trailing_distance_basis'],activation_price=str(activation),active=False,peak=None,stop=None,pending_stop=None)


def advance(state,side,entry,qty,margin,mark,base_stop,tick):
    state=dict(state)
    mark=dec(mark);tick=dec(tick)
    if not mark.is_finite() or mark<=0:raise ValueError('Trailing requires a valid mark price')
    long=side=='Buy'
    if not state.get('active'):
        reached=mark>=dec(state['activation_price']) if long else mark<=dec(state['activation_price'])
        if not reached:return state,None
        state.update(active=True,peak=str(mark))
    peak=max(dec(state['peak']),mark) if long else min(dec(state['peak']),mark)
    state['peak']=str(peak)
    distance=(peak if state['distance_basis']=='price' else dec(margin)/dec(qty))*dec(state['distance_pct'])/100
    target=grid(peak-distance if long else peak+distance,tick,ROUND_UP if long else ROUND_DOWN)
    if target<=0:raise ValueError('Trailing stop is non-positive')
    # Persisted pending writes also constrain future stops; never loosen after a timeout.
    limits=[dec(base_stop),target]
    for key in ('stop','pending_stop'):
        if state.get(key):limits.append(dec(state[key]))
    target=max(limits) if long else min(limits)
    return state,format(target,'f')
