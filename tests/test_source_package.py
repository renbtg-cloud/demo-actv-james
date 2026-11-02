"""Offline reproducible ZIP and tamper-detection tests using real local Git."""
import pathlib
import json
import stat
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
from package_source import make_archive,verify_archive,verify_against_checkout,verify_reproducible


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
        subprocess.run(['git','-C',str(self.root),'-c','user.email=ci@example.invalid', '-c','user.name=CI', 'commit','-qm','seed'],check=True)
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


    def test_dirty_unstaged_tracked_bytes_refused(self):
        (self.root/'README.md').write_text('uncommitted edit\n')
        with self.assertRaisesRegex(ValueError, 'not committed'):
            make_archive(self.root,self.output)

    def test_dirty_staged_bytes_refused(self):
        (self.root/'README.md').write_text('staged edit\n')
        subprocess.run(['git','-C',str(self.root),'add','README.md'],check=True)
        with self.assertRaisesRegex(ValueError, 'not committed'):
            make_archive(self.root,self.output)

    def test_new_untracked_secret_still_excluded(self):
        (self.root/'new_secret.env').write_text('test-only-secret')
        make_archive(self.root,self.output)
        with zipfile.ZipFile(self.output) as z:
            self.assertNotIn('new_secret.env',z.namelist())

    def test_executable_bit_preserved_and_independently_verified(self):
        script=self.root/'src'/'app.py'
        script.chmod(0o755)
        subprocess.run(['git','-C',str(self.root),'add','src/app.py'],check=True)
        subprocess.run(['git','-C',str(self.root),'-c','user.email=ci@example.invalid','-c','user.name=CI','commit','-qm','make executable'],check=True)
        make_archive(self.root,self.output)
        with zipfile.ZipFile(self.output) as z:
            self.assertEqual(stat.S_IMODE(z.getinfo('src/app.py').external_attr >> 16),0o755)
        self.assertEqual(verify_against_checkout(self.root,self.output)['files'],2)

    def _rewrite_archive(self,mutate):
        original=self.output
        alternate=pathlib.Path(self.tmp.name)/'altered.zip'
        with zipfile.ZipFile(original) as source, zipfile.ZipFile(alternate,'w') as dest:
            for info in source.infolist():
                contents=source.read(info.filename)
                info, contents=mutate(info,contents)
                dest.writestr(info,contents)
        shutil.move(alternate,original)

    def test_forged_permission_bit_rejected(self):
        make_archive(self.root,self.output)
        def mutate(info,data):
            if info.filename=='src/app.py':
                info.external_attr=(stat.S_IFREG|0o755)<<16
            return info,data
        self._rewrite_archive(mutate)
        with self.assertRaisesRegex(ValueError,'mode'):
            verify_archive(self.output)

    def test_zip_symlink_rejected_even_with_valid_payload_hash(self):
        make_archive(self.root,self.output)
        def mutate(info,data):
            if info.filename=='README.md':
                info.external_attr=(stat.S_IFLNK|0o644)<<16
            return info,data
        self._rewrite_archive(mutate)
        with self.assertRaisesRegex(ValueError,'type'):
            verify_archive(self.output)

    def test_zip_bomb_preflight_without_decompression(self):
        # Keep original hashes and contents intact. Add a compressed, huge
        # text member; verifier must reject from headers before reading it.
        make_archive(self.root,self.output)
        import package_source
        with zipfile.ZipFile(self.output,'a',compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr(package_source.member('bomb.txt','100644'),b'X'*1_200_000)
        with self.assertRaisesRegex(ValueError, 'ratio'):
            verify_archive(self.output)

    def test_zip_total_and_member_limit_preflight(self):
        make_archive(self.root,self.output)
        import package_source
        original=package_source.MAX_FILE_SIZE
        try:
            package_source.MAX_FILE_SIZE=8
            with self.assertRaisesRegex(ValueError,'oversized ZIP entry'):
                verify_archive(self.output)
        finally:
            package_source.MAX_FILE_SIZE=original

    def test_canonical_independent_rebuild_succeeds(self):
        make_archive(self.root,self.output)
        self.assertEqual(verify_reproducible(self.root,self.output)['files'],2)

    def test_noncanonical_valid_zip_comment_rejected(self):
        make_archive(self.root,self.output)
        with zipfile.ZipFile(self.output,'a') as z:
            z.comment=b'unsigned extra archive comment'
        # Same source bytes/modes: old independent comparison still green.
        self.assertEqual(verify_against_checkout(self.root,self.output)['files'],2)
        with self.assertRaisesRegex(ValueError,'canonical independent rebuild'):
            verify_reproducible(self.root,self.output)

    def test_unsafe_tracked_newline_name_rejected(self):
        (self.root/'unsafe\nname.txt').write_text('bad manifest text')
        subprocess.run(['git','-C',str(self.root),'add','unsafe\nname.txt'],check=True)
        subprocess.run(['git','-C',str(self.root),'-c','user.email=ci@example.invalid','-c','user.name=CI','commit','-qm','unusual filename'],check=True)
        with self.assertRaisesRegex(ValueError,'unsafe'):
            make_archive(self.root,self.output)

if __name__=='__main__':unittest.main()
