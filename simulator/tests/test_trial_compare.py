"""Negative controls for comparative analysis of independent audited trials."""
import copy
import pathlib
import sys
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from trial_compare import compare


def sample(i, rate=25.0):
    return {'outcome':'PASS','run_id':f'run-{i}', 'workload_sha256':'d'*64,
      'global_keys':4,'global_requested_posts':12,'global_recorded_posts':12,
      'gateway_witness':{'scope':'one_gateway_clock_across_backends', 'backend_count':2,
        'total_http_responses':12,'gateway_completed_post_qps':rate,
        'gateway_window_seconds':12/rate},
      'verified_states':{'DONE':4},'harness_errors':[], 'global_invariant_violations':[],
      'state_verification_errors':[],'unexpected_http_or_transport':0,
      'host_evidence':{'status':'SAME_KERNEL_OBSERVED'}}

class TrialTests(unittest.TestCase):
    def setUp(self):self.rows=[sample(i,rate) for i,rate in enumerate((20,30,25,28,24))]
    def test_five_trial_median_mad(self):
        result=compare(self.rows)
        self.assertEqual(result['outcome'],'DESCRIPTIVE_ONLY')
        self.assertEqual(result['trial_count'],5)
        self.assertEqual(result['gateway_completed_post_qps']['median'],25)
        self.assertEqual(result['gateway_completed_post_qps']['median_absolute_deviation'],3)
    def test_fewer_than_three_rejected(self):
        with self.assertRaises(ValueError):compare(self.rows[:2])
    def test_duplicate_run_rejected(self):
        self.rows[1]['run_id']=self.rows[0]['run_id']
        with self.assertRaises(ValueError):compare(self.rows)
    def test_mismatched_workload_rejected(self):
        self.rows[1]['workload_sha256']='a'*64
        with self.assertRaises(ValueError):compare(self.rows)
    def test_absent_or_forged_gateway_proof_rejected(self):
        for mutation in ('remove','rate','count'):
            rows=copy.deepcopy(self.rows)
            if mutation=='remove':rows[0]['gateway_witness']=None
            if mutation=='rate':rows[0]['gateway_witness']['gateway_completed_post_qps']=float('inf')
            if mutation=='count':rows[0]['gateway_witness']['total_http_responses']=7
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):compare(rows)
    def test_incomplete_effect_evidence_rejected(self):
        self.rows[0]['verified_states']={'READY':4}
        with self.assertRaises(ValueError):compare(self.rows)
    def test_different_backend_count_rejected(self):
        self.rows[0]['gateway_witness']['backend_count']=3
        with self.assertRaises(ValueError):compare(self.rows)
    def test_different_kernel_classification_rejected(self):
        self.rows[0]['host_evidence']['status']='DISTINCT_KERNEL_REPORTS_UNVERIFIED'
        with self.assertRaises(ValueError):compare(self.rows)
    def test_failed_prior_audit_rejected(self):
        self.rows[0]['outcome']='FAIL'
        with self.assertRaises(ValueError):compare(self.rows)
    def test_wrong_stat_types_rejected(self):
        self.rows[0]['gateway_witness']['gateway_completed_post_qps']=True
        with self.assertRaises(ValueError):compare(self.rows)

if __name__=='__main__':unittest.main()
