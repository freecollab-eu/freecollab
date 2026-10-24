"""Bounded local-wall recurrence and native busy-time computation.

No calendar-provider requests, private cross-channel discovery, or Matrix
plaintext are involved. A series has at most 366 valid occurrences.
"""
from calendar import monthrange
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def instant(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("A UTC offset is required")
    return result.astimezone(UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _dates(fields):
    """Yield (canonical occurrence identity, start, end), skipping DST gaps.

    Wall-clock recurrences keep the original local time; overlaps use the first
    occurrence. Monthly dates absent in a month are skipped, never clamped.
    """
    zone = ZoneInfo(fields.timezone)
    start = date.fromisoformat(fields.starts_at) if fields.all_day else instant(fields.starts_at).astimezone(zone)
    duration = (date.fromisoformat(fields.ends_at) - start) if fields.all_day else instant(fields.ends_at) - instant(fields.starts_at)
    rule = fields.recurrence
    wanted = rule.count if rule else 1
    step = rule.interval if rule else 1
    emitted = 0
    # At most 732 attempted monthly candidates are needed for 366 valid dates;
    # the larger bound also covers local daylight-saving gaps without a hang.
    for index in range(wanted * 3 + 3):
        if emitted >= wanted:
            break
        try:
            if not rule or rule.frequency in {"daily", "weekly"}:
                days = index * step * (7 if rule and rule.frequency == "weekly" else 1)
                candidate = start + timedelta(days=days)
            else:
                month = start.year * 12 + start.month - 1 + index * step
                year, month = divmod(month, 12)
                month += 1
                if start.day > monthrange(year, month)[1]:
                    continue
                candidate = start.replace(year=year, month=month)
            if fields.all_day:
                yield candidate.isoformat(), candidate, candidate + duration
            else:
                if index == 0:
                    # An explicitly supplied offset selects the exact first
                    # instant, including the second autumn overlap occurrence.
                    candidate = instant(fields.starts_at).astimezone(zone)
                else:
                    candidate = candidate.replace(fold=0)
                    if candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != candidate.replace(tzinfo=None):
                        continue
                candidate = candidate.astimezone(UTC)
                yield iso(candidate), candidate, candidate + duration
            emitted += 1
        except (ValueError, OverflowError):
            raise ValueError("Calendar recurrence exceeds the supported date range") from None


def validate_exceptions(fields):
    from .integration_content import CalendarFields
    if not fields.recurrence:
        raise ValueError("Occurrence exceptions require a recurring series")
    originals = {identity for identity, _, _ in _dates(fields)}
    seen = set()
    for item in fields.exceptions:
        identity = item.original_start if fields.all_day else iso(instant(item.original_start))
        if identity not in originals or identity in seen:
            raise ValueError("Occurrence exceptions must identify distinct dates in this series")
        item.original_start = identity
        seen.add(identity)
        if not item.cancelled:
            base = fields.model_dump(exclude={"recurrence", "exceptions"})
            overrides = item.model_dump(exclude={"original_start", "cancelled"}, exclude_none=True)
            if base.get("field_references"):
                base["field_references"] = {name: refs for name, refs in base["field_references"].items()
                                            if name not in overrides or overrides[name] == base.get(name)}
            base.update(overrides)
            normalized = CalendarFields.model_validate(base)
            if item.starts_at is not None:
                item.starts_at, item.ends_at = normalized.starts_at, normalized.ends_at


def expand_occurrences(fields, start: datetime, end: datetime, limit: int = 366) -> list[dict]:
    from .integration_content import CalendarFields
    if not isinstance(fields, CalendarFields):
        fields = CalendarFields.model_validate(fields)
    if start.tzinfo is None or end.tzinfo is None or end <= start or not 1 <= limit <= 366:
        raise ValueError("Use an ordered, timezone-aware occurrence window")
    exceptions = {item.original_start: item for item in fields.exceptions}
    base = fields.model_dump(exclude={"recurrence", "exceptions"})
    zone = ZoneInfo(fields.timezone)
    result = []
    for identity, occurrence_start, occurrence_end in _dates(fields):
        override = exceptions.get(identity)
        if override and override.cancelled:
            continue
        payload = {**base, "starts_at": occurrence_start.isoformat() if fields.all_day else iso(occurrence_start),
                   "ends_at": occurrence_end.isoformat() if fields.all_day else iso(occurrence_end)}
        if override:
            overrides = override.model_dump(exclude={"original_start", "cancelled"}, exclude_none=True)
            if payload.get("field_references"):
                payload["field_references"] = {name: refs for name, refs in payload["field_references"].items()
                                               if name not in overrides or overrides[name] == payload.get(name)}
                if not payload["field_references"]:
                    payload.pop("field_references")
            payload.update(overrides)
        begins = datetime.combine(date.fromisoformat(payload["starts_at"]), time(), zone) if fields.all_day else instant(payload["starts_at"])
        ends = datetime.combine(date.fromisoformat(payload["ends_at"]), time(), zone) if fields.all_day else instant(payload["ends_at"])
        if begins < end and ends > start:
            result.append({**payload, "original_start": identity})
    return sorted(result, key=lambda row: row["starts_at"])[:limit]
