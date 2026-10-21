"""Immutable local or S3-compatible blobs; no silent cross-backend fallback."""
import base64
import hashlib
import hmac
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from ..config import settings

MAX_BLOB_BYTES = 50 * 1024 * 1024
IDENTIFIER = re.compile(r"[0-9a-f]{32}\Z")


class StorageLimitError(ValueError):
    pass


def validate_id(file_id):
    if not isinstance(file_id, str) or not IDENTIFIER.fullmatch(file_id):
        raise ValueError("Invalid file ID")


def validate_bound(max_bytes, expected_size):
    if not isinstance(max_bytes, int) or not 0 <= max_bytes <= MAX_BLOB_BYTES:
        raise ValueError("Invalid storage read bound")
    if expected_size is not None and (not isinstance(expected_size, int) or not 0 <= expected_size <= max_bytes):
        raise StorageLimitError("Stored file exceeds requested bound")


class AsyncStorage:
    async def save_file(self, file_id: str, content: bytes) -> str:
        return await run_in_threadpool(self.save_file_sync, file_id, content)

    async def get_file(self, file_id: str) -> bytes | None:
        return await run_in_threadpool(self.read_file, file_id, MAX_BLOB_BYTES)

    async def delete_file(self, file_id: str) -> bool:
        return await run_in_threadpool(self.delete_file_sync, file_id)


class StorageService(AsyncStorage):
    """Original local backend, also used by isolated test installations."""
    backend = "local"

    def __init__(self, storage_path=None):
        self.storage_path = Path(storage_path or settings.storage_path).resolve()
        self.storage_path.mkdir(parents=True, exist_ok=True)

    def get_file_path(self, file_id: str) -> Path:
        validate_id(file_id)
        path = self.storage_path / file_id
        if path.is_symlink() or not path.resolve().is_relative_to(self.storage_path.resolve()):
            raise ValueError("File path escapes storage")
        return path

    def location(self, file_id):
        return str(self.get_file_path(file_id))

    def save_file_sync(self, file_id: str, content: bytes) -> str:
        if not isinstance(content, bytes) or len(content) > MAX_BLOB_BYTES:
            raise StorageLimitError("File exceeds storage limit")
        path = self.get_file_path(file_id)
        created = False
        try:
            with path.open("xb") as output:
                created = True
                output.write(content)
        except BaseException:
            if created:
                path.unlink(missing_ok=True)
            raise
        return str(path)

    def read_file(self, file_id, max_bytes=MAX_BLOB_BYTES, expected_size=None):
        validate_bound(max_bytes, expected_size)
        path = self.get_file_path(file_id)
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("Stored blob is not a regular file")
            if metadata.st_size > max_bytes:
                raise StorageLimitError("Stored file exceeds requested bound")
            if expected_size is not None and metadata.st_size != expected_size:
                raise ValueError("Stored file changed")
            content = source.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise StorageLimitError("Stored file exceeds requested bound")
        if len(content) != metadata.st_size or (expected_size is not None and len(content) != expected_size):
            raise ValueError("Stored file changed")
        return content

    def delete_file_sync(self, file_id):
        validate_id(file_id)
        # Unlink only the validated entry in our root; never follow a symlink
        # while erasing a blob. Reading/writing still rejects symlink entries.
        path = self.storage_path.resolve() / file_id
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False


