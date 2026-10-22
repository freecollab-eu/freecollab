#!/usr/bin/env python3
"""Release-boundary regressions; no registry access or publication."""
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess

spec = importlib.util.spec_from_file_location("release", Path(__file__).with_name("release.py"))
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.manifest = release.container_manifest("freecollab-eu/freecollab", "0.13.0", "a" * 40,
                                                   "sha256:" + "b" * 64, "sha256:" + "c" * 64, published=True)

    def test_pins_select_two_exact_digests_and_no_mutable_tags(self):
        value = release.pins(self.manifest)
        self.assertIn("FREECOLLAB_BACKEND_IMAGE=ghcr.io/freecollab-eu/freecollab-backend@sha256:" + "b" * 64, value)
        self.assertIn("FREECOLLAB_FRONTEND_IMAGE=ghcr.io/freecollab-eu/freecollab-frontend@sha256:" + "c" * 64, value)
        self.assertNotIn(":latest", value)

    def test_altered_manifest_cannot_redirect_to_another_registry_or_repository(self):
        for key, replacement in (("reference", "evil.example/image@sha256:" + "b" * 64),
                                 ("name", "ghcr.io/freecollab-eu/other-backend"),
                                 ("digest", "sha256:" + "d" * 64)):
            manifest = copy.deepcopy(self.manifest)
            manifest["images"]["backend"][key] = replacement
            with self.assertRaises(ValueError):
                release.pins(manifest)

    def test_dotenv_and_reference_injection_is_rejected(self):
        for field, value in (("repository", "freecollab-eu/freecollab\nJWT_SECRET=evil"),
                             ("version", "0.13.0\nPUBLIC_APP_URL=evil"),
                             ("sourceCommit", "$(malicious-command)")):
            manifest = copy.deepcopy(self.manifest)
            manifest[field] = value
            with self.assertRaises(ValueError):
                release.pins(manifest)

    def test_existing_operator_files_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "images.env"
            target.write_text("original operator selection\n")
            with self.assertRaises(FileExistsError):
                release.write_new(target, release.pins(self.manifest))
            self.assertEqual(target.read_text(), "original operator selection\n")

    def test_local_review_archive_cannot_masquerade_as_published_installation(self):
        self.manifest['publication'] = 'local-oci'
        with self.assertRaises(ValueError):
            release.pins(self.manifest)

    def test_sdk_requires_both_hashes_and_exact_local_artifact_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = b"private fixture archive bytes"
            archive = root / "freecollab-components-0.13.0.tgz"
            archive.write_bytes(data)
            manifest = {"name": "@freecollab/components", "version": "0.13.0", "artifact": archive.name,
                        "publishTag": "next", "registry": "https://registry.npmjs.org/",
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "integrity": "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()}
            file = root / "release-manifest.json"
            file.write_text(json.dumps(manifest))
            self.assertEqual(release.validate_sdk(root, "0.13.0")["sha256"], manifest["sha256"])
            archive.write_bytes(data + b"modified")
            with self.assertRaises(ValueError):
                release.validate_sdk(root, "0.13.0")
            archive.write_bytes(data)
            manifest["artifact"] = "../freecollab-components-0.13.0.tgz"
            file.write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                release.validate_sdk(root, "0.13.0")

    def test_publication_requires_successful_ci_for_the_exact_source_commit(self):
        with patch.object(release.subprocess, "check_output") as command:
            for runs in ([], [{"head_sha": "d" * 40, "conclusion": "success", "event": "push"}],
                         [{"head_sha": "a" * 40, "conclusion": "failure", "event": "push"}]):
                command.return_value = json.dumps({"workflow_runs": runs})
                with self.assertRaises(ValueError):
                    release.require_successful_ci(Path('.'), "freecollab-eu/freecollab", "a" * 40)
            command.return_value = json.dumps({"workflow_runs": [{"head_sha": "a" * 40, "conclusion": "success", "event": "push"}]})
            release.require_successful_ci(Path('.'), "freecollab-eu/freecollab", "a" * 40)

    def test_unused_tag_check_fails_closed_on_authentication_network_or_existing_tag(self):
        ref = 'ghcr.io/freecollab-eu/freecollab-backend:v0.13.0'
        with patch.object(release.subprocess, "run") as command:
            for code, stderr in ((0, ''), (1, '401 Unauthorized'), (1, 'TLS certificate verification failed'),
                                 (1, 'dial tcp: no such host'), (1, '429 Too Many Requests')):
                command.return_value = subprocess.CompletedProcess([], code, '', stderr)
                with self.assertRaises(ValueError):
                    release.require_unused_image(ref)
            command.return_value = subprocess.CompletedProcess([], 1, '', 'ERROR: manifest unknown')
            release.require_unused_image(ref)

    def test_asset_manifest_excludes_unpacked_binaries_and_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'FreeCollab-0.13.0.exe').write_bytes(b'packaged installer fixture')
            (root/'win-unpacked').mkdir()
            (root/'win-unpacked/freecollab.exe').write_bytes(b'unpacked binary fixture')
            result = release.asset_manifest(root, '0.13.0', 'a'*40, 'windows-x64', 'unsigned')
            self.assertEqual([row['path'] for row in result['assets']], ['FreeCollab-0.13.0.exe'])
            (root/'linked.exe').symlink_to(root/'FreeCollab-0.13.0.exe')
            with self.assertRaises(ValueError):
                release.asset_manifest(root, '0.13.0', 'a'*40, 'windows-x64', 'unsigned')


if __name__ == "__main__":
    unittest.main()
