"""Mathematical four-timestamp offset bounds, not a remote NTP test."""
import pathlib
import sys
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from clock_bounds import audit_clock_probes


def sample(offset=2000000,delay=1000000,request=1500000):
    # local A time t0=1e9; B offset +2ms; remote processing 1ms
    a=1000000000
    return dict(local_send_ns=a,remote_recv_ns=a+offset+delay,
       remote_send_ns=a+offset+delay+request,
       local_recv_ns=a+2*delay+request,local_elapsed_monotonic_ns=2*delay+request)

def document():
    return dict(schema_version=1,run_id='r',reference_id='lab-trusted-reference',shards=[
       {'shard_index':i,'samples':[sample() for _ in range(3)]} for i in range(2)])

class ClockTests(unittest.TestCase):
    def test_bounded_offset(self):
        report,errors=audit_clock_probes(document(),'r',2,5)
        self.assertEqual(errors,[])
        self.assertEqual(report['per_shard'][0]['offset_lower_ns'],-500000)
        self.assertEqual(report['per_shard'][0]['offset_upper_ns'],4500000)
        self.assertIsNone(report['cross_host_wall_time_qps'])
    def test_wrong_run(self):
        self.assertTrue(audit_clock_probes(document(),'x',2)[1])
    def test_missing_shard(self):
        a=document();a['shards'].pop();self.assertTrue(audit_clock_probes(a,'r',2)[1])
    def test_wrong_index(self):
        a=document();a['shards'][0]['shard_index']=1;self.assertTrue(audit_clock_probes(a,'r',2)[1])
    def test_negative_network_transit(self):
        a=document();a['shards'][0]['samples'][0]['remote_recv_ns']+=20000000
        self.assertTrue(audit_clock_probes(a,'r',2)[1])
    def test_local_wall_clock_jump(self):
        a=document();a['shards'][0]['samples'][0]['local_recv_ns']+=15000000
        self.assertTrue(audit_clock_probes(a,'r',2)[1])
    def test_strict_width_rejected(self):
        self.assertTrue(audit_clock_probes(document(),'r',2,1)[1])
    def test_mutually_inconsistent_probes(self):
        a=document();a['shards'][0]['samples'][2]=sample(offset=10000000)
        self.assertTrue(audit_clock_probes(a,'r',2)[1])
    def test_noninteger_timestamp(self):
        a=document();a['shards'][0]['samples'][1]['local_send_ns']=True
        self.assertTrue(audit_clock_probes(a,'r',2)[1])
    def test_missing_monotonic_elapsed(self):
        a=document();del a['shards'][0]['samples'][0]['local_elapsed_monotonic_ns']
        self.assertTrue(audit_clock_probes(a,'r',2)[1])

if __name__=='__main__':unittest.main()