class S3StorageService(AsyncStorage):
    """Real SigV4 S3 transport. Reads are bounded and checksum verified."""
    backend = "s3"

    def __init__(self, config=None):
        self.config = config or settings
        self.bucket = self.config.s3_bucket
        self.prefix = self.config.s3_key_prefix
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config
            def secret(name):
                value = getattr(self.config, name)
                return value.get_secret_value() if value else None
            self._client = boto3.client("s3", endpoint_url=self.config.s3_endpoint_url or None,
                region_name=self.config.s3_region, aws_access_key_id=secret("s3_access_key_id"),
                aws_secret_access_key=secret("s3_secret_access_key"), aws_session_token=secret("s3_session_token"),
                verify=self.config.s3_ca_bundle or True,
                config=Config(signature_version="s3v4", connect_timeout=10, read_timeout=30,
                    retries={"max_attempts": 2, "mode": "standard"},
                    s3={"addressing_style": "path"}))
            origin = urlsplit(self._client.meta.endpoint_url)
            expected = (origin.scheme, origin.hostname, origin.port)
            def pin_origin(request, **kwargs):
                actual = urlsplit(request.url)
                if (actual.scheme, actual.hostname, actual.port) != expected:
                    raise ValueError("Object storage attempted to leave its configured origin")
            self._client.meta.events.register("before-send.s3", pin_origin)
        return self._client

    def key(self, file_id):
        validate_id(file_id)
        return self.prefix + file_id

    def location(self, file_id):
        return "s3://" + self.bucket + "/" + self.key(file_id)

    def get_file_path(self, file_id):
        validate_id(file_id)
        raise ValueError("S3 blobs do not have local file paths")

    @staticmethod
    def _missing(error):
        return getattr(error, "response", {}).get("Error", {}).get("Code") in {"NoSuchKey", "NotFound", "404"}

    def save_file_sync(self, file_id, content):
        key = self.key(file_id)
        if not isinstance(content, bytes) or len(content) > MAX_BLOB_BYTES:
            raise StorageLimitError("File exceeds storage limit")
        digest = hashlib.sha256(content).digest()
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=content,
                ContentLength=len(content), ContentType="application/octet-stream",
                IfNoneMatch="*", ChecksumSHA256=base64.b64encode(digest).decode(),
                Metadata={"sha256": digest.hex()})
        except Exception as error:
            code = getattr(error, "response", {}).get("Error", {}).get("Code")
            if code in {"PreconditionFailed", "412", "ConditionalRequestConflict", "409"}:
                raise FileExistsError("Immutable blob already exists") from None
            raise HTTPException(503, "Object storage write is unavailable") from None
        return self.location(file_id)

    def read_file(self, file_id, max_bytes=MAX_BLOB_BYTES, expected_size=None):
        key = self.key(file_id)
        validate_bound(max_bytes, expected_size)
        try:
            result = self.client.get_object(Bucket=self.bucket, Key=key, ChecksumMode="ENABLED")
        except Exception as error:
            if self._missing(error):
                return None
            raise HTTPException(503, "Object storage read is unavailable") from None
        stream = result["Body"]
        try:
            size = result.get("ContentLength")
            if not isinstance(size, int) or size < 0:
                raise ValueError("Invalid stored object length")
            if size > max_bytes:
                raise StorageLimitError("Stored file exceeds requested bound")
            if expected_size is not None and size != expected_size:
                raise ValueError("Stored file changed")
            digest = result.get("Metadata", {}).get("sha256", "")
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("Stored file checksum metadata is unavailable")
            try:
                content = stream.read(max_bytes + 1)
            except Exception:
                raise HTTPException(503, "Object storage stream is unavailable") from None
            if len(content) != size or not hmac.compare_digest(hashlib.sha256(content).hexdigest(), digest):
                raise ValueError("Stored file checksum or length changed")
            return content
        finally:
            stream.close()

    def delete_file_sync(self, file_id):
        key = self.key(file_id)
        try:
            versions, key_marker, version_marker = [], None, None
            for _ in range(100):
                request = {"Bucket": self.bucket, "Prefix": key, "MaxKeys": 100}
                if key_marker is not None:
                    request["KeyMarker"] = key_marker
                if version_marker is not None:
                    request["VersionIdMarker"] = version_marker
                result = self.client.list_object_versions(**request)
                versions.extend(row for row in result.get("Versions", []) + result.get("DeleteMarkers", [])
                                if row.get("Key") == key)
                if not result.get("IsTruncated"):
                    break
                key_marker, version_marker = result.get("NextKeyMarker"), result.get("NextVersionIdMarker")
            else:
                raise ValueError("Too many object versions for bounded erasure")
            for row in versions:
                self.client.delete_object(Bucket=self.bucket, Key=key, VersionId=row["VersionId"])
            if not versions:
                try:
                    self.client.head_object(Bucket=self.bucket, Key=key)
                except Exception as error:
                    if self._missing(error):
                        return False
                    raise
                self.client.delete_object(Bucket=self.bucket, Key=key)
            remaining = self.client.list_object_versions(Bucket=self.bucket, Prefix=key, MaxKeys=100)
            if any(row.get("Key") == key for row in remaining.get("Versions", []) + remaining.get("DeleteMarkers", [])):
                raise ValueError("Object versions remain after deletion")
            return True
        except Exception:
            raise HTTPException(503, "Object storage erasure is unavailable; retained versions may remain") from None


storage_service = S3StorageService() if settings.storage_backend == "s3" else StorageService()
