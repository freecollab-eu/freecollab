# Install the FreeCollab 0.13 OCI release

These original OCI archives contain the backend, application frontend and
independent public website for **Linux amd64 and arm64**. They were built by
the official `release-containers.yml` workflow from commit
`cdecb4bc97562e8027b536e88e451f5a8be7b8d4`. GHCR publication was disabled.
The image names inside the build metadata describe the build contract; they
do **not** claim that those images are publicly pullable from GHCR.

Download the three `.oci.tar` files, `release-manifest.json`, its GitHub
attestation bundle, `verify-oci.py`, `compose.local-oci.yml` and the checksums
from this release. Keep each original archive unchanged. Prerequisites are a
Linux shell, Python 3.10+, recent GitHub CLI with `gh attestation`, Skopeo and
Docker Engine/Compose 2.24.4+. Docker Desktop users can use a Linux/WSL shell
with access to their Linux Docker daemon.

Run the following commands in one Linux shell session. Stop on any verification
failure. All Docker commands and Skopeo imports must target the same daemon;
set its Unix socket explicitly (Docker Desktop may use a different socket):

```sh
set -eu
export FREECOLLAB_DOCKER_SOCKET=unix:///var/run/docker.sock
fc_docker() {
  env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS \
    -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH \
    docker --host "$FREECOLLAB_DOCKER_SOCKET" "$@"
}
```

## Verify publisher and image bytes

Verify the manifest using the official GitHub workflow identity and exact source
commit. This online check uses public GitHub/Sigstore data; no customer provider
credentials are involved:

```sh
gh attestation verify release-manifest.json \
  --repo freecollab-eu/freecollab \
  --signer-workflow freecollab-eu/freecollab/.github/workflows/release-containers.yml \
  --source-digest cdecb4bc97562e8027b536e88e451f5a8be7b8d4 \
  --deny-self-hosted-runners
sha256sum --check OCI_SHA256SUMS
```

For a disconnected installation, perform publisher verification before transfer
and retain the successful verification record plus verified hashes. The supplied
bundle also permits `gh attestation verify --bundle FILE` with the same identity
constraints; fully disconnected verification additionally requires an appropriate
trusted Sigstore root. A checksum alone does not establish publisher identity.

Choose `amd64` for Intel/AMD Linux or `arm64` for ARM Linux. Check the destination
Docker daemon architecture, rather than assuming it matches the shell host.

```sh
fc_docker info --format '{{.Architecture}}'
export ARCH=amd64
python3 verify-oci.py \
  --manifest release-manifest.json --version 0.13.0 \
  --commit cdecb4bc97562e8027b536e88e451f5a8be7b8d4 \
  --backend freecollab-backend-0.13.0.oci.tar \
  --frontend freecollab-frontend-0.13.0.oci.tar \
  --website freecollab-website-0.13.0.oci.tar \
  --arch "$ARCH" --env-output images.local.env --report oci-verified.json
```

The helper verifies the signed manifest's exact OCI index digests, every stored
blob, uncompressed runtime layer diff IDs, both Linux architectures, source and
version labels, and embedded SPDX/SLSA attestations. It checks that known private
runtime data/configuration and Nginx log paths are empty. It extracts no files,
loads no images and preserves existing output files. Publisher verification
remains the separate GitHub CLI step above.

## Import one architecture into your Docker daemon

Use Skopeo rather than relying on `docker load` support for a multi-platform OCI
archive. The verified report selects the archive's exact named reference when
its top-level layout has multiple entries. No registry login is needed.

```sh
for component in backend frontend website; do
  source_ref=$(python3 -c 'import json,sys;print(json.load(open("oci-verified.json"))["images"][sys.argv[1]]["source_for_skopeo"])' "$component")
  skopeo --override-os linux --override-arch "$ARCH" copy \
    --dest-daemon-host "$FREECOLLAB_DOCKER_SOCKET" \
    "$source_ref" "docker-daemon:freecollab-local-$component:0.13.0"
  expected_id=$(python3 -c 'import json,sys;print(json.load(open("oci-verified.json"))["images"][sys.argv[1]]["platforms"][sys.argv[2]]["local_image_config_id"])' "$component" "$ARCH")
  test "$(fc_docker image inspect --format '{{.Id}}' "freecollab-local-$component:0.13.0")" = "$expected_id"
  test "$(fc_docker image inspect --format '{{.Os}}/{{.Architecture}}' "freecollab-local-$component:0.13.0")" = "linux/$ARCH"
done
```

