#!/usr/bin/env python3
"""Benchmarker self-tests; do not start PostgreSQL or any JVM."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('metrics_ab',ROOT/'scripts'/'benchmark-metrics-ab.py')
ab=importlib.util.module_from_spec(spec)
spec.loader.exec_module(ab)

class BenchmarkContract(unittest.TestCase):
    def test_order_counterbalances(self):
        self.assertEqual(ab.ORDER,('OFF','ON','ON','OFF'))

    def test_default_is_safe_and_explicit(self):
        a=ab.parse_args(['--output-dir','/tmp/benchmark-out'])
        self.assertEqual((a.jvms,a.requests,a.keys,a.rate),(6,240,80,65))
        self.assertNotEqual(a.api_port,a.provider_port)

    def test_bad_request_budget_rejected(self):
        with self.assertRaises(SystemExit):
            ab.parse_args(['--output-dir','/tmp/a','--requests','2','--jvms','6'])

    def test_no_duplicate_output_directory(self):
        with tempfile.TemporaryDirectory() as d:
            a=ab.parse_args(['--output-dir',d,'--private-dir',d+'/secret'])
            with self.assertRaises(FileExistsError):ab.main(['--output-dir',d,'--private-dir',d+'/secret'])

    def test_cpu_and_rss_from_current_process(self):
        p=ab.proc_info(os.getpid())
        self.assertIsNotNone(p)
        self.assertGreaterEqual(p['cpu_seconds'],0)
        self.assertGreater(p['rss_bytes'],0)

    def test_comparison_reports_delta_and_none_when_unavailable(self):
        samples=[{'mode':'OFF','client_qps':40,'client_p95_ms':20,'schedule_lag_p95_ms':5,
                  'service_cpu_seconds_during_load':2,'cgroup_cpu_seconds_during_load':4},
                 {'mode':'ON','client_qps':38,'client_p95_ms':25,'schedule_lag_p95_ms':6,
                  'service_cpu_seconds_during_load':3,'cgroup_cpu_seconds_during_load':5},
                 {'mode':'ON','client_qps':36,'client_p95_ms':35,'schedule_lag_p95_ms':7,
                  'service_cpu_seconds_during_load':3,'cgroup_cpu_seconds_during_load':5},
                 {'mode':'OFF','client_qps':40,'client_p95_ms':20,'schedule_lag_p95_ms':5,
                  'service_cpu_seconds_during_load':2,'cgroup_cpu_seconds_during_load':4}]
        diff=ab.compare(samples)
        self.assertEqual(diff['client_qps']['off_mean'],40)
        self.assertEqual(diff['client_qps']['on_mean'],37)
        self.assertEqual(diff['client_qps']['relative_percent'],-7.5)
        for i,item in enumerate(samples,1):
            item.update(trial=i,peak_jvms_http=6,posts=240,unexpected=0)
        with tempfile.TemporaryDirectory() as d:
            ab.write_report(Path(d),samples,diff)
            report=(Path(d)/'COMPARISON.md').read_text()
            self.assertIn('not a statistically established causal overhead estimate',report)

    def test_cleanup_only_own_controller(self):
        p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
        try:ab.cleanup(p);self.assertIsNotNone(p.poll())
        finally:
            if p.poll() is None:p.kill();p.wait()

    def test_cgroup_cpu_is_monotonic_if_available(self):
        a=ab.cgroup_cpu();b=ab.cgroup_cpu()
        if a is not None and b is not None:self.assertGreaterEqual(b,a)

if __name__=='__main__':unittest.main()
