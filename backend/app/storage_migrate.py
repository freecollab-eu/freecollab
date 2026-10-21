"""Explicit, resumable local-to-S3 copy and verification. Never deletes source."""
import argparse
import hashlib
import json
from pathlib import Path

from sqlmodel import Session, select

from .database import engine
from .models.file import File
from .services.storage import MAX_BLOB_BYTES, S3StorageService, StorageService
from .config import settings


def migrate(session, source, destination, *, copy=False):
    copied = verified = 0
    # Immutable file IDs/versions stay stable. Run during a maintenance window;
    # metadata is committed only once every selected blob has verified.
    rows = session.exec(select(File).order_by(File.id).limit(100001)).all()
    if len(rows) > 100000:
        raise ValueError("Migration is bounded to 100000 blobs; partition a larger installation explicitly")
    for row in rows:
        local = source.read_file(row.id, max_bytes=MAX_BLOB_BYTES, expected_size=row.size)
        if local is None:
            raise ValueError("A source blob is missing; nothing was deleted")
        remote = destination.read_file(row.id, max_bytes=MAX_BLOB_BYTES, expected_size=row.size)
        if remote is None:
            if not copy:
                raise ValueError("An object is not copied; rerun explicitly with --copy")
            try:
                destination.save_file_sync(row.id, local)
                copied += 1
            except FileExistsError:
                pass  # Another copy won; verification below still must pass.
            remote = destination.read_file(row.id, max_bytes=MAX_BLOB_BYTES, expected_size=row.size)
        if remote is None or hashlib.sha256(local).digest() != hashlib.sha256(remote).digest():
            raise ValueError("Source and destination checksums differ; nothing was deleted")
        verified += 1
    if copy:
        for row in rows:
            row.storage_path = destination.location(row.id)
            session.add(row)
        session.commit()
    return {"copied": copied, "verified": verified, "source_deleted": False,
            "metadata_updated": bool(copy)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Explicit local blob directory to copy; never removed")
    parser.add_argument("--copy", action="store_true", help="Perform conditional S3 writes and update verified blob locations")
    arguments = parser.parse_args()
    if not Path(arguments.source).is_absolute() or not Path(arguments.source).is_dir():
        parser.error("--source must be an existing absolute local blob directory")
    if settings.storage_backend != "s3":
        parser.error("Set STORAGE_BACKEND=s3 and the destination's S3 settings explicitly")
    try:
        with Session(engine) as session:
            result = migrate(session, StorageService(arguments.source), S3StorageService(), copy=arguments.copy)
        print(json.dumps(result))
    except Exception:
        # SDK/config exceptions must never dump operator credentials or endpoint
        # request headers. Detailed verification stays available through tests.
        parser.exit(1, "Storage migration did not verify every blob. Source data is retained; check configuration and rerun.\n")


if __name__ == "__main__":
    main()
