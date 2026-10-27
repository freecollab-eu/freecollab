"""Finish already-authorized native file erasure without browser credentials."""
from hashlib import sha256
import json
import logging

from sqlalchemy import delete
from sqlmodel import select

from ..models.file import File
from ..models.file_version import FileErasure
from . import storage


def storage_scope(store):
    # Never apply an old erasure to a newly configured bucket or local root.
    # Keep paths and endpoint configuration out of persisted queue metadata.
    if store.backend == "local":
        namespace = ["local", str(store.storage_path.resolve())]
    else:
        namespace = [store.backend, store.config.s3_endpoint_url or "", store.config.s3_region,
                     store.bucket, store.prefix]
    return sha256(json.dumps(namespace, separators=(",", ":")).encode()).hexdigest()


def erase_pending_file_blobs(session, maximum=20, *, file_ids=None, store=None):
    store = store or storage.storage_service
    scope = storage_scope(store)
    query = select(FileErasure.file_id).where(FileErasure.storage_scope == scope).order_by(FileErasure.created_at, FileErasure.file_id)
    if file_ids is not None:
        query = query.where(FileErasure.file_id.in_(file_ids))
    identifiers = session.exec(query.limit(min(20, max(1, maximum)))).all()
    session.rollback()
    erased = 0
    for identifier in identifiers:
        retained = session.get(File, identifier) is not None
        session.rollback()
        if retained:
            logging.getLogger(__name__).warning("A retained file prevents private storage erasure")
            continue
        try:
            store.delete_file_sync(identifier)
        except Exception:
            logging.getLogger(__name__).warning("A removed file awaits private storage erasure")
            continue
        session.execute(delete(FileErasure).where(FileErasure.file_id == identifier,
                                                FileErasure.storage_scope == scope))
        session.commit()
        erased += 1
    return erased