Skopeo can convert manifest formats when importing into Docker. Consequently,
`images.local.env` pins the **verified architecture-specific Docker configuration
IDs**, which must match the imported images. These local IDs are distinct from
the OCI multi-platform registry index digests. Do not add `--all` or
`--preserve-digests` to this Docker-daemon import command.

## Start the default application stack

The three archives do not include PostgreSQL. Provision a trusted PostgreSQL 15
image separately for the same architecture, verify its provenance/digest under
your infrastructure policy, and make it available in the local daemon. For a
connected first setup, the upstream image can be obtained with
`fc_docker pull postgres:15-alpine`; an air-gapped host must preload it before
disconnecting. Add its actual local configuration ID to `images.local.env`:

```sh
printf 'FREECOLLAB_POSTGRES_IMAGE=%s\n' \
  "$(fc_docker image inspect --format '{{.Id}}' postgres:15-alpine)" >> images.local.env
```

Use the release source archive or `v0.13.0` tag at
`0927e8e258819250d63e27cf0b514e09227960e9` with the supplied Compose overlay.
Its only changes from image build `cdecb4bc97562e8027b536e88e451f5a8be7b8d4` are
OIDC browser-test readiness and CI website-startup retry/diagnostics.
`frontend/.dockerignore` excludes the changed test from both application and
website build contexts; the CI workflow is outside those contexts and the
unchanged backend context. Product, native, SDK, dependencies, Dockerfiles,
Compose and initialization files match the image build.

Verification commands, image labels and signed attestations retain the actual
`cdecb4bc97562e8027b536e88e451f5a8be7b8d4` build source. This comparison does not
claim byte-identical rebuilds or change the original build provenance.

Create your own private installation keys with
`python3 scripts/pilot-init.py --output pilot.env`, then
review the generated environment and the production HTTPS, SMTP, storage and
backup requirements in `docs/OPERATIONS.md` and `docs/DISTRIBUTION.md`.
The container archives contain no installation keys or customer connections.

Copy `compose.local-oci.yml` and `images.local.env` into the source tree, then:

```sh
fc_docker compose --project-name freecollab \
  --env-file pilot.env --env-file images.local.env \
  -f docker-compose.yml -f compose.local-oci.yml config --quiet
fc_docker compose --project-name freecollab \
  --env-file pilot.env --env-file images.local.env \
  -f docker-compose.yml -f compose.local-oci.yml \
  up -d --no-build --pull never --wait
```

This overlay disables both app builds and image pulls, including every app
worker. Do not combine it with `deploy/release/compose.yml` or
`deploy/website/compose.yml`, whose registry installation recipes require pulls.
The default stack contains API, app frontend, integration worker and PostgreSQL.
Optional Matrix, broker, SFU or other profiles require their additional vendor
images and configuration; they are not included in these three archives.
For upgrades, take and verify an authenticated backup before changing images.

## Run the independent public website separately

After verification/import, use the website's local configuration ID:

```sh
website_id=$(python3 -c 'import json,os;print(json.load(open("oci-verified.json"))["images"]["website"]["platforms"][os.environ["ARCH"]]["local_image_config_id"])')
fc_docker run --rm --pull=never -e PORT=8080 \
  -p 127.0.0.1:8081:8080 "$website_id"
```

This is a static website and disposable browser-only demo. It does not start a
customer workspace or external providers. Public hosting, HTTPS and DNS remain
operator actions; Railway deployment is not performed by this release.

## Optional customer-owned registry

You can copy the complete original multi-platform image, including attestations,
to your own registry. Authenticate to that registry yourself, choose a new
destination name, and use `skopeo copy --all --preserve-digests` from the verified
`source_for_skopeo` value to `docker://YOUR_REGISTRY/IMAGE:0.13.0`. The command
fails if exact digest preservation is unsupported. Verify the destination index
digest against `release-manifest.json` before using it. This is an operator
action and does not imply that FreeCollab published anything to GHCR.
