#!/usr/bin/env python3
"""
Diagnostic patch to measure timing of hot path operations.
Add this at the top of exporter.py to track where time is spent.
"""

import time
from collections import defaultdict
from contextlib import contextmanager

class TimingCollector:
    def __init__(self):
        self.timings = defaultdict(list)
        self.counts = defaultdict(int)
    
    @contextmanager
    def measure(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self.timings[name].append(elapsed)
            self.counts[name] += 1
    
    def report(self):
        print("\n" + "="*60)
        print("TIMING DIAGNOSTICS")
        print("="*60)
        for name in sorted(self.timings.keys()):
            times = self.timings[name]
            count = self.counts[name]
            total = sum(times)
            avg = total / count if count > 0 else 0
            print(f"{name:40s}: {count:8d} calls, {total:8.2f}s total, {avg*1000:8.3f}ms avg")
        print("="*60)

# Global singleton
_timing = TimingCollector()

def get_timing_collector():
    return _timing
