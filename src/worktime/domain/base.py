"""What makes the domain's classes storable in the database."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    """Base class of every domain class that is stored as a table."""


class UtcDateTime(TypeDecorator[datetime]):
    """Stores moments in UTC and returns them time-zone-aware.

    SQLite has no time zone support of its own, so the offset is dropped on
    the way in and UTC is attached again on the way out.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Any
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("A time-zone-aware datetime is required.")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(
        self, value: datetime | None, dialect: Any
    ) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)
