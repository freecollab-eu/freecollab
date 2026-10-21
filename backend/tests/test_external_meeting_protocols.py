"""Real verified-TLS/CONNECT OAuth and meeting payloads for all three providers."""
import base64
import hashlib
from urllib.parse import parse_qs, urlsplit
import pytest

from app.services import external_meeting_providers as providers
from tests.external_meeting_fixture import ProviderFixture, SECRET
from tests.test_external_meetings import CONFIGS, FIELDS


@pytest.mark.parametrize("provider", ["google_meet", "microsoft_teams", "zoom"])
def test_private_https_oauth_create_read_conditional_delete_and_refresh(provider, tmp_path):
    fixture = ProviderFixture(tmp_path / provider).start()
    verifier = "v" * 64; challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    public, initial = CONFIGS[provider], {"client_secret": SECRET}
    try:
        with fixture.transport():
            url = providers.authorization_url(provider, public, initial, "https://app.example/callback", "state-" * 8, challenge)
            query = parse_qs(urlsplit(url).query)
            assert query["client_id"] == [public["client_id"]] and query["code_challenge"] == [challenge]
            code = fixture.issue_code(provider, challenge, public["client_id"])
            secret = {**initial, **providers.exchange_code(provider, public, initial, "https://app.example/callback", code, verifier)}
            providers.test(provider, public, secret)
            result = providers.create(provider, public, secret, "a" * 32, FIELDS)
            assert result["join_url"] and result["title"] == FIELDS["title"]
            observed = providers.read(provider, public, secret, result["id"])
            assert providers.fingerprint(result) == providers.fingerprint(observed)
            refreshed = providers.refresh(provider, public, secret)
            assert refreshed["refresh_token"] and fixture.refreshes[provider] == 1
            providers.cancel(provider, public, refreshed, result["id"], result["etag"])
            assert fixture.deletes[provider] == fixture.posts[provider] == 1
            assert all(host in {"oauth2.googleapis.com", "www.googleapis.com", "login.microsoftonline.com", "graph.microsoft.com", "zoom.us", "api.zoom.us"} for host, _, _ in fixture.requests)
    finally: fixture.close()


@pytest.mark.parametrize("provider", ["google_meet", "microsoft_teams", "zoom"])
def test_uncertain_create_reconciles_by_real_https_reads_without_another_post(provider, tmp_path):
    fixture = ProviderFixture(tmp_path / provider).start()
    verifier = "v" * 64; challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    try:
        with fixture.transport():
            public = CONFIGS[provider]; code = fixture.issue_code(provider, challenge, public["client_id"])
            secret = {"client_secret": SECRET, **providers.exchange_code(provider, public, {"client_secret": SECRET}, "https://app.example/callback", code, verifier)}
            fixture.uncertain_once.add(provider)
            with pytest.raises(providers.ProviderError): providers.create(provider, public, secret, "b" * 32, FIELDS)
            recovered = providers.reconcile(provider, public, secret, "b" * 32)
            assert recovered["join_url"] and fixture.posts[provider] == 1
    finally: fixture.close()
