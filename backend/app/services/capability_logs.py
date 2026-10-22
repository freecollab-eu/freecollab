"""Keep bearer capabilities and OAuth handoffs out of application access logs."""
import logging
import re

def _url_literal(value):
    # ASGI routing decodes URL octets; Uvicorn may log the original raw path.
    # Match both representations without decoding untrusted text into log lines.
    return ''.join('(?:' + re.escape(char) + '|%' + format(ord(char), '02x') + ')' for char in value)


_PREFIX = _url_literal('/') + '(?:' + _url_literal('api/') + ')?'
_CAPABILITY = re.compile('(' + _PREFIX + _url_literal('office-edit/') + r')[^\s\"\']*', re.IGNORECASE)
# Every native OAuth/OIDC callback carries one-use credentials. The generic
# backend image can emit access logs even when the outer proxy suppresses them.
_OAUTH_CALLBACK_PATHS = (
    'auth/oidc/callback',
    'cloud-files/oauth/callback',
    'external-meetings/oauth/callback',
    'sync/oauth/callback',
    'personal-oauth/callback',
)
_OAUTH_CALLBACK = re.compile(
    '(' + _PREFIX + '(?:' + '|'.join(_url_literal(path) for path in _OAUTH_CALLBACK_PATHS)
    + ')(?:' + _url_literal('/') + r')?)(?:\?|%3f)[^\s\"\']*', re.IGNORECASE,
)
_CALENDAR_SUBSCRIPTION = re.compile('(' + _PREFIX + _url_literal('calendar/subscriptions/') + r')[^\s\"\']*', re.IGNORECASE)


class CapabilityLogFilter(logging.Filter):
    def filter(self, record):
        # Preserve Uvicorn's five structured args: AccessFormatter unpacks them.
        # The ASGI scope and routing path are never changed.
        def redact(value):
            if not isinstance(value, str):
                return value
            value = _CALENDAR_SUBSCRIPTION.sub(r"\1[redacted]", value)
            return _OAUTH_CALLBACK.sub(r"\1?[redacted]", _CAPABILITY.sub(r"\1[redacted]", value))
        record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: redact(value) for key, value in record.args.items()}
        return True


def install_capability_log_filter():
    for name in ("uvicorn.access", "uvicorn.error", "freecollab.security_audit"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, CapabilityLogFilter) for item in logger.filters):
            logger.addFilter(CapabilityLogFilter())
