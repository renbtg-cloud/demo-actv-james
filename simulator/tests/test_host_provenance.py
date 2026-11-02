"""Self-reporting host provenance is an audit aid, not hardware attestation."""
import pathlib
import sys
import unittest
from unittest import mock
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from host_provenance import snapshot, evaluate_provenance, _quota


class HostProvenanceTests(unittest.TestCase):
    def test_same_boot_id_produces_same_run_scoped_hash(self):
        with mock.patch('host_provenance._read',side_effect=lambda path: '1234' if path.endswith('boot_id') else None), mock.patch('host_provenance.os.sched_getaffinity',return_value={0,1}):
            a=snapshot('runA');b=snapshot('runA');c=snapshot('runB')
        self.assertEqual(a['kernel_fingerprint'],b['kernel_fingerprint'])
        self.assertNotEqual(a['kernel_fingerprint'],c['kernel_fingerprint'])
        self.assertNotIn('1234',str(a))
    def test_cpu_quota_validity(self):
        self.assertEqual(_quota('200000 100000'),2.0)
        self.assertIsNone(_quota('max 100000'))
        self.assertIsNone(_quota('0 10000'))
        self.assertIsNone(_quota('100 0'))
        self.assertIsNone(_quota('abc 10'))
    def test_missing_boot_id_is_unknown(self):
        with mock.patch('host_provenance._read',return_value=None):
            data=snapshot('run')
        self.assertIsNone(data['kernel_fingerprint'])
        self.assertEqual(evaluate_provenance([data],'run')['status'],'UNKNOWN_KERNEL_FINGERPRINT_UNAVAILABLE')
    def test_one_shard_cannot_prove_independent_kernels(self):
        data={'schema_version':1,'kernel_fingerprint':'a'*64}
        self.assertEqual(evaluate_provenance([data],'run')['status'],'INSUFFICIENT_SHARDS_FOR_KERNEL_COMPARISON')
    def test_invalid_run_id_rejected(self):
        for bad in ('', 'has space', '../escape', 'x'*50):
            with self.subTest(bad=bad), self.assertRaises(ValueError):snapshot(bad)
    def test_self_reported_distinct_kernels_not_physical_attestation(self):
        report=evaluate_provenance([{'schema_version':1,'kernel_fingerprint':'a'*64},
                                    {'schema_version':1,'kernel_fingerprint':'b'*64}],'run')
        self.assertEqual(report['status'],'DISTINCT_KERNEL_REPORTS_UNVERIFIED')
        self.assertIn('do not prove separate hardware',report['measurement_note'])

if __name__=='__main__':unittest.main()
