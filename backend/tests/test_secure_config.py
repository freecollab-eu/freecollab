import pytest
from pydantic import ValidationError
from app.config import Settings


def secure(**overrides):
    return Settings(_env_file=None, matrix_enabled=True, matrix_registration_shared_secret='s'*48, **overrides)


def test_secure_messaging_requires_private_explicit_registration_secret():
    for value in (None, '', 'short'):
        with pytest.raises(ValidationError, match='registration secret'):
            Settings(_env_file=None, matrix_enabled=True, matrix_registration_shared_secret=value)
    assert secure().matrix_enabled is True
    assert Settings(_env_file=None, matrix_enabled=False).matrix_enabled is False


@pytest.mark.parametrize('url', ['file:///data/secret','http://user:password@matrix:8008','http://matrix:8008/other','https://matrix/?secret=yes','http://matrix/#fragment','http://'])
def test_private_homeserver_url_is_an_origin_not_an_arbitrary_proxy_destination(url):
    with pytest.raises(ValidationError, match='MATRIX_INTERNAL_URL'):
        secure(matrix_internal_url=url)


@pytest.mark.parametrize('name', ['bad/name','bad name','https://example.com','user@example.com'])
def test_server_identity_has_safe_stable_format(name):
    with pytest.raises(ValidationError, match='MATRIX_SERVER_NAME'):
        secure(matrix_server_name=name)
