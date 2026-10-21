"""Keep Office bearer capabilities out of the default application access log."""
import logging
import re

_CAPABILITY = re.compile(r"(/(?:api/)?office-edit/)[^\s\"']*")


class CapabilityLogFilter(logging.Filter):
    def filter(self, record):
        # Preserve Uvicorn's five structured args: AccessFormatter unpacks them.
        # The ASGI scope and routing path are never changed.
        def redact(value):
            return _CAPABILITY.sub(r"\1[redacted]", value) if isinstance(value, str) else value
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
