# Public website and browser-only demo

The independent website entry reuses FreeCollab's landing page, documentation
and interactive sample workspace. Demo messages, tasks, documents and appearance
stay in the current tab and disappear on reload/reset. Guest language can be
remembered on the device. The website starts no account provider, mobile bridge,
service worker, provider connection or collaboration API. Its CSP forbids network
connections; the supplied server rejects `/api`, `/socket.io`, `/embed`, `/native`
and service-worker requests. Existing credentials stored on this origin are
preserved and ignored.

Build locally from `frontend/`:

```sh
npm ci
npm run build:website
docker build -f website/Dockerfile -t freecollab-website:review .
docker run --rm -p 127.0.0.1:8080:8080 freecollab-website:review
```

For managed proxy/CA environments, pass the public trust bundle as the BuildKit
secret `proxy_ca`; preserve the platform proxy and TLS verification. The normal
customer application continues to use `frontend/Dockerfile` with its own backend.

## Railway preparation

Railway deployment remains paused. These files create no project, service,
domain, certificates or DNS changes. When the owner resumes deployment:

1. Use a separate website service with repository root `frontend` and config
   `/frontend/website/railway.json`; verify that its build context is `frontend`
   and Dockerfile is `website/Dockerfile` in Railway's plan.
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
