"""Focused positive and fail-closed checks for the reviewed Connect SDK artifact."""
from pathlib import Path
import shutil
import tempfile
import unittest

from sdk_artifact_binding import EXPECTED_PACKAGE, verify_sdk_artifact


PACKAGE = Path(__file__).resolve().parents[1]
ARCHIVE = PACKAGE / 'artifacts/freecollab-components-0.13.0.tgz'


class SDKArtifactBindingTests(unittest.TestCase):
    def copy_public_sources(self, destination):
        for name in ('src', 'dist'):
            shutil.copytree(PACKAGE / name, destination / name)
        for name in ('package.json', 'README.md', 'LICENSE', 'COMPATIBILITY.md'):
            shutil.copyfile(PACKAGE / name, destination / name)

    def test_reviewed_archive_matches_every_public_file_and_current_sources(self):
        binding = verify_sdk_artifact(ARCHIVE, PACKAGE)
        self.assertEqual(binding['sha256'], EXPECTED_PACKAGE)
        self.assertEqual(binding['release']['sourceDigest'], binding['source_digest'])
        self.assertGreater(binding['files'], 50)
        self.assertGreater(binding['source_files'], 20)

    def test_unreviewed_archive_is_rejected_before_its_contents_are_used(self):
        with tempfile.TemporaryDirectory(prefix='freecollab-sdk-unreviewed-') as directory:
            changed = Path(directory) / ARCHIVE.name
            changed.write_bytes(ARCHIVE.read_bytes() + b'unreviewed bytes')
            with self.assertRaisesRegex(AssertionError, 'reviewed SHA-256 pin'):
                verify_sdk_artifact(changed, PACKAGE)

    def test_source_edits_cannot_reuse_an_older_reviewed_distribution(self):
        with tempfile.TemporaryDirectory(prefix='freecollab-sdk-source-drift-') as directory:
            package = Path(directory); self.copy_public_sources(package)
            source = package / 'src/types.ts'
            source.write_bytes(source.read_bytes() + b'\n// Source changed after build.\n')
            with self.assertRaisesRegex(AssertionError, 'current source tree'):
                verify_sdk_artifact(ARCHIVE, package)

    def test_distribution_or_readme_edits_cannot_reuse_the_reviewed_archive(self):
        for name in ('dist/index.js', 'README.md'):
            with self.subTest(name=name), tempfile.TemporaryDirectory(prefix='freecollab-sdk-public-drift-') as directory:
                package = Path(directory); self.copy_public_sources(package)
                changed = package / name
                changed.write_bytes(changed.read_bytes() + b'\nChanged public bytes.\n')
                with self.assertRaisesRegex(AssertionError, 'bytes differ'):
                    verify_sdk_artifact(ARCHIVE, package)

    def test_missing_or_added_public_files_cannot_pass_as_the_frozen_distribution(self):
        for added in (False, True):
            with self.subTest(added=added), tempfile.TemporaryDirectory(prefix='freecollab-sdk-file-drift-') as directory:
                package = Path(directory); self.copy_public_sources(package)
                if added:
                    (package / 'dist/unreviewed.js').write_text('export const changed = true;\n')
                else:
                    (package / 'dist/index.js').unlink()
                with self.assertRaisesRegex(AssertionError, 'public files differ'):
                    verify_sdk_artifact(ARCHIVE, package)


if __name__ == '__main__':
    unittest.main(verbosity=2)
