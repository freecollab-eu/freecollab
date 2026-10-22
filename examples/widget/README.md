# Standalone widget example

This is a plain HTML integration of the actual `FreeCollab.init` browser bundle. It does not require a published npm SDK. Use a disposable test installation and personal channel-member token.

From the repository root:

```bash
(cd frontend && npm ci && npm run build:widget)
cp frontend/dist-widget/widget.iife.js examples/widget/widget.iife.js
python -m http.server 8090 --bind 127.0.0.1 --directory examples/widget
```

Open `http://127.0.0.1:8090/`. Enter the API base URL, personal member token from the login response, and channel ID. The copied build output is ignored by Git. Configure the backend `CORS_ORIGINS` JSON array to include the exact example-page origin before starting/restarting it. For local development, that includes `http://127.0.0.1:8090`; allow HTTPS origins only in production.

For the local Compose pilot, the API base is `http://127.0.0.1:8080/api`. The normal direct development API base is `http://127.0.0.1:8000`. The token must belong to a channel member; a channel ID by itself cannot authorize access. Use a separate authenticated host login flow for a real integration. Never embed a permanent user token, staff token or private deployment setting in this page or its URL.

The API base must use HTTPS outside loopback development; URL credentials, query strings and fragments are refused. Widget requests omit cookies, use `cache: 'no-store'` and reject redirects. CORS still needs the exact host-page origin.

The form holds its token only in memory, clears the password input after startup, loads a widget bundle from the same origin, and uses text rendering for errors. Disconnect destroys the widget and its polling. Loading the bundle means the widget code runs with the host page's privileges; host only a trusted build.

`setUser` and `setChannel` clear the old conversation and unsent draft when the binding changes. Pending work from the old binding cannot update the new one, including when a host callback changes the binding synchronously. `destroy()` cancels work and discards the token; create a new instance to reconnect.

The widget polls the most recent 50 messages every three seconds. It is a limited integration client: it does not reconcile edits/deletions or fill history gaps larger than that window. FreeCollab's server can read content, and uploaded files are plaintext; see [the security model](../../docs/SECURITY_MODEL.md).
