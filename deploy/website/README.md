# Public website and browser-only demo

The independent website entry reuses FreeCollab's landing page, documentation
and interactive sample workspace. Demo messages, tasks, documents and appearance
stay in the current tab and disappear on reload/reset. Guest language can be
remembered on the device. The website starts no account provider, mobile bridge,
service worker, provider connection or collaboration API. Its CSP forbids network
connections; the supplied server rejects `/api`, `/socket.io`, `/embed`, `/native`
and service-worker requests. Existing credentials stored on this origin are
preserved and ignored.

Build and run the website image from `frontend/`; host Node/npm is not required:

```sh
docker build -f website/Dockerfile -t freecollab-website:review .
docker run --rm -p 127.0.0.1:8081:8080 freecollab-website:review
```

Open `http://127.0.0.1:8081`. The application pilot uses port 8080 by default,
so both services can run on the same host with these separate loopback ports.

For an optional host asset build, use Node 24 LTS (minimum 22.12) from `frontend/`:

```sh
ONNXRUNTIME_NODE_INSTALL_CUDA=skip npm ci
npm run build:website
```

The published Dockerfile uses a plain dependency-install step: Railway's builder
does not support BuildKit secret mounts. For a local build in a managed proxy/CA
environment, use a temporary copy of the Dockerfile that adds
`--mount=type=secret,id=proxy_ca,required=true` to that `RUN` and sets
`NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca` for the same command. Pass the public
trust bundle with `docker build --secret id=proxy_ca,src="$CODEX_PROXY_CERT"`.
Preserve the platform proxy and TLS verification; keep the temporary Dockerfile
outside the upload directory and never copy the certificate into an image layer.
The customer application uses `frontend/Dockerfile` with its own backend.

## Railway preparation

These files create no project, service, domain, certificates or DNS changes.
Verify the current Railway status and deployment result in your own project:

1. Use a separate website service with repository root `/frontend` and Dockerfile
   `website/Dockerfile`, relative to that build context. Set healthcheck `/healthz`
   with timeout 120 seconds, one replica, restart on failure (five retries), and
   leave build/start command overrides empty. Verify that the deployment actually
   selects the Dockerfile builder. Railway no longer lets new services opt into
   `railway.json`/`railway.toml`; configure these service settings directly or use
   its current Infrastructure as Code workflow. The tracked
   `/frontend/website/railway.json` is a legacy reference; do not substitute the
   customer app's `/frontend/railway.json`.
2. Set `PORT=8080`, then build the reviewed source. This service needs no volume,
   database URL, OAuth registration, API keys or signing material.
3. Check `/healthz`, the landing page, `/#demo`, `/#docs`, EN/SV and a 320px
   viewport. Confirm `/api/ready` returns 404 and the demo makes no API calls.
4. Attach `freecollab.eu` using Railway's current domain instructions and the
   owner's DNS controls. Verify real public HTTPS and the deployed revision
   before advertising the site as live.

Use separate origins for the public website and any hosted customer workspace.
The public demo is sample data and is never described as a connected customer
Microsoft/Google account or a public multi-tenant collaboration installation.
