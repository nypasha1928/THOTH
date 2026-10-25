"""Uploaded zone formulas, explicit SMA scope, rollback and causal replay."""
import hashlib,json,random,unittest
from pathlib import Path
from thoth.order_block import OrderBlockState,select,reference
from thoth.models import Settings,OrderBlockSettings


def formation_rows():
    rows=[dict(time=i*300,open=200.,close=200.,high=201.,low=199.,volume=10) for i in range(198)]
    rows[-1].update(open=100,close=101,high=102,low=99)
    rows += [dict(time=198*300,open=105,close=100,high=106,low=98,volume=10),
             dict(time=199*300,open=99,close=108,high=109,low=97,volume=10)]
    return rows


def mirror(row):return {**row,'open':1000-row['open'],'close':1000-row['close'],'high':1000-row['low'],'low':1000-row['high']}

class OrderBlockTests(unittest.TestCase):
    def test_uploaded_bytes_preserved(self):
        path=Path(__file__).resolve().parents[1]/'reference/order_block_salem_uploaded.pine'
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),'e2fcbf0221f92fc175ead52f569c86d063c4f43d7588048c031c2e0934af03d5')

    def test_new_profile_normalizes_five_minutes_and_keeps_no_other_filters(self):
        cfg=Settings(strategy='sr_order_block_gate',timeframe='1',sma_exit_enabled=True,sma_enabled=True,
            sma_7_50_filter_enabled=True,sma_length=50,loss_streak_scope='account',order_block={'filter_breaks':False})
        self.assertEqual(cfg.timeframe,'5');self.assertEqual(cfg.sma_length,200);self.assertFalse(cfg.sma_exit_enabled)
        self.assertFalse(cfg.sma_enabled or cfg.sma_7_50_filter_enabled)
        self.assertTrue(cfg.order_block.filter_breaks);self.assertEqual(cfg.loss_streak_scope,'per_coin')
        with self.assertRaises(ValueError):Settings(strategy='sr_order_block_gate',major=False,minor=False)
        with self.assertRaises(ValueError):Settings(strategy='sr_order_block_gate',order_block=dict(bullish_formed=False,bearish_formed=False,supply_breakout=False,demand_breakdown=False))
        self.assertEqual(Settings(strategy='sr_rsi_volume_gate').timeframe,'1')

    def test_formation_pivot_offsets_zone_bounds_and_inverted_sma(self):
        for rows,kind,zone in ((formation_rows(),'bullish_formed','demand'),([mirror(r) for r in formation_rows()],'bearish_formed','supply')):
            state=OrderBlockState()
            for row in rows:result=state.step(row)
            self.assertIn(kind,result['events'])
            actual=result['zones'][zone][0]
            expected=(106,97) if kind=='bullish_formed' else (903,894)
            self.assertEqual((actual['high'],actual['low']),expected)
            self.assertEqual(actual['time'],198*300)
            self.assertEqual(select(result,OrderBlockSettings())['signal'],'Long' if kind=='bullish_formed' else 'Short')

    def test_sma200_rule_by_event_type_above_below_and_equal(self):
        kinds=['bullish_formed','bearish_formed','supply_breakout','demand_breakdown']
        for close,expected in ((99,['bullish_formed','demand_breakdown']),(101,['bearish_formed','supply_breakout']),(100,[])):
            out=select(dict(events=kinds,sma200=100,close=close,ready=True),OrderBlockSettings())
            self.assertEqual([e['kind'] for e in out['events']],expected)
            self.assertTrue(out['conflict'] if expected else not out['conflict'])
            self.assertIsNone(out['signal'])  # Opposing qualifying events stay visible, no arbitrary trade.
        self.assertFalse(select(dict(events=kinds,sma200=None,close=99,ready=False),OrderBlockSettings())['events'])

    def seed(self):
        state=OrderBlockState()
        for i in range(200):state.step(dict(time=i*300,open=100,close=100,high=101,low=99,volume=10))
        return state

    def test_breaks_are_strict_wicks_before_new_zone_and_head_only(self):
        state=self.seed();state.s['sell']=[dict(high=105,low=103,time=0,bounced=False),dict(high=102,low=101,time=300,bounced=False)]
        equal=OrderBlockState(state.checkpoint()).step(dict(time=60000,open=100,close=100,high=105,low=99,volume=10))
        self.assertNotIn('supply_breakout',equal['events'])  # Even though older head-behind zone is crossed.
        broken=OrderBlockState(state.checkpoint()).step(dict(time=60000,open=104,close=104,high=106,low=99,volume=10))
        self.assertIn('supply_breakout',broken['events']);self.assertEqual(len(broken['broken_supply']),2)
        self.assertEqual(select(broken,OrderBlockSettings())['signal'],'Long')
        mirrored=OrderBlockState()
        for i in range(200):mirrored.step(dict(time=i*300,open=900,close=900,high=901,low=899,volume=10))
        mirrored.s['buy']=[dict(high=897,low=895,time=0,bounced=False)]
        broken=mirrored.step(dict(time=60000,open=896,close=896,high=901,low=894,volume=10))
        self.assertIn('demand_breakdown',broken['events']);self.assertEqual(select(broken,OrderBlockSettings())['signal'],'Short')

    def test_overlap_first_bounce_then_replace_preserves_pine_mutation(self):
        for bounced in (False,True):
            rows=formation_rows();state=OrderBlockState()
            for row in rows[:-1]:state.step(row)
            state.s['buy']=[dict(high=110,low=90,time=0,bounced=bounced)]
            state.step(rows[-1])
            self.assertEqual(len(state.s['buy']),1 if bounced else 2)
            if not bounced:self.assertEqual(state.s['buy'][1],dict(high=97,low=90,time=0,bounced=True))

    def test_checkpoint_mirror_symmetry_and_full_incremental_replay(self):
        rng=random.Random(42);rows=[];price=200
        for i in range(800):
            op=price+rng.randint(-2,2);price=max(40,min(400,op+rng.randint(-5,5)))
            rows.append(dict(time=i*300,open=op,close=price,high=max(op,price)+rng.randint(1,4),low=min(op,price)-rng.randint(1,4),volume=10))
        original=OrderBlockState();inverse=OrderBlockState();seen=set();swap={'bullish_formed':'bearish_formed','bearish_formed':'bullish_formed','supply_breakout':'demand_breakdown','demand_breakdown':'supply_breakout'}
        out=[]
        for i,row in enumerate(rows):
            if i%37==0:original=OrderBlockState(json.loads(json.dumps(original.checkpoint())))
            a=original.step(row);b=inverse.step(mirror(row));out.append(select(a,OrderBlockSettings()))
            self.assertEqual({swap[k] for k in a['events']},set(b['events']));seen.update(a['events'])
        self.assertEqual(seen,set(swap))
        self.assertEqual(out,reference(rows,OrderBlockSettings(),rows[-1]['time']+300))

    def test_preview_rolls_back_zones_and_atr(self):
        rows=formation_rows();state=OrderBlockState()
        for row in rows[:-1]:state.step(row)
        before=state.checkpoint();preview=OrderBlockState(before).step(rows[-1])
        self.assertIn('bullish_formed',preview['events']);self.assertEqual(state.checkpoint(),before)
        quiet={**rows[-1],'close':99,'high':100}
        self.assertNotIn('bullish_formed',OrderBlockState(before).step(quiet)['events'])
        self.assertEqual(state.checkpoint(),before)
