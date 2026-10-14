import re
from datetime import datetime
from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from worktime.domain.base import Base, UtcDateTime
from worktime.domain.errors import RuleViolationError

MINUTES_PER_DAY = 24 * 60

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Employee(Base):
    """A person whose working hours are recorded.

    Employees are never deleted, only deactivated, so that their recorded
    hours remain available for evaluation.
    """

    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    email: Mapped[str] = mapped_column(String, unique=True)
    time_zone: Mapped[str] = mapped_column(String)
    standard_daily_minutes: Mapped[int]
    deactivated_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    @classmethod
    def create(
        cls, *, name: str, email: str, time_zone: str, standard_daily_minutes: int
    ) -> Self:
        employee = cls(
            name=name,
            email=email,
            time_zone=time_zone,
            standard_daily_minutes=standard_daily_minutes,
        )
        employee._normalise_and_validate()
        return employee

    @property
    def is_active(self) -> bool:
        return self.deactivated_at is None

    def change(
        self,
        *,
        name: str | None = None,
        email: str | None = None,
        time_zone: str | None = None,
        standard_daily_minutes: int | None = None,
    ) -> None:
        """Replace the given details; anything left out stays as it is."""
        if name is not None:
            self.name = name
        if email is not None:
            self.email = email
        if time_zone is not None:
            self.time_zone = time_zone
        if standard_daily_minutes is not None:
            self.standard_daily_minutes = standard_daily_minutes
        self._normalise_and_validate()

    def deactivate(self, now: datetime) -> None:
        if self.is_active:
            self.deactivated_at = now

    def reactivate(self) -> None:
        self.deactivated_at = None

    def _normalise_and_validate(self) -> None:
        self.name = self.name.strip()
        self.email = self.email.strip().lower()

        if not self.name:
            raise RuleViolationError("The name must not be empty.")
        if not _EMAIL_PATTERN.match(self.email):
            raise RuleViolationError(f"'{self.email}' is not an email address.")
        if not _is_time_zone(self.time_zone):
            raise RuleViolationError(f"'{self.time_zone}' is not a known time zone.")
        if not 0 < self.standard_daily_minutes <= MINUTES_PER_DAY:
            raise RuleViolationError(
                "The standard daily working time must be between 1 and "
                f"{MINUTES_PER_DAY} minutes."
            )


def _is_time_zone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True
