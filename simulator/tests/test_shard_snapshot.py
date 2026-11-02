"""Adversarial collector intake: bounded pinned snapshots and durable publication."""
import os
import pathlib
import sys
import unittest
from unittest.mock import patch

SIM=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SIM))
import shard_collection
from shard_collection import _snapshot_archive, collect, _fsync_tree
from test_shard_collection import ShardCollectionTests


class SnapshotTests(ShardCollectionTests):
    """Reuse actual Ed25519 fixtures while exercising new snapshot boundaries."""

    def test_aggregate_budget_rejects_before_staging(self):
        total=sum(a.stat().st_size for a in self.archives)
        with self.assertRaisesRegex(ValueError,'budget'):
            collect(self.archives,self.root/'collected',self.pins,
                 self.session,self.manifest,max_collection_bytes=total-1)
        self.assertFalse((self.root/'collected').exists())
        self.assertEqual(list(self.root.glob('.deroyque-collect-*')),[])

    def test_budget_rejects_bool_and_abusive_max(self):
        for budget in (False, 0, -1, shard_collection.MAX_COLLECTION_BUDGET+1):
            with self.subTest(budget=budget), self.assertRaisesRegex(ValueError,'budget'):
                collect(self.archives,self.root/'collected',self.pins,self.session,self.manifest,
                        max_collection_bytes=budget)

    def test_snapshot_rejects_symlink_to_valid_zip(self):
        symlink=self.root/'zip-link'
        symlink.symlink_to(self.archives[0])
        with self.assertRaises(OSError):
            _snapshot_archive(symlink,self.root/'snapshot',100_000)
        self.assertFalse((self.root/'snapshot').exists())

    def test_snapshot_rejects_fifo_without_blocking(self):
        pipe=self.root/'pipe.zip';os.mkfifo(pipe)
        with self.assertRaises(ValueError):
            _snapshot_archive(pipe,self.root/'snapshot',100_000)

    def test_snapshot_hardlink_refusal(self):
        second=self.root/'hardlink.zip'
        os.link(self.archives[0],second)
        with self.assertRaises(ValueError):
            _snapshot_archive(second,self.root/'snapshot',100_000)

    def test_snapshot_exact_digest_matches_bytes(self):
        import hashlib
        out=self.root/'snapshot'
        size,digest=_snapshot_archive(self.archives[0],out,100_000)
        self.assertEqual(size,len(out.read_bytes()))
        self.assertEqual(digest,hashlib.sha256(out.read_bytes()).hexdigest())
        self.assertEqual(out.stat().st_mode & 0o777,0o600)

    def test_detect_external_archive_swap_after_initial_inspection(self):
        real=shard_collection.inspect_archive
        swapped=False
        def swap(path):
            nonlocal swapped
            result=real(path)
            if not swapped:
                swapped=True
                # Another valid signed shard, but now with a contradictory identity.
                self.archives[0].write_bytes(self.archives[1].read_bytes())
            return result
        with patch.object(shard_collection,'inspect_archive',side_effect=swap):
            with self.assertRaisesRegex(ValueError,'duplicate|changed identity|Unsafe'):
                self.accept()
        self.assertFalse((self.root/'collected').exists())
        self.assertEqual(list(self.root.glob('.deroyque-collect-*')),[])

    def test_snapshot_checksum_reused_in_final_inventory(self):
        import hashlib
        result=self.accept()
        for i,item in enumerate(result['bundles']):
            self.assertEqual(item['source_bundle_sha256'],hashlib.sha256(self.archives[i].read_bytes()).hexdigest())
            self.assertEqual(item['source_bundle_bytes'],self.archives[i].stat().st_size)
        self.assertFalse((self.root/'collected'/'.trusted-snapshots').exists())
        self.assertEqual((self.root/'collected').stat().st_mode & 0o777,0o700)

    def test_simulated_flush_failure_is_fail_closed_and_clean(self):
        with patch.object(shard_collection,'_fsync_tree',side_effect=OSError('injected sync failure')):
            with self.assertRaisesRegex(OSError,'injected sync'):
                self.accept()
        self.assertFalse((self.root/'collected').exists())
        self.assertEqual(list(self.root.glob('.deroyque-collect-*')),[])

    def test_snapshot_over_budget_does_not_create_file(self):
        dest=self.root/'over'
        with self.assertRaisesRegex(ValueError,'over-budget'):
            _snapshot_archive(self.archives[0],dest,1)
        self.assertFalse(dest.exists())


if __name__=='__main__':unittest.main()
