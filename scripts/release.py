#!/usr/bin/env python3
"""Validate a FreeCollab release and create digest-pinned installation metadata.

This helper does not publish, log in, sign, start services or change source.
"""
from __future__ import annotations

import argparse
import ast
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?\Z")
DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")
COMMIT = re.compile(r"[a-f0-9]{40}\Z")
REPOSITORY = re.compile(r"[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]*\Z")
PACKAGES = ("frontend", "packages/components", "clients/desktop", "clients/mobile")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def version(value: str) -> str:
    require(bool(SEMVER.fullmatch(value)), "Use an exact semantic version, without a v prefix.")
    return value


def repository(value: str) -> str:
    value = value.lower()
    require(bool(REPOSITORY.fullmatch(value)), "Use a lowercase GitHub owner/repository.")
    return value


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def validate_source(root: Path, expected: str, require_clean: bool = False) -> dict:
    expected = version(expected)
    for directory in PACKAGES:
        package = json.loads((root / directory / "package.json").read_text())
        lock = json.loads((root / directory / "package-lock.json").read_text())
        require(package["version"] == expected, f"{directory}/package.json version does not match.")
        require(lock["version"] == expected and lock["packages"][""]["version"] == expected,
                f"{directory}/package-lock.json version does not match.")
    tree = ast.parse((root / "backend/app/main.py").read_text())
    api_versions = [keyword.value.value for node in ast.walk(tree)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "FastAPI"
                    for keyword in node.keywords if keyword.arg == "version" and isinstance(keyword.value, ast.Constant)]
    require(api_versions == [expected], "The backend API version does not match.")
    android = (root / "clients/mobile/android/app/build.gradle").read_text()
    require(re.findall(r'versionName\s+["\']([^"\']+)["\']', android) == [expected], "Android versionName does not match.")
    ios = (root / "clients/mobile/ios/App/App.xcodeproj/project.pbxproj").read_text()
    ios_versions = re.findall(r"MARKETING_VERSION\s*=\s*([^;\s]+)\s*;", ios)
    require(bool(ios_versions) and set(ios_versions) == {expected}, "iOS marketing version does not match.")
    commit = git(root, "rev-parse", "HEAD")
    require(bool(COMMIT.fullmatch(commit)), "The source commit is invalid.")
    if require_clean:
        require(not git(root, "status", "--porcelain"), "Release source must be a clean checkout.")
    ref_type, ref_name = os.environ.get("GITHUB_REF_TYPE"), os.environ.get("GITHUB_REF_NAME")
    if ref_type == "tag":
        require(ref_name == "v" + expected, "Release tag must exactly match the source version.")
    return {"version": expected, "commit": commit}


def require_successful_ci(root: Path, repo: str, commit: str) -> None:
    """Require the actual CI workflow's completed success for this exact commit."""
    repo = repository(repo)
    require(bool(COMMIT.fullmatch(commit)), "Invalid source commit.")
    payload = subprocess.check_output([
        "gh", "api", f"repos/{repo}/actions/workflows/ci.yml/runs?head_sha={commit}&status=completed&per_page=100"
    ], cwd=root, text=True)
    runs = json.loads(payload).get("workflow_runs", [])
    require(any(row.get("head_sha") == commit and row.get("conclusion") == "success"
                and row.get("event") in {"push", "pull_request", "workflow_dispatch"} for row in runs),
            "This exact commit has no successful completed ci.yml run. Publication is blocked.")


def require_unused_image(reference: str) -> None:
    require(bool(re.fullmatch(r"ghcr\.io/[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]*:(?:v[0-9A-Za-z.-]+|sha-[a-f0-9]{40})", reference)),
            "Unexpected publication image reference.")
    result = subprocess.run(["docker", "buildx", "imagetools", "inspect", reference], capture_output=True, text=True)
    require(result.returncode != 0, "Publication would overwrite an existing image tag: " + reference)
    # A denied login, unavailable registry or DNS/TLS failure must never be
    # mistaken for an unused tag. Preserve those errors as publication blockers.
    error = result.stderr.lower()
    require("manifest unknown" in error or bool(re.search(r"(?:^|\s)not found(?:\s|$)", error)),
            "Cannot establish that the registry tag is unused; check registry access before publication.")


def validate_sdk(path: Path, expected: str) -> dict:
    expected = version(expected)
    manifest = json.loads((path / "release-manifest.json").read_text())
    require(manifest.get("name") == "@freecollab/components" and manifest.get("version") == expected,
            "Unexpected SDK package identity.")
    require(manifest.get("artifact") == f"freecollab-components-{expected}.tgz", "Unexpected SDK artifact path.")
    require(manifest.get("publishTag") == "next" and manifest.get("registry") == "https://registry.npmjs.org/",
            "Unexpected SDK registry or distribution tag.")
    artifact = path / manifest["artifact"]
    require(artifact.is_file() and not artifact.is_symlink(), "SDK artifact must be a regular local file.")
    raw = artifact.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == manifest.get("sha256"), "SDK SHA-256 does not match.")
    require("sha512-" + base64.b64encode(hashlib.sha512(raw).digest()).decode() == manifest.get("integrity"),
            "SDK SHA-512 integrity does not match.")
    return manifest


