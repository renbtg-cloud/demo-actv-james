"""Deterministic arithmetic oracle: true constructed offsets must remain bounded.

The oracle validates interval formulas, not remote clock authenticity.
"""
import random
import sys
import pathlib
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from clock_bounds import audit_clock_probes


def generated_sample(offset_ns,d1,d2,work_ns,base_ns):
    t0=base_ns
    t1=t0+d1+offset_ns
    t2=t1+work_ns
    t3=t0+d1+work_ns+d2
    return {'local_send_ns':t0,'remote_recv_ns':t1,'remote_send_ns':t2,
            'local_recv_ns':t3,'local_elapsed_monotonic_ns':t3-t0}


class ClockBoundPropertyTests(unittest.TestCase):
    def test_512_constructed_offsets_are_inside_conservative_bounds(self):
        rng=random.Random(20261102)
        for i in range(512):
            true_offset=rng.randint(-10_000_000,10_000_000)
            samples=[]
            for j in range(3):
                d1=rng.randint(1_000,250_000)
                d2=rng.randint(1_000,250_000)
                work=rng.randint(1_000,100_000)
                samples.append(generated_sample(true_offset,d1,d2,work,1_900_000_000_000_000_000+j*2_000_000))
            report,errors=audit_clock_probes({'schema_version':1,'run_id':'oracle',
                'reference_id':'math', 'shards':[{'shard_index':k,'samples':samples} for k in (0,1)]},
                'oracle',2,100)
            self.assertFalse(errors,(i,errors))
            self.assertEqual(report['status'],'BOUNDED_SELF_REPORTED_NOT_AUTHENTICATED')
            for bound in report['per_shard']:
                self.assertLessEqual(bound['offset_lower_ns'],true_offset)
                self.assertGreaterEqual(bound['offset_upper_ns'],true_offset)

    def test_conflicting_offset_hypotheses_rejected(self):
        base=1_900_000_000_000_000_000
        samples=[generated_sample(x,20_000,20_000,20_000,base+i*2_000_000)
                 for i,x in enumerate((0,8_000_000,0))]
        result,errors=audit_clock_probes({'schema_version':1,'run_id':'r', 'reference_id':'r',
            'shards':[{'shard_index':i,'samples':samples} for i in (0,1)]},'r',2,100)
        self.assertIsNone(result)
        self.assertTrue(any('Inconsistent offset intervals' in e for e in errors))


if __name__=='__main__':unittest.main()
