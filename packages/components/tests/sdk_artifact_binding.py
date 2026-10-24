"""Bind the Connect browser proof to reviewed SDK bytes and their local sources."""
import hashlib
import json
from pathlib import Path
import tarfile


# Reviewed canonical release:artifact bytes, including the optional typed message
# reference API. Rebuild, inspect all packed inputs and review this literal pin
# whenever public package inputs change; a generated manifest cannot approve it.
EXPECTED_PACKAGE = '239838cd9f18d850a31e193079d8e8473bafe6c734e32289994a185805f083c9'


def verify_sdk_artifact(archive: Path, package: Path):
    """Reject stale/unreviewed archives, distribution drift and stale sources.

    Verification reads the archive without extracting it. All public package
    files must match the frozen distribution, and release.sourceDigest must bind
    that distribution to the current SDK source tree.
    """
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == EXPECTED_PACKAGE, 'SDK archive does not match the reviewed SHA-256 pin'
    public = {name: package / name for name in ('package.json', 'README.md', 'LICENSE', 'COMPATIBILITY.md')}
    public.update({str(path.relative_to(package)): path for path in (package / 'dist').rglob('*') if path.is_file()})
    with tarfile.open(archive, 'r:gz') as packed:
        members = packed.getmembers()
        assert all(member.isfile() and member.name.startswith('package/') and '..' not in Path(member.name).parts for member in members), 'SDK archive contains an unsupported path or entry'
        names = [member.name.removeprefix('package/') for member in members]
        assert len(names) == len(set(names)) and set(names) == set(public), 'Packed SDK public files differ from the frozen distribution'
        contents = {name: packed.extractfile(member).read() for name, member in zip(names, members)}
    assert all(contents[name] == path.read_bytes() for name, path in public.items()), 'Packed SDK bytes differ from the frozen distribution'
    release = json.loads(contents['dist/release.json'])
    manifest = json.loads(contents['package.json'])
    assert manifest['name'] == '@freecollab/components' and manifest['version'] == '0.13.0', 'Unexpected SDK package identity'
    assert release['version'] == manifest['version'] and release['apiLine'] == '0.13', 'Unexpected SDK release line'
    sources = [[str(path.relative_to(package / 'src')), hashlib.sha256(path.read_bytes()).hexdigest()]
               for path in sorted((package / 'src').rglob('*')) if path.is_file()]
    source_digest = hashlib.sha256(json.dumps(sources, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    assert release['sourceDigest'] == source_digest, 'Packed SDK release does not bind the current source tree'
    return {'release': release, 'files': len(names), 'source_files': len(sources), 'source_digest': source_digest,
            'sha256': EXPECTED_PACKAGE}
