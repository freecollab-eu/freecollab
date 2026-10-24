# The product with sample data

This is a separate, disposable instance of the normal FreeCollab application.
It builds the real `Chat`, `WorkHub`, editors and settings, runs the real API and
native integration worker, and bootstraps an isolated sample workspace per visit.
It contains no second implementation of messages, documents, tasks or AI.

Never attach customer database, storage or key volumes to this image. It uses
private temporary SQLite/files/keys under `/tmp/freecollab-public-demo`, with
strict path/configuration checks. A normal installation does not register the
anonymous bootstrap route. The public instance disables external providers,
mail, federation, push and account/installation administration. Those controls
are configuration boundaries; the normal product components remain shared.

From the repository root, for a local test on one computer:

```sh
docker build -f deploy/product-demo/Dockerfile -t freecollab-product-demo .
docker run --rm -p 127.0.0.1:8082:8080 \
  -e DEMO_ENVIRONMENT=test \
  -e DEMO_PUBLIC_ORIGIN=http://127.0.0.1:8082 \
  -e DEMO_WEBSITE_ORIGIN=http://127.0.0.1:8081 \
  freecollab-product-demo
```

Open `http://127.0.0.1:8082/product-demo/`. For public operation use the default
production mode, an exact HTTPS `DEMO_PUBLIC_ORIGIN`, and the trusted HTTPS
website origin. A single API process/replica owns this disposable SQLite
installation. The supervisor stops the runtime if API, worker or Nginx exits;
`/healthz` checks the actual API readiness.

Point a separate website build at it:

```sh
docker build --build-arg VITE_PUBLIC_DEMO_URL=http://127.0.0.1:8082/product-demo/ \
  -f frontend/website/Dockerfile -t freecollab-website frontend/
```

Then run it on `127.0.0.1:8081:8080`. Its CSP permits only the configured demo
origin in a frame. It still exposes no collaboration API of its own.

Example visits last one hour. Reset creates and adopts a new visit before
revoking the old one; a failed reset keeps the existing workspace usable.
Expiry rejects API/Socket.IO access and the bounded collector removes expired
records/files. Reload starts a fresh visit; previous data expires independently.
The public limit is forty active visits, with bounded requests, uploads and
writes. Use example data only; do not enter real passwords, keys or private files.

See [the feature/verification contract](../../docs/PRODUCT_DEMO.md) and
[normal self-hosting](../../docs/SELF_HOSTING.md). The latter does not depend on
this public sample service.
