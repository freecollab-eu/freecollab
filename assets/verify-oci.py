#!/usr/bin/env python3
"""Verify original FreeCollab OCI archives without extracting or loading images.

Verify the GitHub attestation of release-manifest.json separately first. This
stdlib helper checks the byte chain from that manifest to each image, layer,
configuration and embedded SBOM/provenance. It grants no publisher identity.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import tarfile


DIGEST = re.compile(r'sha256:[a-f0-9]{64}\Z')
COMMIT = re.compile(r'[a-f0-9]{40}\Z')
VERSION = re.compile(r'(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?\Z')
INDEX = {'application/vnd.oci.image.index.v1+json', 'application/vnd.docker.distribution.manifest.list.v2+json'}
MANIFEST = {'application/vnd.oci.image.manifest.v1+json', 'application/vnd.docker.distribution.manifest.v2+json'}
JSON_LIMIT = 64 * 1024 * 1024
LAYER_LIMIT = 4 * 1024**3
REPOSITORY = 'freecollab-eu/freecollab'
SOURCE = 'https://github.com/' + REPOSITORY


def require(condition, message):
    if not condition:
        raise ValueError(message)


def hash_stream(stream, limit=None):
    value, size = hashlib.sha256(), 0
    while chunk := stream.read(1024 * 1024):
        size += len(chunk)
        require(limit is None or size <= limit, 'Decompressed layer exceeds verification bound')
        value.update(chunk)
    return 'sha256:' + value.hexdigest(), size


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hash_stream(stream)[0]


class LayerReader:
    def __init__(self, stream):
        self.stream, self.digest, self.size = stream, hashlib.sha256(), 0

    def read(self, size=-1):
        data = self.stream.read(size)
        self.size += len(data)
        require(self.size <= LAYER_LIMIT, 'Decompressed layer exceeds verification bound')
        self.digest.update(data)
        return data


def verify_runtime_layer(stream):
    reader = LayerReader(stream)
    with tarfile.open(fileobj=reader, mode='r|') as layer:
        for member in layer:
            parts = []
            for part in member.name.lstrip('/').split('/'):
                if part in {'', '.'}:
                    continue
                if part == '..':
                    require(bool(parts), 'Runtime layer path traverses outside image root')
                    parts.pop()
                else:
                    parts.append(part)
            name = '/'.join(parts)
            private = name in {'app/.env', 'app/pilot.env', 'app/.npmrc', 'root/.npmrc', 'root/.docker/config.json'}
            private = private or name.startswith(('data/files/', 'data/mail/', 'data/security/', 'root/.ssh/id_'))
            private = private or name.startswith('var/log/nginx/')
            require(not (member.isfile() and member.size and private), 'Runtime image contains populated private configuration/data/log path')
    while reader.read(1024 * 1024):
        pass
    return 'sha256:' + reader.digest.hexdigest(), reader.size


def load_manifest(path, version, commit):
    require(bool(VERSION.fullmatch(version)) and bool(COMMIT.fullmatch(commit)), 'Invalid expected version or source commit')
    value = json.loads(Path(path).read_text())
    require(value.get('format') == 'freecollab-release-v1' and value.get('publication') == 'local-oci', 'Expected an unpublished OCI review manifest')
    require(value.get('repository') == REPOSITORY and value.get('version') == version and value.get('sourceCommit') == commit,
            'Release identity differs from independently verified expectations')
    require(value.get('sourceURL') == SOURCE + '/commit/' + commit, 'Unexpected source URL')
    require(set(value.get('images', {})) == {'backend', 'frontend', 'website'}, 'Expected exactly three release images')
    for component, image in value['images'].items():
        name = 'ghcr.io/' + REPOSITORY + '-' + component
        require(image.get('name') == name and bool(DIGEST.fullmatch(image.get('digest', '')))
                and image.get('reference') == name + '@' + image['digest'], 'Invalid image identity')
    return value


def provenance_matches(statement, commit):
    predicate = statement.get('predicate', {})
    if statement.get('predicateType') == 'https://slsa.dev/provenance/v1':
        request = predicate.get('buildDefinition', {}).get('externalParameters', {}).get('request', {})
    else:
        request = predicate.get('invocation', {}).get('parameters', {})
    args = request.get('root', {}).get('request', {}).get('args', {})
    return args.get('vcs:revision') == commit and args.get('vcs:source') == SOURCE


def verify_archive(path, image, version, commit):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), 'Archive must be a regular local file')
    require(':' not in str(path.resolve()), 'Use an archive path without colon characters for OCI transport portability')
    archive_sha = file_hash(path)
    entries, blob_digests = {}, {}
    with tarfile.open(path, 'r:*') as archive:
        for member in archive:
            name = member.name.removeprefix('./').rstrip('/')
            require(name and not name.startswith('/') and '..' not in name.split('/') and '\\' not in name,
                    'Unsafe archive entry name')
            require(name not in entries and len(entries) < 100000, 'Duplicate entry or archive entry bound exceeded')
            require(member.isdir() or member.isfile(), 'OCI archive must not contain links or special files')
            require(name in {'index.json', 'oci-layout', 'blobs', 'blobs/sha256'}
                    or bool(re.fullmatch(r'blobs/sha256/[a-f0-9]{64}', name)), 'Unexpected OCI archive entry')
            entries[name] = member
            if member.isfile() and name.startswith('blobs/'):
                with archive.extractfile(member) as stream:
                    actual, size = hash_stream(stream)
                require(actual == 'sha256:' + name.rsplit('/', 1)[1] and size == member.size, 'OCI blob digest mismatch')
                blob_digests[actual] = member

        def read_json(member):
            require(member.isfile() and member.size <= JSON_LIMIT, 'Invalid or oversized OCI JSON')
            with archive.extractfile(member) as stream:
                return json.load(stream)

        require('index.json' in entries and 'oci-layout' in entries, 'Missing OCI layout metadata')
        require(read_json(entries['oci-layout']).get('imageLayoutVersion') == '1.0.0', 'Unsupported OCI layout')
        root = read_json(entries['index.json'])
        require(root.get('schemaVersion') == 2 and root.get('manifests'), 'Invalid OCI root index')
        require(all(entry.get('digest') == image['digest'] for entry in root['manifests']), 'Root OCI index differs from signed release digest')
        refs = sorted({entry.get('annotations', {}).get('org.opencontainers.image.ref.name', '') for entry in root['manifests']} - {''})
        require(len(root['manifests']) == 1 or bool(refs), 'Multiple root descriptors require a named OCI reference')

        referenced = set()

        def blob(descriptor):
            digest = descriptor.get('digest', '')
            require(bool(DIGEST.fullmatch(digest)) and digest in blob_digests, 'Referenced OCI blob is missing')
            require(type(descriptor.get('size')) is int and descriptor['size'] == blob_digests[digest].size, 'OCI descriptor size mismatch')
            require(not descriptor.get('urls'), 'Offline archive must not need external descriptor URLs')
            referenced.add(digest)
            return blob_digests[digest]

        image_manifests, attestations, visited = {}, [], set()

        def walk(descriptor, depth=0):
            require(depth <= 8, 'OCI manifest nesting bound exceeded')
            member = blob(descriptor)
            if descriptor['digest'] in visited:
                return
            visited.add(descriptor['digest'])
            value = read_json(member)
            media = descriptor.get('mediaType')
            require(value.get('schemaVersion') == 2 and value.get('mediaType') == media, 'OCI media type mismatch')
            if media in INDEX:
                require(isinstance(value.get('manifests'), list), 'Invalid OCI image index')
                for child in value['manifests']:
                    walk(child, depth + 1)
                return
            require(media in MANIFEST, 'Unsupported OCI manifest media type')
            config = read_json(blob(value['config']))
            for layer in value.get('layers', []):
                blob(layer)
            platform = descriptor.get('platform', {})
            annotations = descriptor.get('annotations', {})
            if annotations.get('vnd.docker.reference.type') == 'attestation-manifest':
                require(platform.get('os') == platform.get('architecture') == 'unknown', 'Unexpected attestation platform')
                target = annotations.get('vnd.docker.reference.digest')
                require(bool(DIGEST.fullmatch(target or '')), 'Attestation must bind a runtime manifest')
                statements = []
                for layer in value['layers']:
                    require(layer.get('mediaType') == 'application/vnd.in-toto+json', 'Unexpected attestation layer')
                    statement = read_json(blob(layer))
                    require(any(subject.get('digest', {}).get('sha256') == target.removeprefix('sha256:')
                                for subject in statement.get('subject', [])), 'Attestation subject differs from runtime manifest')
                    statements.append(statement)
                attestations.append((target, statements))
                return
            arch = config.get('architecture')
            require(config.get('os') == 'linux' and arch in {'amd64', 'arm64'}
                    and platform.get('os') == 'linux' and platform.get('architecture') == arch,
                    'Unexpected runtime image platform')
            require(arch not in image_manifests, 'Duplicate runtime architecture')
            labels = config.get('config', {}).get('Labels', {})
            require(labels.get('org.opencontainers.image.revision') == commit
                    and labels.get('org.opencontainers.image.source') == SOURCE
                    and labels.get('org.opencontainers.image.version') == version, 'Baked runtime source/version labels differ')
            forbidden = {'JWT_SECRET', 'ACCOUNT_SECURITY_KEY', 'MESSAGE_ENCRYPTION_KEYS', 'POSTGRES_PASSWORD',
                         'OIDC_CLIENT_SECRET', 'SMTP_PASSWORD', 'S3_SECRET_ACCESS_KEY', 'NATIVE_PUSH_APNS_PRIVATE_KEY'}
            require(not any(entry.split('=', 1)[0] in forbidden and entry.partition('=')[2]
                            for entry in config.get('config', {}).get('Env', [])), 'Image contains a baked runtime credential')
            diff_ids = config.get('rootfs', {}).get('diff_ids', [])
            require(config.get('rootfs', {}).get('type') == 'layers' and len(diff_ids) == len(value.get('layers', [])), 'Runtime rootfs layer count differs')
            unpacked_bytes = 0
            for layer, expected in zip(value['layers'], diff_ids):
                require(bool(DIGEST.fullmatch(expected)), 'Invalid runtime diff ID')
                member = blob(layer)
                media_type = layer['mediaType']
                with archive.extractfile(member) as compressed:
                    if media_type.endswith('+gzip') or media_type.endswith('.gzip'):
                        with gzip.GzipFile(fileobj=compressed) as stream:
                            actual, size = verify_runtime_layer(stream)
                    else:
                        require(media_type.endswith('.tar'), 'Unsupported layer compression; expected gzip or plain tar')
                        actual, size = verify_runtime_layer(compressed)
                require(actual == expected, 'Uncompressed runtime layer differs from config diff ID')
                unpacked_bytes += size
                require(unpacked_bytes <= 16 * 1024**3, 'Runtime rootfs verification bound exceeded')
            image_manifests[arch] = {'runtime_manifest_digest': descriptor['digest'], 'local_image_config_id': value['config']['digest'],
                                     'runtime_layer_count': len(diff_ids), 'verified_uncompressed_layer_bytes': unpacked_bytes}

        for descriptor in root['manifests']:
            walk(descriptor)
        require(set(image_manifests) == {'amd64', 'arm64'}, 'Both Linux architectures are required')
        for arch, runtime in image_manifests.items():
            statements = [statement for target, values in attestations if target == runtime['runtime_manifest_digest'] for statement in values]
            provenance = [value for value in statements if value.get('predicateType') in {'https://slsa.dev/provenance/v0.2', 'https://slsa.dev/provenance/v1'}]
            sbom = [value for value in statements if value.get('predicateType') == 'https://spdx.dev/Document']
            require(provenance and sbom, 'Missing architecture-specific SLSA provenance or SPDX SBOM')
            require(any(provenance_matches(value, commit) for value in provenance),
                    'Embedded BuildKit provenance differs from expected source repository or commit')
            runtime['embedded_slsa_and_spdx_present'] = True
        require(all(target in {value['runtime_manifest_digest'] for value in image_manifests.values()} for target, _ in attestations), 'Orphan attestation manifest')
        require(set(blob_digests) == referenced, 'Archive contains a blob outside the verified release graph')
    return {'archive': path.name, 'archive_sha256': archive_sha, 'archive_bytes': path.stat().st_size,
            'oci_index_digest': image['digest'], 'all_blob_count': len(blob_digests),
            'all_compressed_blob_digests_and_uncompressed_runtime_diff_ids_verified': True,
            'known_private_runtime_configuration_data_and_nginx_log_paths_empty': True,
            'top_level_reference_names': refs,
            'source_for_skopeo': 'oci-archive:' + str(path.resolve()) + (':' + refs[0] if refs else ''),
            'platforms': image_manifests}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--commit', required=True)
    for name in ('backend', 'frontend', 'website'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--arch', choices=['amd64', 'arm64'])
    parser.add_argument('--env-output', type=Path)
    args = parser.parse_args()
    manifest = load_manifest(args.manifest, args.version, args.commit)
    result = {'version': args.version, 'sourceCommit': args.commit, 'release_manifest_sha256': file_hash(args.manifest),
              'publisher_identity': 'Not established by this helper; independently verify the GitHub attestation of release-manifest.json first.',
              'scope': 'Original archive byte verification only; no extraction, image load, daemon mutation or registry publication.', 'images': {}}
    for name, image in manifest['images'].items():
        result['images'][name] = verify_archive(getattr(args, name), image, args.version, args.commit)
    if args.env_output:
        require(args.arch is not None, '--env-output requires --arch')
        with args.env_output.open('x') as output:
            output.write('# Local Docker config IDs from verified OCI archives; no registry publication claimed.\n')
            for name in ('backend', 'frontend', 'website'):
                output.write('FREECOLLAB_' + name.upper() + '_IMAGE=' + result['images'][name]['platforms'][args.arch]['local_image_config_id'] + '\n')
    with args.report.open('x') as output:
        json.dump(result, output, indent=2)
        output.write('\n')
    print(json.dumps({'verified': True, 'sourceCommit': args.commit, 'report': str(args.report)}))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, gzip.BadGzipFile) as failure:
        raise SystemExit('OCI verification failed: ' + str(failure))
