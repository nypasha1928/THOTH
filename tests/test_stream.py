import json
import math
import random
import tempfile
import unittest
from pathlib import Path
from thoth.indicator import evaluate
from thoth.indicator_stream import IndicatorState
from thoth.models import Settings
from thoth.engine import Engine
from thoth.runner import Runner


class StreamTests(unittest.TestCase):
    def fixture(self,n):
        rng=random.Random(42);rows=[]
        for i in range(n):
            c=100+math.sin(i*.2)*10+math.sin(i*.017)*20+rng.uniform(-1,1)
            rows.append(dict(time=i*60,open=c,high=c+1,low=c-1,close=c,volume=1))
        return rows

    def test_pruned_checkpoint_matches_unpruned_replay_after_restart(self):
        rows=self.fixture(6000);cfg=Settings(pivot_long=3,pivot_short=1,sma_length=50)
        expected=evaluate(rows,cfg,now=6000*60)
        state=IndicatorState(cfg);actual=[]
        for i,row in enumerate(rows):
            actual.append(state.step(row))
            if i in (100,3000):state=IndicatorState(cfg,json.loads(json.dumps(state.checkpoint(),allow_nan=False)))
        self.assertEqual(actual,expected)
        self.assertLessEqual(len(state.buffer),51)
        self.assertTrue(all(len(m.arrays[0])<=64 and len(m.arrays[4])<=68 for m in state.machines))

    def test_cached_replay_advances_only_new_closed_rows(self):
        cfg=Settings();rows=self.fixture(350)
        with tempfile.TemporaryDirectory() as d:
            runner=Runner(Engine(Path(d)/'paper.sqlite'))
            history=runner.history('BTCUSDT','1',rows[:300])
            first,count=runner.analysis('BTCUSDT',cfg,history,300*60)
            self.assertEqual(count,300)
            restarted=Runner(Engine(Path(d)/'paper.sqlite'))
            history=restarted.history('BTCUSDT','1',rows)
            actual,count=restarted.analysis('BTCUSDT',cfg,history,350*60)
            self.assertEqual(actual,evaluate(rows,cfg,now=350*60))
            self.assertEqual(count,350)
            again,count=restarted.analysis('BTCUSDT',cfg,history,350*60)
            self.assertEqual(again,actual);self.assertEqual(count,350)
