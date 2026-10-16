"""Persistent paper execution. No exchange orders or implicit signal generation."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .models import Settings


def utcnow():
    return datetime.now(timezone.utc)


class Engine:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
            if not db.execute('SELECT 1 FROM state').fetchone():
                self.save(db, dict(settings=Settings().model_dump(), balance=250.0, day_equity=250.0,
                                   day=utcnow().date().isoformat(), mode='paper', status='paused',
                                   reason='Start paper mode manually', resume_at=None, streak=0,
                                   positions=[], trades=[], signals=[], events=[]))

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def load(self, db):
        s = json.loads(db.execute('SELECT payload FROM state WHERE id=1').fetchone()[0])
        s['settings'] = {**Settings().model_dump(), **s['settings']}
        return s

    def save(self, db, state):
        db.execute('INSERT OR REPLACE INTO state VALUES(1, ?)', (json.dumps(state, allow_nan=False),))

    @staticmethod
    def pnl(p):
        return (p['mark'] - p['entry']) * p['qty'] * (1 if p['side'] == 'Long' else -1)

    def equity(self, s):
        return s['balance'] + sum(self.pnl(p) for p in s['positions'])

    def event(self, s, text):
        s['events'] = (s['events'] + [{'time': utcnow().isoformat(), 'message': text}])[-100:]

    def roll_day(self, s):
        now = utcnow()
        if s['day'] != now.date().isoformat():
            s['day'] = now.date().isoformat()
            s['day_equity'] = self.equity(s)
            if s['status'] == 'daily_stop':
                s.update(status='running' if s['settings']['daily_resume_automatic'] else 'paused', reason='', resume_at=None, streak=0)
                self.event(s, 'New UTC day: daily stop released')
        if s['status'] == 'cooldown' and now >= datetime.fromisoformat(s['resume_at']):
            s.update(status='running', reason='', resume_at=None, streak=0)
            self.event(s, 'Consecutive-loss pause completed')

    def close(self, s, p, reason):
        pnl = self.pnl(p)
        s['balance'] += pnl
        s['positions'].remove(p)
        s['trades'] = (s['trades'] + [dict(p, pnl=pnl, exit=p['mark'], closed_at=utcnow().isoformat(), reason=reason)])[-500:]
        if pnl < 0:
            s['streak'] += 1
        elif pnl > 0:
            s['streak'] = 0
        self.event(s, f"{p['symbol']} closed: {reason}")

    def risk(self, s):
        cfg = s['settings']
        # Check equity before closing individual positions: profitable positions must also close.
        loss_pct = max(0, (s['day_equity'] - self.equity(s)) / max(s['day_equity'], 0.000001) * 100)
        if loss_pct >= cfg['daily_loss_pct']:
            for p in list(s['positions']):
                self.close(s, p, 'Daily loss limit')
            s.update(status='daily_stop', reason='Daily loss limit reached; paused until next UTC day', resume_at=None)
            return
        if cfg['portfolio_loss_enabled']:
            losses=sum(max(0,-self.pnl(p)) for p in s['positions'])
            margin=sum(p['margin'] for p in s['positions'] if cfg['portfolio_loss_basis']=='all_margin' or self.pnl(p)<0)
            if margin>0 and losses/margin*100>=cfg['portfolio_loss_pct']:
                for p in list(s['positions']):self.close(s,p,'Overall position loss limit')
                s.update(status='portfolio_stop',reason='Overall position loss limit; manual restart required',resume_at=None)
                return
        for p in list(s['positions']):
            # Freeze each position's stop at entry, independent of subsequent setting edits.
            if self.pnl(p) <= -p['stop_dollars']:
                self.close(s, p, 'Individual dollar stop')
        if s['streak'] >= cfg['consecutive_losses'] and s['status'] not in ('cooldown', 'daily_stop'):
            s.update(status='cooldown', reason='Consecutive loss limit',
                     resume_at=(utcnow() + timedelta(minutes=cfg['pause_minutes'])).isoformat())
            self.event(s, 'New entries paused; existing paper positions remain managed')

    def snapshot(self):
        with self.transaction() as db:
            s = self.load(db)
            self.roll_day(s)
            self.risk(s)
            self.save(db, s)
            result = dict(s)
            result['equity'] = self.equity(s)
            result['positions'] = [dict(p, pnl=self.pnl(p)) for p in s['positions']]
            result['daily_loss_pct'] = max(0, (s['day_equity'] - result['equity']) / max(s['day_equity'], 0.000001) * 100)
            return result

    def configure(self, cfg):
        with self.transaction() as db:
            s = self.load(db)
            if any(p['symbol'] not in cfg.symbols for p in s['positions']):
                raise ValueError('Close an open position before removing its symbol')
            s['settings'] = cfg.model_dump()
            self.risk(s)
            self.save(db, s)
        return self.snapshot()

    def control(self, action):
        with self.transaction() as db:
            s = self.load(db)
            self.roll_day(s)
            if action == 'start':
                if s['status'] in ('daily_stop', 'cooldown'):
                    raise ValueError('Risk pause cannot be bypassed by Start')
                s.update(status='running', reason='')
            elif action == 'pause':
                if s['status'] not in ('daily_stop', 'cooldown'):
                    s.update(status='paused', reason='Manually paused')
            elif action == 'close_all':
                for p in list(s['positions']):
                    self.close(s, p, 'Manual close all')
                if s['status'] not in ('daily_stop', 'cooldown'):
                    s.update(status='paused', reason='Manual close all; restart required')
            else:
                raise ValueError('Unknown control')
            self.risk(s)
            self.event(s, action)
            self.save(db, s)
        return self.snapshot()

    def _place(self, s, order, origin='manual'):
        if order.signal_id in s['signals']:
            return False
        if s['status'] != 'running':
            raise ValueError('Paper engine is not running')
        if order.symbol not in s['settings']['symbols']:
            raise ValueError('Symbol is not enabled')
        current = next((p for p in s['positions'] if p['symbol'] == order.symbol), None)
        if current:
            if current['side'] == order.side:
                raise ValueError('Position already open in this direction')
            current['mark'] = order.price
            self.close(s, current, 'Opposite paper signal')
            self.risk(s)
        cfg = s['settings']
        if s['status'] == 'running':
            margin = cfg['position_value'] / cfg['leverage']
            used = sum(p['margin'] for p in s['positions'])
            denial = ('Maximum positions reached' if len(s['positions']) >= cfg['max_positions'] else
                      'Insufficient paper margin' if self.equity(s) - used < margin else None)
            if denial and not current:
                raise ValueError(denial)
            if denial:
                self.event(s, 'Reversal entry skipped: '+denial)
            else:
                s['positions'].append(dict(symbol=order.symbol, side=order.side, entry=order.price,
                    mark=order.price, qty=cfg['position_value']/order.price, margin=margin,
                    leverage=cfg['leverage'], value=cfg['position_value'], stop_dollars=cfg['stop_dollars'],
                    origin=origin, opened_at=utcnow().isoformat()))
                self.event(s, f'{order.symbol}: {origin} paper {order.side}')
        s['signals'] = (s['signals'] + [order.signal_id])[-10000:]
        return True

    def order(self, order):
        with self.transaction() as db:
            s = self.load(db)
            self.roll_day(s)
            if not self._place(s, order):
                return self._duplicate(s)
            self.save(db, s)
        return self.snapshot()

    def market_cycle(self, cfg, observations):
        """Atomic paper marks + risk + fresh signals, guarded against settings races.

        Each observation contains symbol, latest closed-bar time, current mark,
        and an optional confirmed signal. First observation seeds the cursor.
        """
        from .models import PaperOrder
        import hashlib
        identity = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]
        with self.transaction() as db:
            s = self.load(db)
            if s['settings'] != cfg or not cfg.get('automatic_paper'):
                return
            self.roll_day(s)
            for p in s['positions']:
                observation = observations.get(p['symbol'])
                if observation:
                    p['mark'] = observation['mark']
            self.risk(s)
            cursors = s.setdefault('cursors', {})
            for symbol, observation in observations.items():
                key = identity+':'+symbol
                stamp = observation['time']
                previous = cursors.get(key)
                if previous is not None and stamp <= previous:
                    continue
                cursors[key] = stamp
                if previous is None or not observation.get('signal') or s['status'] != 'running':
                    continue
                try:
                    self._place(s, PaperOrder(symbol=symbol, side=observation['signal'],
                        price=observation['mark'], signal_id=f'{identity}:{symbol}:{stamp}'), origin='automatic')
                except ValueError as exc:
                    self.event(s, f'{symbol} paper signal skipped: {exc}')
            self.save(db, s)

    def _duplicate(self, s):
        return {'duplicate': True, 'status': s['status']}

    def marks(self, prices):
        with self.transaction() as db:
            s = self.load(db)
            self.roll_day(s)
            for p in s['positions']:
                if p['symbol'] in prices:
                    p['mark'] = prices[p['symbol']]
            self.risk(s)
            self.save(db, s)
        return self.snapshot()
