"""Adversarial and real-thread contract of one-clock terminal observations."""
import pathlib
import sys
import threading
import time
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from terminal_progress import count_query, CompletionTrace, CompletionSampler


class TraceTests(unittest.TestCase):
    def test_clean_convergence(self):
        t=CompletionTrace(3)
        for ns, counters in [(1_000_000, '0|0|0'), (2_000_000,'2|1|0'),
                             (3_000_000,'3|2|0'), (4_000_000,'3|3|0')]:
            t.observe(counters, ns)
        d=t.report()
        self.assertEqual(d['expected_keys'],3)
        self.assertEqual(d['observed_convergence_ms'],2)
        self.assertEqual(d['sampling_uncertainty_ms'],1)
        self.assertEqual(d['samples'],4)
        self.assertEqual(d['all_done_observed_ms'],3)

    def test_reject_partial_terminal(self):
        t=CompletionTrace(2)
        t.observe('0|0|0',1);t.observe('2|1|0',2)
        with self.assertRaisesRegex(ValueError,'not actually observed'):t.report()

    def test_reject_unknown_final_state(self):
        t=CompletionTrace(2)
        t.observe('0|0|0',1);t.observe('2|1|1',2)
        with self.assertRaises(ValueError):t.report()

    def test_reject_missing_initial_zero(self):
        t=CompletionTrace(1);t.observe('1|0|0',1);t.observe('1|1|0',2)
        with self.assertRaisesRegex(ValueError,'zero baseline'):t.report()

    def test_fail_closed_counters(self):
        for invalid in ('3|2|0','1|2|0','1|0|2','1|-1|0','True|0|0','1|0|0\n2|2|0',''):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):CompletionTrace(2).observe(invalid,1)

    def test_regression(self):
        for regression in ('0|0|0','2|0|0','2|1|0'):
            t=CompletionTrace(3)
            t.observe('2|1|1',10)
            with self.subTest(regression=regression):
                with self.assertRaises(ValueError):t.observe(regression,11)

    def test_nonmonotonic_clock(self):
        t=CompletionTrace(1);t.observe('0|0|0',100)
        with self.assertRaises(ValueError):t.observe('1|1|0',100)

    def test_sample_budget(self):
        t=CompletionTrace(1,max_samples=2)
        t.observe('0|0|0',1);t.observe('0|0|0',2)
        with self.assertRaises(ValueError):t.observe('1|1|0',3)

    def test_invalid_query_identifiers(self):
        for val in ("foo';DROP TABLE x",'abc.def','ABC','a'*65):
            with self.assertRaises(ValueError):count_query(val,'good')
        for val in ("bad';--",'bad space','a'*49):
            with self.assertRaises(ValueError):count_query('gw_abc',val)
        self.assertIn('gw_abc.p9_requests',count_query('gw_abc','test42'))

    def test_real_background_thread(self):
        counters=['0|0|0','1|0|0','1|1|0']
        lock=threading.Lock()
        def query():
            with lock:
                return counters.pop(0) if len(counters)>1 else counters[0]
        s=CompletionSampler(1,query,interval_seconds=.1)
        try:
            s.start()
            time.sleep(.25)
            d=s.finish()
            self.assertEqual(d['observed_done'],1)
            self.assertGreaterEqual(d['samples'],3)
        finally:s.close()

    def test_background_failure_propagates(self):
        def query():
            query.calls+=1
            if query.calls>1:raise ValueError('bad database counters')
            return '0|0|0'
        query.calls=0
        s=CompletionSampler(1,query,interval_seconds=.1)
        try:
            s.start();time.sleep(.15)
            with self.assertRaisesRegex(ValueError,'bad database'):s.finish()
        finally:s.close()

if __name__=='__main__':unittest.main()
