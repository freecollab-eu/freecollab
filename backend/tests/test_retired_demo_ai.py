"""A historical opt-in setting cannot restore anonymous provider access."""
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys


def test_old_demo_flag_cannot_expose_either_anonymous_groq_alias(tmp_path):
    environment = os.environ.copy()
    environment.update(ENVIRONMENT="test", ENABLE_DEMO_AI="true", GROQ_API_KEY="synthetic-retired-demo-provider-key",
        DATABASE_URL="sqlite:///"+str(tmp_path/"retired-demo.db"), STORAGE_PATH=str(tmp_path/"files"),
        JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
        MESSAGE_ENCRYPTION_KEYS=json.dumps({"retired": base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID="retired",
        MESSAGE_ENCRYPTION_KEY_FILE=str(tmp_path/"message.keys"), ACCOUNT_SECURITY_KEY_FILE=str(tmp_path/"account.key"),
        MATRIX_ENABLED="false", MAIL_DELIVERY_MODE="disabled", OIDC_ENABLED="false", SYNC_ENABLED="false", WEBHOOKS_ENABLED="false", OTRUST_ENABLED="false")
    script = '''import json
from fastapi.testclient import TestClient
import groq
calls=[]
def forbidden(*args,**kwargs):
    calls.append('provider_constructor')
    raise AssertionError('Retired demo must never create a provider client')
groq.Groq=forbidden
from app.main import app
with TestClient(app) as client:
    statuses={path:client.post(path,json={'message':'Explicit synthetic test','history':[]}).status_code for path in ('/demo/chat','/api/demo/chat')}
    capabilities=client.get('/capabilities').json()
assert statuses=={'/demo/chat':404,'/api/demo/chat':404},statuses
assert calls==[],calls
assert capabilities['demo_ai'] is False,capabilities
print(json.dumps({'aliases':statuses,'provider_calls':len(calls),'demo_ai':capabilities['demo_ai']}))
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], env=environment,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr[-4000:] + result.stdout[-2000:]
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["provider_calls"] == 0 and report["demo_ai"] is False
