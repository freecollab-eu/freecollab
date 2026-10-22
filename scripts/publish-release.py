#!/usr/bin/env python3
"""Publish only the already reviewed FreeCollab draft with GitHub's job token.

--check-local never reads a token, opens a network client, or mutates releases.
No token or signed download URL is written to disk or logged.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

REPO = 'freecollab-eu/freecollab'
FINAL = '0927e8e258819250d63e27cf0b514e09227960e9'
BUILT = 'cdecb4bc97562e8027b536e88e451f5a8be7b8d4'
TAG_OBJECT = 'b375be7dcbb4b3766f3b9903e159bc843f8477bd'
BRANCH = 'release-0.13.0-publication'
DRAFT = 420560024
OCI_NAMES = {f'freecollab-{name}-0.13.0.oci.tar' for name in ('backend', 'frontend', 'website')}
NAMES = {
    'BUILD_METADATA_SHA256SUMS', 'OCI_ARCHIVE_VERIFICATION.json', 'OCI_INSTALL.md',
    'OCI_NOT_PUBLISHED.txt', 'OCI_RELEASE_VERIFICATION.json', 'OCI_SHA256SUMS', 'SHA256SUMS',
    'VALIDATION.json', 'backend.buildkit.json', 'compose.local-oci.yml',
    'freecollab-0.13.0-main.bundle', 'freecollab-0.13.0-source.tar.gz',
    'freecollab-components-0.13.0.tgz', 'freecollab-demo-0.13.0.html',
    'frontend.buildkit.json', 'release-manifest.attestation-verified.json',
    'release-manifest.intoto.jsonl', 'release-manifest.json', 'sdk-release-manifest.json',
    'verify-oci.py', 'website.buildkit.json',
} | OCI_NAMES


class Blocked(Exception):
    """A safe, deliberately bounded publication failure."""


def require(condition, message):
    if not condition:
        raise Blocked(message)


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(path, expected):
    require(path.is_file() and not path.is_symlink(), 'Asset is not a regular file: ' + path.name)
    require(path.stat().st_size == expected['bytes'] and sha(path) == expected['sha256'],
            'Asset bytes do not match reviewed plan: ' + path.name)


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True).strip()


def tree(root, ref):
    raw = subprocess.check_output(['git', 'ls-tree', '-rz', '-r', ref], cwd=root)
    result = {}
    for item in raw.split(b'\0'):
        if not item:
            continue
        metadata, path = item.split(b'\t', 1)
        mode, kind, oid = metadata.split()
        require(kind == b'blob' and mode in {b'100644', b'100755'}, 'Unexpected source tree entry')
        result[path.decode()] = oid.decode()
    return result


def verify_checksums(path, expected_names, assets):
    seen = set()
    for line in path.read_text().splitlines():
        fields = line.split(maxsplit=1)
        require(len(fields) == 2, 'Invalid checksum line')
        digest, name = fields
        require(name in expected_names and name not in seen, 'Unexpected/duplicate checksum member')
        require(digest == assets[name]['sha256'], 'Checksum does not match reviewed plan: ' + name)
        seen.add(name)
    require(seen == expected_names, 'Checksum inventory is incomplete')


def local_check(publication, product):
    plan = json.loads((publication / 'publication-plan.json').read_text())
    require(plan['schema_version'] == 1 and plan['repository'] == REPO and plan['branch'] == BRANCH,
            'Unexpected publication plan identity')
    require(plan['source_commit'] == FINAL and plan['build_commit'] == BUILT
            and plan['tag_object'] == TAG_OBJECT and plan['tag'] == 'v0.13.0'
            and plan['version'] == '0.13.0' and plan['draft_release_id'] == DRAFT
            and plan['ci_run'] == 39755974580 and plan['ci_attempt'] == 1 and plan['ci_jobs'] == 8
            and plan['build_run'] == 39748780136 and plan['repository_id'] == 1428624207
            and plan['release_author'] == 'krisledel', 'Unexpected reviewed source/run/release identity')
    assets = plan['assets']
    require(set(assets) == NAMES and len(assets) == 24, 'Publication requires the exact 24 asset names')
    for name, item in assets.items():
        require(re.fullmatch(r'[A-Za-z0-9._-]+', name) and isinstance(item['bytes'], int)
                and 0 < item['bytes'] <= 300_000_000
                and re.fullmatch(r'[a-f0-9]{64}', item['sha256']), 'Invalid asset plan entry')
    local = publication / 'assets'
    require(local.is_dir() and not local.is_symlink()
            and {item.name for item in local.iterdir()} == NAMES - OCI_NAMES,
            'Expected exactly 21 checked-in non-OCI assets')
    for name in NAMES - OCI_NAMES:
        verify_file(local / name, assets[name])
    notes = (publication / 'release-notes.md').read_bytes()
    require(hashlib.sha256(notes).hexdigest() == plan['release_notes_sha256']
            == 'b1085c38e2680bfa2fd05e1a2ecb6b9a9b97f4738ddda3f27fc6e398110b1cd9',
            'Release notes do not match the reviewed body')
    verify_checksums(local / 'SHA256SUMS', NAMES - {'SHA256SUMS'}, assets)
    verify_checksums(local / 'BUILD_METADATA_SHA256SUMS',
                     {'release-manifest.json', 'backend.buildkit.json', 'frontend.buildkit.json', 'website.buildkit.json'}, assets)
    oci_names = NAMES - {'SHA256SUMS', 'VALIDATION.json', 'freecollab-0.13.0-main.bundle',
                         'freecollab-0.13.0-source.tar.gz', 'freecollab-demo-0.13.0.html',
                         'freecollab-components-0.13.0.tgz', 'sdk-release-manifest.json'}
    verify_checksums(local / 'OCI_SHA256SUMS', oci_names - {'OCI_SHA256SUMS'}, assets)
    require(git(product, 'rev-parse', 'HEAD') == FINAL
            and git(product, 'rev-parse', 'v0.13.0^{tag}') == TAG_OBJECT
            and git(product, 'rev-parse', 'v0.13.0^{}') == FINAL
            and not git(product, 'status', '--porcelain'), 'Product checkout/tag is not exact and clean')
    final_tree, build_tree = tree(product, FINAL), tree(product, BUILT)
    require(set(final_tree) == set(build_tree) and len(final_tree) == plan['tracked_source_files'] == 1030,
            'Source file inventory changed')
    changes = {name for name in final_tree if final_tree[name] != build_tree[name]}
    require(changes == set(plan['allowed_source_deltas'])
            == {'.github/workflows/ci.yml', 'frontend/tests/oidc-browser.py'}, 'Runtime/build inputs changed')
    require(set(plan['oci_artifacts']) == {'backend', 'frontend', 'website'}, 'Unexpected artifact plan')
    for component, artifact in plan['oci_artifacts'].items():
        require(artifact['name'] == f'freecollab-{component}-0.13.0-oci'
                and artifact['asset'] == f'freecollab-{component}-0.13.0.oci.tar'
                and artifact['archive_member'] == 'freecollab-image.oci.tar'
                and isinstance(artifact['id'], int) and artifact['id'] > 0
                and 0 < artifact['zip_bytes'] <= 310_000_000
                and re.fullmatch(r'sha256:[a-f0-9]{64}', artifact['zip_digest']), 'Invalid artifact source')
    return plan, notes.decode()


def valid_url(value, allowed_hosts):
    parsed = urlsplit(value)
    require(parsed.scheme == 'https' and parsed.hostname in allowed_hosts
            and parsed.port in {None, 443} and not parsed.username and not parsed.password
            and not parsed.fragment, 'Unexpected API/upload URL')
    return parsed


class ArtifactRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        require(req.get_method() in {'GET', 'HEAD'}, 'Mutation redirects are not allowed')
        host = urlsplit(newurl).hostname or ''
        require(host == 'api.github.com' or host.endswith(('.blob.core.windows.net',
                '.actions.githubusercontent.com', '.githubusercontent.com')), 'Unexpected artifact redirect host')
        valid_url(newurl, {host})
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header('Authorization')
        return redirected


class API:
    def __init__(self, token):
        require(bool(token), 'Automatic GitHub job token is unavailable')
        self.token = token
        self.opener = build_opener(ArtifactRedirect())

    def request(self, method, url, *, body=None, size=None, timeout=60):
        parsed = valid_url(url, {'api.github.com', 'uploads.github.com'})
        headers = {'Authorization': 'Bearer ' + self.token,
                   'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
                   'User-Agent': 'freecollab-reviewed-release-publisher-0.13.0'}
        if body is not None:
            headers['Content-Type'] = 'application/octet-stream' if size is not None else 'application/json'
            if size is not None:
                headers['Content-Length'] = str(size)
        try:
            return self.opener.open(Request(url, data=body, headers=headers, method=method), timeout=timeout)
        except HTTPError as failure:
            # Never print token headers, server bodies, or signed redirect URLs.
            raise Blocked(f'HTTP {failure.code} from {parsed.hostname}; '
                          f'request-id={failure.headers.get("X-GitHub-Request-Id", "unavailable")}; '
                          f'accepted-permissions={failure.headers.get("X-Accepted-GitHub-Permissions", "unavailable")}') from None
        except (URLError, TimeoutError):
            raise Blocked('Network request failed; release state must be rechecked before retry') from None

    def json(self, path, method='GET', value=None):
        body = None if value is None else json.dumps(value).encode()
        with self.request(method, 'https://api.github.com/repos/' + REPO + '/' + path, body=body) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
            require(len(raw) <= 8 * 1024 * 1024, 'API response exceeds bound')
            return json.loads(raw)


def remote_gates(api, plan, product):
    require(api.json('git/ref/heads/main')['object']['sha'] == FINAL, 'Product main changed')
    ref = api.json('git/ref/tags/v0.13.0')['object']
    require(ref['type'] == 'tag' and ref['sha'] == TAG_OBJECT, 'Annotated release tag changed')
    tag = api.json('git/tags/' + TAG_OBJECT)
    require(tag['object']['type'] == 'commit' and tag['object']['sha'] == FINAL
            and tag['tag'] == 'v0.13.0' and tag['tagger']['name'] == plan['tagger_name']
            and tag['tagger']['email'] == plan['tagger_email'], 'Tag target/author changed')
    ci = api.json('actions/runs/' + str(plan['ci_run']))
    require(ci['head_sha'] == FINAL and ci['path'] == '.github/workflows/ci.yml'
            and ci['status'] == 'completed' and ci['conclusion'] == 'success'
            and ci['run_attempt'] == plan['ci_attempt'], 'Exact complete product CI has not passed')
    jobs = api.json('actions/runs/' + str(plan['ci_run']) + '/jobs?per_page=100')['jobs']
    require(len(jobs) == plan['ci_jobs'] and all(job['status'] == 'completed'
            and job['conclusion'] == 'success' for job in jobs), 'Not all eight product jobs passed')
    run = api.json('actions/runs/' + str(plan['build_run']))
    require(run['head_sha'] == BUILT and run['path'] == '.github/workflows/release-containers.yml'
            and run['event'] == 'workflow_dispatch' and run['status'] == 'completed'
            and run['conclusion'] == 'success', 'Original reviewed container build did not succeed')
    subprocess.run([sys.executable, str(product / 'scripts/release.py'), 'check', '--version', '0.13.0',
                    '--require-clean', '--require-ci', '--repository', REPO], cwd=product, check=True)


def release_identity(api, notes, *, published=False):
    release = api.json('releases/' + str(DRAFT))
    require(release['id'] == DRAFT and release['author']['login'] == 'krisledel'
            and release['tag_name'] == 'v0.13.0' and release['prerelease'] is False
            and release['draft'] is (not published) and release['body'] == notes,
            'Existing release author/tag/body/state is not the reviewed draft')
    base = release['upload_url'].split('{', 1)[0]
    parsed = valid_url(base, {'uploads.github.com'})
    require(parsed.path == f'/repos/{REPO}/releases/{DRAFT}/assets' and not parsed.query,
            'Unexpected release upload endpoint')
    return base


def existing_assets(api, plan, *, complete=False):
    rows = api.json(f'releases/{DRAFT}/assets?per_page=100')
    require(isinstance(rows, list) and len(rows) <= 24, 'Unexpected release asset inventory')
    seen = {}
    for row in rows:
        name = row['name']
        require(name in NAMES and name not in seen, 'Unexpected/duplicate existing asset')
        item = plan['assets'][name]
        require(row['state'] == 'uploaded' and row['size'] == item['bytes']
                and row.get('digest') == 'sha256:' + item['sha256'],
                'Existing asset differs; automatic overwrite/delete is forbidden: ' + name)
        seen[name] = {'id': row['id'], 'bytes': row['size'], 'digest': row['digest']}
    require(not complete or set(seen) == NAMES, 'The complete 24-asset release is not present')
    return seen


def obtain_oci(api, artifact, plan, work):
    row = api.json('actions/artifacts/' + str(artifact['id']))
    require(row['id'] == artifact['id'] and row['name'] == artifact['name'] and not row['expired']
            and row['digest'] == artifact['zip_digest'] and row['size_in_bytes'] == artifact['zip_bytes']
            and row['workflow_run']['id'] == plan['build_run']
            and row['workflow_run']['head_sha'] == BUILT
            and row['workflow_run']['repository_id'] == plan['repository_id'], 'Artifact identity changed/expired')
    archive = work / (artifact['name'] + '.zip')
    with archive.open('xb') as output, api.request('GET',
            f'https://api.github.com/repos/{REPO}/actions/artifacts/{artifact["id"]}/zip', timeout=180) as response:
        count = 0
        while chunk := response.read(1024 * 1024):
            count += len(chunk)
            require(count <= artifact['zip_bytes'], 'Artifact ZIP exceeds reviewed size')
            output.write(chunk)
    require(count == artifact['zip_bytes'] and 'sha256:' + sha(archive) == artifact['zip_digest'],
            'Artifact ZIP digest/size differs from original GitHub build artifact')
    destination = work / artifact['asset']
    with zipfile.ZipFile(archive) as zipped:
        members = zipped.infolist()
        require(len(members) == 1 and members[0].filename == artifact['archive_member']
                and not members[0].is_dir() and not stat.S_ISLNK(members[0].external_attr >> 16)
                and members[0].file_size == plan['assets'][artifact['asset']]['bytes'], 'Unsafe ZIP inventory')
        with zipped.open(members[0]) as source, destination.open('xb') as output:
            shutil.copyfileobj(source, output, 1024 * 1024)
    verify_file(destination, plan['assets'][artifact['asset']])
    archive.unlink()  # Only this job's owned, digest-checked download ZIP.
    return destination


def publish(plan, notes, publication, product, work, evidence):
    require(os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('GITHUB_REPOSITORY') == REPO
            and os.environ.get('GITHUB_REF') == 'refs/heads/' + BRANCH
            and os.environ.get('GITHUB_EVENT_NAME') == 'push'
            and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted',
            'Publication is restricted to the reviewed hosted branch-push workflow')
    api = API(os.environ.get('GH_TOKEN', ''))
    evidence['phase'] = 'remote identity and CI gates'
    remote_gates(api, plan, product)
    upload = release_identity(api, notes)
    existing = existing_assets(api, plan)
    files = {name: publication / 'assets' / name for name in NAMES - OCI_NAMES}
    evidence['phase'] = 'original OCI download and full-file verification'
    for component in ('backend', 'frontend', 'website'):
        artifact = plan['oci_artifacts'][component]
        files[artifact['asset']] = obtain_oci(api, artifact, plan, work)
    require(set(files) == NAMES, 'Missing reviewed local asset')
    for name, path in files.items():
        verify_file(path, plan['assets'][name])
    evidence['all_24_local_hashes_verified'] = True
    evidence['phase'] = 'sequential no-clobber uploads'
    for name in sorted(NAMES):
        if name in existing:
            continue
        path, expected = files[name], plan['assets'][name]
        with path.open('rb') as stream, api.request('POST', upload + '?' + urlencode({'name': name}),
                                                  body=stream, size=expected['bytes'], timeout=180) as response:
            row = json.loads(response.read(1024 * 1024))
        require(row['name'] == name and row['state'] == 'uploaded' and row['size'] == expected['bytes']
                and row.get('digest') == 'sha256:' + expected['sha256'], 'Upload API digest/size mismatch: ' + name)
        print('Verified uploaded asset: ' + name, flush=True)
    evidence['phase'] = 'final complete inventory and identity recheck'
    remote_gates(api, plan, product)
    release_identity(api, notes)
    evidence['verified_assets'] = existing_assets(api, plan, complete=True)
    evidence['phase'] = 'publish reviewed existing draft'
    api.json('releases/' + str(DRAFT), method='PATCH', value={
        'draft': False, 'prerelease': False, 'make_latest': 'true',
        'name': plan['release_name'], 'body': notes})
    release_identity(api, notes, published=True)
    existing_assets(api, plan, complete=True)
    evidence.update({'status': 'published_and_verified', 'phase': 'complete',
                     'release_url': 'https://github.com/' + REPO + '/releases/tag/v0.13.0'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publication-root', type=Path, required=True)
    parser.add_argument('--product-root', type=Path, required=True)
    parser.add_argument('--work-root', type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check-local', action='store_true')
    mode.add_argument('--publish-reviewed-draft', action='store_true')
    args = parser.parse_args()
    publication, product = args.publication_root.resolve(), args.product_root.resolve()
    work = None
    work_created = False
    evidence = {'status': 'blocked', 'source_commit': FINAL, 'build_commit': BUILT,
                'release_id': DRAFT, 'phase': 'local verification'}
    try:
        plan, notes = local_check(publication, product)
        if args.check_local:
            print('PASS: exact 21 local assets, reviewed 24-asset plan/body and unchanged product source/tag; no network/mutation')
            return
        require(args.work_root is not None, 'An owned fresh work directory is required')
        work = args.work_root.resolve()
        require(not work.exists(), 'Work directory must be new; no implicit reuse or clobber')
        work.mkdir(parents=True)
        work_created = True
        publish(plan, notes, publication, product, work, evidence)
    except (Blocked, subprocess.CalledProcessError) as failure:
        message = str(failure) if isinstance(failure, Blocked) else 'Trusted product release check failed'
        evidence['error'] = message
        print('BLOCKED: ' + message, file=sys.stderr)
        raise SystemExit(1)
    except Exception as failure:
        evidence['error'] = 'Unexpected ' + type(failure).__name__ + '; no success is claimed'
        print('BLOCKED: ' + evidence['error'], file=sys.stderr)
        raise SystemExit(1)
    finally:
        if work_created:
            evidence['recorded_at'] = datetime.now(timezone.utc).isoformat()
            (work / 'publication-result.json').write_text(json.dumps(evidence, indent=2) + '\n')


if __name__ == '__main__':
    main()
