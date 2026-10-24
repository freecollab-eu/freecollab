"""Supervise an isolated, disposable instance of the normal FreeCollab product."""
import base64
import json
import os
from pathlib import Path
import secrets
import signal
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener


def origin(value, *, local=False):
    url = urlsplit(value)
    loopback = url.hostname in {"localhost", "127.0.0.1", "::1"}
    if (url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}
            or not url.hostname or url.scheme != "https" and not (local and loopback and url.scheme == "http")):
        raise ValueError("The demo needs an explicit trusted web origin")
    if any(char.isspace() or char in "\"'<>;" for char in url.netloc):
        raise ValueError("Invalid demo origin")
    return url.scheme + "://" + url.netloc


def main():
    # This image is a separate installation, never an alternative startup path
    # for an existing customer database or shared persistent volume.
    if os.environ.get("DEMO_INSTANCE", "").lower() != "true":
        raise ValueError("This image only runs a disposable example installation")
    mode = os.environ.get("DEMO_ENVIRONMENT", "production")
    if mode not in {"production", "test"}:
        raise ValueError("Invalid example environment")
    public = origin(os.environ["DEMO_PUBLIC_ORIGIN"], local=mode == "test")
    website = origin(os.environ.get("DEMO_WEBSITE_ORIGIN", "https://freecollab-website-production.up.railway.app"), local=mode == "test")
    port = int(os.environ.get("PORT", "8080"))
    if not 1024 <= port <= 65535:
        raise ValueError("Invalid listening port")
    root = Path("/tmp/freecollab-public-demo")
    if root.is_symlink():
        raise ValueError("Invalid example storage directory")
    root.mkdir(mode=0o700, exist_ok=True)
    if root.stat().st_uid != os.getuid() or stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise ValueError("Example storage must be private to this process user")
    keyfile = root / "runtime-secrets.json"
    if not keyfile.exists():
        descriptor = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump({"jwt": secrets.token_urlsafe(48), "content": base64.b64encode(secrets.token_bytes(32)).decode(), "account": base64.b64encode(secrets.token_bytes(32)).decode()}, output)
    if keyfile.is_symlink() or stat.S_IMODE(keyfile.stat().st_mode) != 0o600 or keyfile.stat().st_uid != os.getuid():
        raise ValueError("Invalid example key file")
    keys = json.loads(keyfile.read_text())
    # Keep OS/proxy/CA trust; do not adopt any project-shared customer settings
    # or credentials as configuration for the sample installation.
    retained = {"PATH", "HOME", "LANG", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "NODE_EXTRA_CA_CERTS", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED"}
    env = {key: value for key, value in os.environ.items() if key in retained or key.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"} or key.startswith("LC_")}
    env.update({"ENVIRONMENT": mode, "DEMO_INSTANCE": "true", "DEMO_DATA_PATH": str(root),
        "DATABASE_URL": "sqlite:///" + str(root / "public-demo.sqlite"), "STORAGE_PATH": str(root / "files"),
        "STORAGE_BACKEND": "local", "JWT_SECRET": keys["jwt"], "ACCOUNT_SECURITY_KEY": keys["account"],
        "ACCOUNT_SECURITY_KEY_FILE": str(root / "account-security.key"), "MESSAGE_ENCRYPTION_KEYS": json.dumps({"public-demo": keys["content"]}),
        "MESSAGE_ENCRYPTION_ACTIVE_KEY_ID": "public-demo", "MESSAGE_ENCRYPTION_KEY_FILE": str(root / "message-keys.json"),
        "PUBLIC_APP_URL": public, "CORS_ORIGINS": json.dumps([public]), "MAIL_DELIVERY_MODE": "disabled",
        "WORKFLOWS_ENABLED": "true", "ACTIVITY_FEEDS_ENABLED": "true", "DATA_CONTROLS_ENABLED": "false", "ENTERPRISE_SEARCH_ENABLED": "false",
        "REDIS_URL": "", "PYTHONPATH": "/app", "DEMO_MAX_VISITS": "40", "DEMO_SESSION_MINUTES": "60"})
    for field in ("MATRIX_ENABLED", "CONNECT_FEDERATION_ENABLED", "OIDC_ENABLED", "WEBHOOKS_ENABLED", "SYNC_ENABLED", "OTRUST_ENABLED", "CLOUD_FILES_ENABLED", "EXTERNAL_MEETINGS_ENABLED", "COPILOT_ENABLED", "PUSH_ENABLED", "NATIVE_PUSH_ENABLED", "NATIVE_CALLS_ENABLED", "ENABLE_EXPERIMENTAL_FEATURES", "ENABLE_DEMO_AI"):
        env[field] = "false"
    config = Path(__file__).with_name("nginx.conf.template").read_text().replace("${PORT}", str(port)).replace("${WEBSITE_ORIGIN}", website).replace("${WEBSOCKET_ORIGIN}", public.replace("https://", "wss://").replace("http://", "ws://"))
    nginx = root / "nginx.conf"
    nginx.write_text(config)
    children = []
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        children.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:socket_app", "--host", "127.0.0.1", "--port", "8000", "--workers", "1", "--no-access-log"], env=env))
        # An explicit loopback probe uses no external network or TLS override.
        probe = build_opener(ProxyHandler({}))
        deadline = time.monotonic() + 45
        while not stopping:
            if children[0].poll() is not None:
                raise RuntimeError("Example API failed to start")
            try:
                with probe.open("http://127.0.0.1:8000/ready", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Example API did not become ready")
            time.sleep(.2)
        if not stopping:
            children.append(subprocess.Popen([sys.executable, "-m", "app.services.integration_worker"], env=env))
            children.append(subprocess.Popen(["nginx", "-c", str(nginx), "-g", "daemon off;"], env=env))
        while not stopping:
            if any(child.poll() is not None for child in children):
                raise RuntimeError("An example runtime process stopped")
            time.sleep(.5)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


if __name__ == "__main__":
    main()
