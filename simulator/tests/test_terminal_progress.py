"""Adversarial and real-thread contract of one-clock terminal observations."""
import pathlib
import random
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
        self.assertEqual(d['convergence_lower_bound_ms'],1)
        self.assertEqual(d['convergence_upper_bound_ms'],3)
        self.assertEqual(d['convergence_bound_width_ms'],2)
        self.assertEqual(d['max_observed_sampling_gap_ms'],1)

    def test_same_poll_first_admission_and_terminal_completion(self):
        t=CompletionTrace(1)
        t.observe('0|0|0',1_000_000)
        t.observe('1|1|0',6_000_000)
        r=t.report()
        self.assertEqual(r['convergence_lower_bound_ms'],0)
        self.assertEqual(r['convergence_upper_bound_ms'],5)
        self.assertEqual(r['observed_convergence_ms'],0)

    def test_slow_sql_round_trip_does_not_invalidate_bounds(self):
        t=CompletionTrace(1)
        # The zero snapshot could occur at 1ms even if we receive it at 5ms;
        # the DONE snapshot could occur as early as 12ms even when read at 20ms.
        t.observe('0|0|0',5_000_000,query_start_ns=1_000_000)
        t.observe('1|0|0',11_000_000,query_start_ns=10_000_000)
        t.observe('1|1|0',20_000_000,query_start_ns=12_000_000)
        r=t.report()
        self.assertEqual(r['convergence_lower_bound_ms'],0)
        self.assertEqual(r['convergence_upper_bound_ms'],19)
        self.assertEqual(r['max_sql_round_trip_ms'],8)

    def test_query_windows_cannot_overlap_or_reverse(self):
        t=CompletionTrace(1)
        with self.assertRaises(ValueError):t.observe('0|0|0',10,query_start_ns=11)
        t.observe('0|0|0',10,query_start_ns=8)
        with self.assertRaises(ValueError):t.observe('1|1|0',20,query_start_ns=9)

    def test_irregular_poll_intervals_disclosed(self):
        t=CompletionTrace(2)
        for ns, row in ((1_000_000,'0|0|0'),(3_000_000,'1|0|0'),
                        (103_000_000,'2|1|0'),(108_000_000,'2|2|0')):
            t.observe(row,ns)
        r=t.report()
        self.assertEqual(r['max_observed_sampling_gap_ms'],100)
        self.assertEqual(r['convergence_lower_bound_ms'],100)
        self.assertEqual(r['convergence_upper_bound_ms'],107)
        self.assertEqual(r['convergence_bound_width_ms'],7)

    def test_randomized_oracle_checks_true_event_duration_inside_bounds(self):
        # Independent synthetic oracle: the observer sees only SQL aggregate
        # counts, while this test alone knows the exact transition instants.
        rng = random.Random(670066)
        under = over = 0
        for experiment in range(600):
            admission = rng.randint(200_000, 1_800_000)
            completion = admission + rng.randint(200, 2_500_000)
            actual_ms = (completion-admission)/1e6
            t=CompletionTrace(1,max_samples=1500)
            end=1000
            for _ in range(1500):
                start=end+rng.randint(1, 25_000)
                end=start+rng.randint(1, 80_000)
                snapshot=rng.randint(start,end)
                counters=('0|0|0' if snapshot<admission else
                          '1|0|0' if snapshot<completion else '1|1|0')
                t.observe(counters,end,query_start_ns=start)
                if t.complete:break
            else:self.fail(f'oracle experiment {experiment} did not finish')
            r=t.report()
            lo=r['convergence_lower_bound_ms']
            hi=r['convergence_upper_bound_ms']
            self.assertLessEqual(lo-0.001,actual_ms)
            self.assertLessEqual(actual_ms,hi+0.001)
            point=r['observed_convergence_ms']
            if point<actual_ms-0.001:under+=1
            if point>actual_ms+0.001:over+=1
        self.assertGreater(under,0,'point observation must sometimes underestimate')
        self.assertGreater(over,0,'point observation must sometimes overestimate')

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
