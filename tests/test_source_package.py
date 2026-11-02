"""Offline reproducible ZIP and tamper-detection tests using real local Git."""
import pathlib
import subprocess
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
from package_source import make_archive,verify_archive,verify_against_checkout


class SourcePackageTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=pathlib.Path(self.tmp.name)/'repo'
        self.root.mkdir()
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        (self.root/'README.md').write_text('safe tracked source\n')
        (self.root/'src').mkdir()
        (self.root/'src'/'app.py').write_text('print("hello")\n')
        (self.root/'private').mkdir()
        (self.root/'private'/'untracked-token.txt').write_text('DO-NOT-PACKAGE')
        subprocess.run(['git','-C',str(self.root),'add','README.md','src/app.py'],check=True)
        self.output=pathlib.Path(self.tmp.name)/'bundle.zip'

    def test_build_and_verify(self):
        summary=make_archive(self.root,self.output)
        self.assertEqual(summary['files'],2)
        with zipfile.ZipFile(self.output) as archive:
            self.assertNotIn('private/untracked-token.txt',archive.namelist())
            self.assertEqual(archive.read('README.md'),b'safe tracked source\n')

    def test_byte_reproducible_across_rebuilds(self):
        make_archive(self.root,self.output)
        original=self.output.read_bytes()
        make_archive(self.root,self.output)
        self.assertEqual(self.output.read_bytes(),original)

    def test_independent_checkout_exact_match(self):
        make_archive(self.root,self.output)
        self.assertEqual(verify_against_checkout(self.root,self.output)['files'],2)

    def test_second_checkout_detects_changed_tracked_bytes(self):
        make_archive(self.root,self.output)
        (self.root/'README.md').write_text('different source content\n')
        # Archive self-check still passes; independent Git-worktree comparison fails.
        self.assertEqual(verify_archive(self.output)['files'],2)
        with self.assertRaises(ValueError):verify_against_checkout(self.root,self.output)

    def test_tampered_payload_detected(self):
        make_archive(self.root,self.output)
        with zipfile.ZipFile(self.output,'a') as archive:
            archive.writestr('README.md',b'tampered')
        with self.assertRaises(ValueError):verify_archive(self.output)

    def test_unsafe_zip_entry_detected(self):
        make_archive(self.root,self.output)
        with zipfile.ZipFile(self.output,'a') as archive:
            archive.writestr('../outside.txt',b'hack')
        with self.assertRaises(ValueError):verify_archive(self.output)

    def test_worktree_output_path_refused(self):
        with self.assertRaises(ValueError):make_archive(self.root,self.root/'bad.zip')


if __name__=='__main__':unittest.main()
