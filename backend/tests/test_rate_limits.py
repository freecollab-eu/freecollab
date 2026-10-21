"""Auth limits cannot be bypassed by forwarding headers and retain bounded state."""
import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.services.rate_limit import AuthRateLimiter, auth_rate_limit


def denied(call):
    with pytest.raises(HTTPException) as info:
        call()
    assert info.value.status_code == 429
    assert int(info.value.headers["Retry-After"]) > 0


def test_registration_window_expires_and_other_addresses_work():
    time = [0.0]
    limiter = AuthRateLimiter(clock=lambda: time[0], registration_limit=2)
    limiter.request("register", "first")
    limiter.request("register", "first")
    denied(lambda: limiter.request("register", "first"))
    limiter.request("register", "second")
    time[0] = 60
    limiter.request("register", "first")


def test_account_failures_are_normalized_and_apply_across_addresses():
    time = [0.0]
    limiter = AuthRateLimiter(clock=lambda: time[0], account_failure_limit=2)
    limiter.failed_login("first", " MEMBER@example.com ")
    limiter.failed_login("second", "member@EXAMPLE.com")
    denied(lambda: limiter.check_login("third", "member@example.com"))
    limiter.check_login("first", "other@example.com")
    time[0] = 300
    limiter.check_login("third", "member@example.com")


def test_success_resets_account_limit_without_resetting_address_failures():
    limiter = AuthRateLimiter(account_failure_limit=2, address_failure_limit=2)
    limiter.failed_login("first", "member@example.com")
    limiter.failed_login("first", "member@example.com")
    limiter.successful_login("member@example.com")
    limiter.check_login("second", "member@example.com")
    denied(lambda: limiter.check_login("first", "other@example.com"))


def test_storage_is_bounded_without_evicting_live_throttles():
    time = [0.0]
    limiter = AuthRateLimiter(clock=lambda: time[0], max_keys=2, registration_limit=1)
    limiter.request("register", "one")
    limiter.request("register", "two")
    denied(lambda: limiter.request("register", "three"))
    denied(lambda: limiter.request("register", "one"))
    assert len(limiter.events) == 2
    time[0] = 301
    limiter.request("register", "three")
    assert len(limiter.events) <= 2


def test_forwarded_headers_do_not_select_a_new_throttle_bucket():
    app = FastAPI()
    app.state.auth_rate_limiter = AuthRateLimiter(login_limit=2)
    @app.post("/auth/login", dependencies=[Depends(auth_rate_limit)])
    def login():
        return {"ok": True}
    with TestClient(app) as client:
        for index in range(2):
            assert client.post("/auth/login", headers={"X-Forwarded-For": f"198.51.100.{index}"}).status_code == 200
        response = client.post("/auth/login", headers={"X-Forwarded-For": "203.0.113.99"})
        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) > 0
