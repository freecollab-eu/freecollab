"""Bound ZIP directory allocation before handing an archive to ZipFile."""
import struct


def bounded_zip_directory(raw: bytes, *, max_entries: int, label: str = "Archive") -> bytes:
    """Validate actual central-directory records, including dishonest counts.

    ZIP64, split archives and encrypted extra fields are unsupported. Strip an
    optional comment so ZipFile's later EOCD search uses this checked directory.
    No ZipInfo objects or uncompressed file contents are allocated here.
    """
    def invalid(detail):
        raise ValueError(detail)

    if len(raw) < 22:
        invalid("Invalid ZIP directory")
    floor = max(0, len(raw) - 65557)
    at, end = len(raw), -1
    while at > floor:
        at = raw.rfind(b"PK\x05\x06", floor, at)
        if at < 0:
            break
        if at + 22 <= len(raw) and at + 22 + struct.unpack_from("<H", raw, at + 20)[0] == len(raw):
            end = at
            break
    if end < 0:
        invalid("Invalid ZIP end record")
    disk, directory_disk, disk_count, count, size, start = struct.unpack_from("<4H2I", raw, end + 4)
    if disk or directory_disk or disk_count != count or count == 0xffff or start == 0xffffffff or size == 0xffffffff:
        invalid("Split and ZIP64 archives are unsupported")
    if not count or count > max_entries or start + size != end:
        invalid(label + " exceeds the directory bound")
    cursor, actual = start, 0
    while cursor < end:
        if actual >= max_entries or cursor + 46 > end or raw[cursor:cursor + 4] != b"PK\x01\x02":
            invalid(label + " exceeds the directory bound")
        name, extra, comment, entry_disk = struct.unpack_from("<4H", raw, cursor + 28)
        compressed, expanded = struct.unpack_from("<2I", raw, cursor + 20)
        local = struct.unpack_from("<I", raw, cursor + 42)[0]
        next_record = cursor + 46 + name + extra + comment
        if entry_disk or 0xffffffff in {compressed, expanded, local} or next_record > end:
            invalid("Invalid, split or ZIP64 directory entry")
        field, fields_end = cursor + 46 + name, cursor + 46 + name + extra
        while field < fields_end:
            if field + 4 > fields_end:
                invalid("Invalid ZIP extra field")
            kind, length = struct.unpack_from("<2H", raw, field)
            if kind in {1, 0x9901} or field + 4 + length > fields_end:
                invalid("ZIP64 and encrypted extra fields are unsupported")
            field += 4 + length
        actual += 1
        cursor = next_record
    if cursor != end or actual != count:
        invalid("ZIP directory count does not match its records")
    return raw if end + 22 == len(raw) else raw[:end + 20] + b"\x00\x00"