def container_manifest(repo: str, release: str, commit: str, backend: str, frontend: str, website: str | None = None,
                       published: bool = False) -> dict:
    repo, release = repository(repo), version(release)
    require(bool(COMMIT.fullmatch(commit)), "Invalid source commit.")
    require(bool(DIGEST.fullmatch(backend)) and bool(DIGEST.fullmatch(frontend)), "Container digests must be SHA-256.")
    images = {}
    components = [("backend", backend), ("frontend", frontend)]
    if website is not None:
        require(bool(DIGEST.fullmatch(website)), "Website digest must be SHA-256.")
        components.append(("website", website))
    for component, digest in components:
        name = f"ghcr.io/{repo}-{component}"
        images[component] = {"name": name, "digest": digest, "reference": f"{name}@{digest}"}
    return {"format": "freecollab-release-v1", "version": release, "repository": repo, "sourceCommit": commit,
            "publication": "ghcr" if published else "local-oci",
            "sourceURL": f"https://github.com/{repo}/commit/{commit}", "createdAt": datetime.now(timezone.utc).isoformat(),
            "images": images}


def validate_container_manifest(manifest: dict) -> dict:
    require(manifest.get("format") == "freecollab-release-v1", "Unsupported release manifest format.")
    require(manifest.get("publication") in {"ghcr", "local-oci"}, "Unsupported publication state.")
    expected = container_manifest(manifest["repository"], manifest["version"], manifest["sourceCommit"],
                                  manifest["images"]["backend"]["digest"], manifest["images"]["frontend"]["digest"],
                                  manifest["images"].get("website", {}).get("digest"), manifest['publication'] == 'ghcr')
    require(manifest.get("images") == expected["images"], "Image names/references do not match the declared repository and digests.")
    require(manifest.get("sourceURL") == expected["sourceURL"], "Source URL does not match the declared commit.")
    return manifest


