"""Check the generated Linux packages, including their real AppImage launcher."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tarfile
import tempfile
import zlib


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    directory = args.directory.resolve()
    deb, = directory.glob('*.deb')
    appimage, = directory.glob('*.AppImage')
    expected = digest((directory / 'linux-unpacked/resources/app.asar').read_bytes())
    with tarfile.open(fileobj=io.BytesIO(subprocess.check_output([
            'dpkg-deb', '--fsys-tarfile', str(deb)]))) as archive:
        for entry in archive:
            assert entry.uid == 0 and entry.gid == 0, entry.name
            assert not entry.mode & 0o022, entry.name
            if entry.isdir():
                assert entry.mode & 0o055 == 0o055, entry.name
            elif entry.isfile():
                assert entry.mode & 0o044 == 0o044, entry.name
        assert digest(archive.extractfile('./opt/FreeCollab/resources/app.asar').read()) == expected
        assert archive.getmember('./opt/FreeCollab/freecollab').mode & 0o111 == 0o111

    # Read the actual SquashFS root inode rather than relying on the AppImage
    # extractor's deliberately private directory permissions.
    offset = int(subprocess.check_output([str(appimage), '--appimage-offset'], text=True))
    with appimage.open('rb') as image:
        image.seek(offset)
        superblock = image.read(96)
        assert superblock[:4] == b'hsqs'
        assert struct.unpack_from('<H', superblock, 20)[0] == 1, 'Expected gzip SquashFS'
        reference = struct.unpack_from('<Q', superblock, 32)[0]
        inode_table = struct.unpack_from('<Q', superblock, 64)[0]
        image.seek(offset + inode_table + (reference >> 16))
        header, = struct.unpack('<H', image.read(2))
        block = image.read(header & 0x7fff)
        if not header & 0x8000:
            block = zlib.decompress(block)
        root_mode, = struct.unpack_from('<H', block, (reference & 0xffff) + 2)
        assert root_mode & 0o055 == 0o055, oct(root_mode)
        assert not root_mode & 0o022, oct(root_mode)

    with tempfile.TemporaryDirectory(prefix='freecollab-package-proof-') as temporary:
        private = Path(temporary)
        for name in ['AppRun', 'freecollab.desktop', 'resources/app.asar']:
            subprocess.run([str(appimage), '--appimage-extract', name],
                           cwd=private, check=True, stdout=subprocess.DEVNULL)
        extracted = private / 'squashfs-root'
        assert digest((extracted / 'resources/app.asar').read_bytes()) == expected
        launcher = (extracted / 'AppRun').read_bytes()
        assert launcher == (directory / 'linux-unpacked/AppRun').read_bytes()
        assert b'--no-sandbox' not in launcher
        assert '--no-sandbox' not in (extracted / 'freecollab.desktop').read_text()
        # Execute the packed launcher against a harmless binary substitute.
        # All caller arguments must arrive intact, with no implicit flags.
        fixture = private / 'launcher-fixture'
        fixture.mkdir()
        (fixture / 'AppRun').write_bytes(launcher)
        (fixture / 'AppRun').chmod(0o755)
        (fixture / 'freecollab').write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" > "$NATIVE_ARGV_PROOF"\n')
        (fixture / 'freecollab').chmod(0o755)
        capture = private / 'arguments.txt'
        subprocess.run([str(fixture / 'AppRun'), '--proof', 'space value'], check=True,
                       env={**os.environ, 'APPDIR': str(fixture), 'NATIVE_ARGV_PROOF': str(capture)})
        assert capture.read_text().splitlines() == ['--proof', 'space value']
    report = {'deb_runtime_public_permissions': True, 'deb_owned_by_root': True,
              'appimage_root_mode': oct(root_mode), 'asar_sha256': expected,
              'both_installer_asars_match': True, 'packed_appimage_launcher_preserves_arguments': True,
              'automatic_sandbox_bypass_absent': True}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
