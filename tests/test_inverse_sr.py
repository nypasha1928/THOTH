import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from thoth.models import Settings,TestnetSettings
from thoth.runner import Runner
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from thoth.timeframes import interval_seconds
from test_testnet import Exchange

class InverseSRTests(unittest.TestCase):
    def test_confirmation_intervals_exit_and_sources_required(self):
        with self.assertRaisesRegex(ValueError,'Confirm inverse'):Settings(strategy='sr_inverse_intrabar')
        cfg=Settings(strategy='sr_inverse_intrabar',inverse_direction_confirmed=True,sma_exit_enabled=False)
        self.assertEqual(cfg.timeframe,'240');self.assertTrue(cfg.sma_exit_enabled)
        for interval in ('1','5','60'):
            with self.assertRaisesRegex(ValueError,'4-hour or Daily'):Settings(strategy='sr_inverse_intrabar',timeframe=interval,inverse_direction_confirmed=True)
        with self.assertRaisesRegex(ValueError,'level family'):Settings(strategy='sr_inverse_intrabar',inverse_direction_confirmed=True,major=False,minor=False)

    def test_inverse_positions_and_actual_side_minute_exits_survive_restart(self):
        for interval in ('240','D'):
            for raw,price,actual,side in [('Long',102,'Short','Sell'),('Short',98,'Long','Buy')]:
                with self.subTest(interval=interval,raw=raw),tempfile.TemporaryDirectory() as d:
                    duration=interval_seconds(interval);start=duration*100;now=[start+10]
                    exchange=Exchange(lambda:now[0]);path=Path(d)/'account.sqlite'
                    def create():return TestnetSupervisor(path,enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
                    service=create();cfg=TestnetSettings(strategy='sr_inverse_intrabar',timeframe=interval,inverse_direction_confirmed=True,dedicated_account=True,symbols=['BTCUSDT'])
                    service.configure(cfg);service.activate(service.confirmation)
                    adapter=FeedAdapter(service);runner=Runner(adapter)
                    plain=Settings(**{k:v for k,v in cfg.model_dump().items() if k in Settings.model_fields})
                    result=[dict(time=start-duration,levels={'long_resistance_major':dict(value=101,pivot_time=0),'short_support_minor':dict(value=99,pivot_time=0)})]
                    first=runner.intrabar_observation('BTCUSDT',plain,[dict(time=start,close=100)],result,now[0])
                    adapter.market_cycle(plain.model_dump(),{'BTCUSDT':first});self.assertFalse(exchange.orders)
                    now[0]+=5
                    observation=runner.intrabar_observation('BTCUSDT',plain,[dict(time=start,close=price)],result,now[0])
                    self.assertEqual((observation['source_signal'],observation['signal']),(raw,actual))
                    adapter.market_cycle(plain.model_dump(),{'BTCUSDT':observation})
                    order=next(iter(exchange.orders.values()));link=order['orderLinkId']
                    self.assertEqual(order['side'],side)
                    exchange.fill(link);service.monitor()
                    self.assertEqual(service.state['owned']['BTCUSDT']['side'],side)
                    self.assertTrue(service.state['owned']['BTCUSDT']['protection_confirmed'])
                    service=create();service.activate(service.confirmation,recovery=True);now[0]+=6
                    def minute_rows(symbol,timeframe,limit):
                        self.assertEqual(timeframe,'1');minute=int(now[0])//60*60
                        return [dict(time=minute-(50-i)*60,close=100 if i<50 else 101 if actual=='Short' else 99) for i in range(51)]
                    with patch.object(exchange,'candles',side_effect=minute_rows):service.monitor()
                    owner=service.state['owned']['BTCUSDT']
                    self.assertIsNotNone(owner['close_link']);self.assertEqual(owner['exit_timeframe'],'1')
                    exchange.fill(owner['close_link']);service.monitor()
                    trade=service.history()['items'][0]
                    self.assertEqual((trade['strategy'],trade['side'],trade['entry_timeframe'],trade['exit_timeframe']),('sr_inverse_intrabar',actual,interval,'1'))
                    self.assertIn('Inverse S/R',trade['entry_source_label'])
                    restored=Runner(FeedAdapter(service));now[0]+=5
                    self.assertIsNone(restored.intrabar_observation('BTCUSDT',plain,[dict(time=start,close=price)],result,now[0])['signal'])
