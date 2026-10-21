"""Operator-invoked immutable write/read/checksum/erasure probe."""
import hashlib
import json
import secrets
from uuid import uuid4

from .services.storage import storage_service


def probe(service, *, identifier=None):
    identifier, content = identifier or uuid4().hex, secrets.token_bytes(64)
    created = erased = False
    try:
        service.save_file_sync(identifier, content)
        created = True
        returned = service.read_file(identifier, max_bytes=64, expected_size=64)
        if returned is None or hashlib.sha256(returned).digest() != hashlib.sha256(content).digest():
            raise ValueError("Storage probe bytes did not verify")
        service.delete_file_sync(identifier)
        erased = service.read_file(identifier, max_bytes=64) is None
        if not erased:
            raise ValueError("Storage probe erasure did not verify")
        return {"backend": service.backend, "write": True, "read": True,
                "checksum": True, "erased": True}
    finally:
        if created and not erased:
            service.delete_file_sync(identifier)


def main():
    identifier = uuid4().hex
    try:
        print(json.dumps(probe(storage_service, identifier=identifier)))
    except Exception:
        # Metadata only: never print keys, endpoint URLs, payloads or SDK errors.
        print(json.dumps({"backend": storage_service.backend, "verified": False, "probe_id": identifier,
                          "detail": "Storage probe did not complete. Review storage permissions, TLS and retention; a probe object may require cleanup."}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