def write_new(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Installation pins/manifests contain only public identities. Existing
    # operator files are never overwritten implicitly.
    with path.open("x") as output:
        output.write(text)
    path.chmod(0o644)


def pins(manifest: dict) -> str:
    validate_container_manifest(manifest)
    require(manifest['publication'] == 'ghcr', "Local OCI review artifacts are not published registry images.")
    value = ("# Generated from a reviewed FreeCollab release manifest. Contains no credentials.\n"
            f"FREECOLLAB_RELEASE_VERSION={manifest['version']}\n"
            f"FREECOLLAB_RELEASE_COMMIT={manifest['sourceCommit']}\n"
            f"FREECOLLAB_BACKEND_IMAGE={manifest['images']['backend']['reference']}\n"
            f"FREECOLLAB_FRONTEND_IMAGE={manifest['images']['frontend']['reference']}\n")
    if "website" in manifest["images"]:
        value += f"FREECOLLAB_WEBSITE_IMAGE={manifest['images']['website']['reference']}\n"
    return value


def verify_attestations(manifest: dict) -> None:
    validate_container_manifest(manifest)
    require(manifest['publication'] == 'ghcr', "Local OCI review artifacts cannot have registry publication attestations.")
    for image in manifest["images"].values():
        subprocess.run(["gh", "attestation", "verify", "oci://" + image["reference"],
                        "--repo", manifest["repository"],
                        "--signer-workflow", manifest["repository"] + "/.github/workflows/release-containers.yml",
                        "--source-digest", manifest["sourceCommit"], "--deny-self-hosted-runners"], check=True)


def asset_manifest(directory: Path, release: str, commit: str, platform: str, signing: str,
                   installation: str | None = None, application_id: str | None = None) -> dict:
    version(release)
    require(bool(COMMIT.fullmatch(commit)), "Invalid source commit.")
    require(platform in {"linux-x64", "linux-arm64", "windows-x64", "macos-x64", "macos-arm64", "android", "ios"},
            "Unexpected artifact platform.")
    require(signing in {"unsigned", "windows-authenticode-verified", "apple-notarized", "android-apksigner-verified", "apple-distribution-verified"},
            "Unexpected signing assertion.")
    allowed = {".AppImage", ".deb", ".exe", ".dmg", ".zip", ".apk", ".aab", ".ipa"}
    files = []
    for path in sorted(directory.iterdir()):
        if path.suffix not in allowed:
            continue
        require(path.is_file() and not path.is_symlink(), "Release assets must be regular files.")
        name = path.relative_to(directory).as_posix()
        require(bool(re.fullmatch(r"[A-Za-z0-9._/-]+", name)), "Unexpected release asset path.")
        files.append({"path": name, "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    require(bool(files), "No release binaries were produced.")
    manifest = {"format": "freecollab-native-release-v1", "version": release, "sourceCommit": commit,
                "platform": platform, "signing": signing, "assets": files}
    if platform in {"android", "ios"}:
        require(isinstance(installation, str) and len(installation) <= 2048 and not any(c.isspace() for c in installation),
                "A mobile release requires its public HTTPS installation origin.")
        target = urlsplit(installation)
        require(target.scheme == 'https' and bool(target.hostname) and not target.username and not target.password
                and target.path in {'', '/'} and not target.query and not target.fragment, "Invalid mobile installation origin.")
        require(isinstance(application_id, str) and len(application_id) <= 200 and
                bool(re.fullmatch(r'[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*){2,}', application_id)),
                "A mobile release requires its exact application/bundle ID.")
        manifest.update({"installation": installation.rstrip('/'), "applicationID": application_id})
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Require matching source/package/platform versions.")
    check.add_argument("--root", type=Path, default=ROOT)
    check.add_argument("--version", required=True)
    check.add_argument("--require-clean", action="store_true")
    check.add_argument("--require-ci", action="store_true")
    check.add_argument("--repository", default="freecollab-eu/freecollab")
    check.add_argument("--github-output", type=Path)
    sdk = commands.add_parser("sdk", help="Verify exact packed SDK identity and both integrity hashes.")
    sdk.add_argument("directory", type=Path)
    sdk.add_argument("--version", required=True)
    absent = commands.add_parser("image-absent", help="Fail closed if a registry tag exists or cannot be checked.")
    absent.add_argument("reference")
    create = commands.add_parser("containers", help="Create a public manifest from published image digests.")
    for name in ("version", "commit", "backend-digest", "frontend-digest"):
        create.add_argument("--" + name, required=True)
    create.add_argument("--repository", default="freecollab-eu/freecollab")
    create.add_argument("--website-digest")
    create.add_argument("--published", action="store_true", help="Record successful registry publication; does not publish by itself.")
    create.add_argument("--output", type=Path, required=True)
    pin = commands.add_parser("pins", help="Create immutable Compose image pins from a reviewed manifest.")
    pin.add_argument("manifest", type=Path)
    pin.add_argument("--output", type=Path, required=True)
    pin.add_argument("--verify-attestations", action="store_true", help="Require GitHub attestations for both exact image digests and source commit.")
    assets = commands.add_parser("assets", help="Hash a verified native build's public binary assets.")
    assets.add_argument("directory", type=Path)
    for name in ("version", "commit", "platform", "signing", "output"):
        assets.add_argument("--" + name, required=True, **({"type": Path} if name == "output" else {}))
    assets.add_argument("--installation")
    assets.add_argument("--application-id")
    args = parser.parse_args()
    try:
        if args.command == "check":
            result = validate_source(args.root, args.version, args.require_clean)
            if args.require_ci:
                require_successful_ci(args.root, args.repository, result["commit"])
            if args.github_output:
                with args.github_output.open("a") as output:
                    for key, value in result.items():
                        output.write(f"{key}={value}\n")
            print(json.dumps(result))
        elif args.command == "sdk":
            manifest = validate_sdk(args.directory, args.version)
            print(json.dumps({"version": manifest["version"], "artifact": manifest["artifact"], "sha256": manifest["sha256"]}))
        elif args.command == "image-absent":
            require_unused_image(args.reference)
            print("Registry tag is unused: " + args.reference)
        elif args.command == "containers":
            manifest = container_manifest(args.repository, args.version, args.commit, args.backend_digest, args.frontend_digest,
                                          args.website_digest, args.published)
            write_new(args.output, json.dumps(manifest, indent=2) + "\n")
            print("Created public digest manifest: " + str(args.output))
        elif args.command == "pins":
            manifest = json.loads(args.manifest.read_text())
            if args.verify_attestations:
                verify_attestations(manifest)
            write_new(args.output, pins(manifest))
            print("Created public image pins: " + str(args.output))
        elif args.command == "assets":
            manifest = asset_manifest(args.directory, args.version, args.commit, args.platform, args.signing,
                                      args.installation, args.application_id)
            write_new(args.output, json.dumps(manifest, indent=2) + "\n")
            checksums = "".join(f"{item['sha256']}  {item['path']}\n" for item in manifest["assets"])
            write_new(args.output.parent / "SHA256SUMS", checksums)
            print("Created public binary manifest: " + str(args.output))
    except (ValueError, KeyError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as failure:
        # Messages include only public source/release identities, not environment
        # values, provider credentials or command output from authenticated CLIs.
        parser.exit(1, "Release validation failed: " + str(failure) + "\n")


if __name__ == "__main__":
    main()
