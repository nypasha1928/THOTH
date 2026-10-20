"""Process-wide pacing shared by authenticated Bybit clients and workers."""
import threading
import time


class RequestPacer:
    def __init__(self,interval=.2,clock=time.monotonic,sleep=time.sleep):
        self.interval=interval;self.clock=clock;self.sleep=sleep
        self.lock=threading.Lock();self.next_at=0

    def wait(self):
        with self.lock:
            delay=max(0,self.next_at-self.clock())
            if delay:self.sleep(delay)
            self.next_at=self.clock()+self.interval

    def backoff(self,seconds):
        with self.lock:self.next_at=max(self.next_at,self.clock()+seconds)


BYBIT_PACER=RequestPacer()
