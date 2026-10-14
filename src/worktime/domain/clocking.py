"""Clock events, working hours, and how events are paired into sessions."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Self
from zoneinfo import ZoneInfo

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from worktime.config import settings
from worktime.domain.base import Base, UtcDateTime


class ClockEventType(StrEnum):
    IN = "IN"
    OUT = "OUT"


class ClockEvent(Base):
    """The fact that an employee clocked in or out at a certain moment.

    Clock events are only ever added. They are never changed or removed.
    """

    __tablename__ = "clock_events"
    __table_args__ = (
        CheckConstraint("type IN ('IN', 'OUT')", name="clock_event_type"),
        Index("ix_clock_events_employee_time", "employee_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    # One of ClockEventType's values, which compare equal to plain strings.
    type: Mapped[str] = mapped_column(String)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime)

    @classmethod
    def record(cls, employee_id: int, type: ClockEventType, now: datetime) -> Self:
        if now.tzinfo is None:
            raise ValueError("A time-zone-aware datetime is required.")
        occurred_at = now.astimezone(UTC).replace(microsecond=0)
        return cls(employee_id=employee_id, type=type, occurred_at=occurred_at)


@dataclass(frozen=True)
class Session:
    """An uninterrupted stretch of work, from a clock-in to its clock-out."""

    start: datetime
    end: datetime

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


@dataclass(frozen=True)
class Pairing:
    sessions: tuple[Session, ...]

    # Events whose counterpart is missing: a clock-in that was never
    # followed by a clock-out, or a clock-out that had no clock-in.
    unpaired: tuple[ClockEvent, ...]

    # Set while the employee is clocked in.
    open_since: datetime | None


def to_the_minute(moment: datetime) -> datetime:
    """Express a moment in UTC, dropping seconds and anything finer."""
    if moment.tzinfo is None:
        raise ValueError("A time-zone-aware datetime is required.")
    return moment.astimezone(UTC).replace(second=0, microsecond=0)


def working_hours_on(day: date, time_zone: ZoneInfo) -> tuple[datetime, datetime]:
    """The first and last moment of a day at which time can be recorded."""
    return (
        datetime.combine(day, settings.working_hours_start, time_zone).astimezone(UTC),
        datetime.combine(day, settings.working_hours_end, time_zone).astimezone(UTC),
    )


def within_working_hours(moment: datetime, time_zone: ZoneInfo) -> bool:
    opens, closes = working_hours_on(moment.astimezone(time_zone).date(), time_zone)
    return opens <= moment <= closes


def pair_events(
    events: Iterable[ClockEvent], *, now: datetime, time_zone: ZoneInfo
) -> Pairing:
    """Pair each clock-out with the most recent clock-in of the same day.

    Days are calendar days in the employee's time zone. Any sequence of
    events is accepted. Whatever cannot be paired is reported as unpaired
    rather than treated as an error.
    """

    def day_of(event: ClockEvent) -> date:
        return event.occurred_at.astimezone(time_zone).date()

    sessions: list[Session] = []
    unpaired: list[ClockEvent] = []
    pending_in: ClockEvent | None = None

    for event in _without_repeats(events):
        if event.type == ClockEventType.IN:
            if pending_in is not None:
                unpaired.append(pending_in)
            pending_in = event
        elif pending_in is None:
            unpaired.append(event)
        elif day_of(event) != day_of(pending_in):
            unpaired += [pending_in, event]
            pending_in = None
        else:
            sessions.append(Session(pending_in.occurred_at, event.occurred_at))
            pending_in = None

    open_since = None
    if pending_in is not None:
        # Once working hours are over, the clock-out can no longer follow.
        _, closes = working_hours_on(day_of(pending_in), time_zone)
        if now > closes:
            unpaired.append(pending_in)
        else:
            open_since = pending_in.occurred_at

    return Pairing(tuple(sessions), tuple(unpaired), open_since)


def _without_repeats(events: Iterable[ClockEvent]) -> list[ClockEvent]:
    """Order the events by time and drop repeats of the same action.

    A repeat is measured against the event just before it, so a burst of
    identical actions collapses into its first one.
    """
    kept: list[ClockEvent] = []
    previous: ClockEvent | None = None
    for event in sorted(events, key=lambda e: (e.occurred_at, e.id or 0)):
        is_repeat = (
            previous is not None
            and event.type == previous.type
            and event.occurred_at - previous.occurred_at <= settings.repeat_window
        )
        if not is_repeat:
            kept.append(event)
        previous = event
    return kept
