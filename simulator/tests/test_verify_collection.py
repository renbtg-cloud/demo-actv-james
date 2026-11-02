"""Offline re-verification of published bytes and optional source ZIPs."""
import json
import pathlib
import sys
import tempfile
import unittest
SIM=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SIM))
from test_shard_collection import ShardCollectionTests
from verify_collection import verify_collection

class VerifyTests(ShardCollectionTests):
    def test_valid_published_bytes_and_original_shards_in_reverse_order(self):
        self.accept()
        result=verify_collection(self.root/'collected',self.pins,self.session,
                                 self.manifest,self.archives[::-1])
        self.assertEqual(result['shards'],2)
        self.assertTrue(result['original_bundles_checked'])

    def test_valid_without_archives_does_not_claim_transport_recheck(self):
        self.accept()
        result=verify_collection(self.root/'collected',self.pins,self.session,self.manifest)
        self.assertFalse(result['original_bundles_checked'])

    def test_reject_altered_published_signed_agent(self):
        self.accept()
        (self.root/'collected'/'shard-000'/'agent-000.tsv').write_text('forged')
        with self.assertRaisesRegex(ValueError,'signature verification'):
            verify_collection(self.root/'collected',self.pins,self.session,self.manifest)

    def test_reject_tampered_result_claim(self):
        self.accept()
        path=self.root/'collected'/'collection.json'
        x=json.loads(path.read_text());x['host_evidence']['status']='DISTINCT_KERNEL_REPORTS_UNVERIFIED'
        path.write_text(json.dumps(x))
        with self.assertRaisesRegex(ValueError,'provenance'):
            verify_collection(self.root/'collected',self.pins,self.session,self.manifest)

    def test_reject_extra_collected_member(self):
        self.accept()
        (self.root/'collected'/'shard-000'/'extra.txt').write_text('surprise')
        with self.assertRaisesRegex(ValueError,'Unexpected'):
            verify_collection(self.root/'collected',self.pins,self.session,self.manifest)

    def test_reject_replaced_original_zip(self):
        self.accept()
        self.archives[0].write_bytes(self.archives[1].read_bytes())
        with self.assertRaisesRegex(ValueError,'duplicated'):
            verify_collection(self.root/'collected',self.pins,self.session,self.manifest,self.archives)

    def test_reject_missing_original_zip(self):
        self.accept()
        with self.assertRaisesRegex(ValueError,'count mismatch'):
            verify_collection(self.root/'collected',self.pins,self.session,self.manifest,[self.archives[0]])

if __name__=='__main__':unittest.main()
