import os
import re
import aiofiles
from pathlib import Path
from ..config import settings


class StorageService:
    """Local file storage. Contents are stored without application encryption."""

    def __init__(self):
        self.storage_path = Path(settings.storage_path).resolve()
        self.storage_path.mkdir(parents=True, exist_ok=True)

    async def save_file(self, file_id: str, content: bytes) -> str:
        """Save file to storage and return the path."""
        file_path = self.get_file_path(file_id)
        created = False
        try:
            async with aiofiles.open(file_path, "xb") as f:
                created = True
                await f.write(content)
        except BaseException:
            # Failed/cancelled writes must not leave a partial immutable blob.
            # Never unlink an existing blob when exclusive creation failed.
            if created:
                file_path.unlink(missing_ok=True)
            raise
        return str(file_path)

    async def get_file(self, file_id: str) -> bytes | None:
        """Retrieve file content by ID."""
        file_path = self.get_file_path(file_id)
        if not file_path.exists():
            return None
        async with aiofiles.open(file_path, "rb") as f:
            return await f.read()

    async def delete_file(self, file_id: str) -> bool:
        """Delete file from storage."""
        file_path = self.get_file_path(file_id)
        if file_path.exists():
            os.remove(file_path)
            return True
        return False

    def get_file_path(self, file_id: str) -> Path:
        """Get the full path for a file."""
        if not re.fullmatch(r"[0-9a-f]{32}", file_id):
            raise ValueError("Invalid file ID")
        file_path = (self.storage_path / file_id).resolve()
        if not file_path.is_relative_to(self.storage_path):
            raise ValueError("File path escapes storage")
        return file_path


storage_service = StorageService()
