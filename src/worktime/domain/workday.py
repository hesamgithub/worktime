"""Overrides of a day, and the evaluation of sessions day by day."""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from typing import Self
from zoneinfo import ZoneInfo

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from worktime.config import settings
from worktime.domain.base import Base, UtcDateTime
from worktime.domain.clocking import (
    ClockEvent,
    Pairing,
    Session,
    to_the_minute,
    working_hours_on,
)
from worktime.domain.errors import RuleViolationError

_NO_TIME = timedelta(0)


class DayOverride(Base):
    """The sessions an employee really worked on one day, entered by hand.

    An override states the whole day. It takes the place of that day's
    clock events when the day is evaluated; the clock events themselves
    stay untouched. A day has at most one override.
    """

    __tablename__ = "day_overrides"
    __table_args__ = (UniqueConstraint("employee_id", "work_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    day: Mapped[date] = mapped_column("work_date")
    reason: Mapped[str | None] = mapped_column(String)
    submitted_at: Mapped[datetime] = mapped_column(UtcDateTime)

    stored_sessions: Mapped[list["OverrideSession"]] = relationship(
        cascade="all, delete-orphan",
        order_by="OverrideSession.start",
        lazy="selectin",
    )

    @property
    def sessions(self) -> tuple[Session, ...]:
        return tuple(Session(s.start, s.end) for s in self.stored_sessions)

    @classmethod
    def create(
        cls,
        *,
        employee_id: int,
        day: date,
        sessions: Iterable[Session],
        reason: str | None,
        time_zone: ZoneInfo,
        now: datetime,
    ) -> Self:
        """Check an override against the rules and build it.

        An empty list of sessions is allowed and means that no work was
        done on that day.
        """

        def describe(session: Session) -> str:
            start = session.start.astimezone(time_zone)
            end = session.end.astimezone(time_zone)
            return f"{start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M}"

        if day >= now.astimezone(time_zone).date():
            raise RuleViolationError(
                f"{day} is not over yet. Only past days can be overridden."
            )

        sessions = tuple(sessions)
        if any(
            moment.second or moment.microsecond
            for session in sessions
            for moment in (session.start, session.end)
        ):
            raise RuleViolationError("Override session times must be whole minutes.")

        opens, closes = working_hours_on(day, time_zone)
        ordered = sorted(
            (Session(to_the_minute(s.start), to_the_minute(s.end)) for s in sessions),
            key=lambda session: session.start,
        )

        for session in ordered:
            if session.end <= session.start:
                raise RuleViolationError(
                    f"Each session must end after it starts ({describe(session)})."
                )
            if session.start < opens or session.end > closes:
                raise RuleViolationError(
                    f"Each session must lie within the working hours of {day}, "
                    f"{settings.working_hours_start:%H:%M} to "
                    f"{settings.working_hours_end:%H:%M} ({describe(session)})."
                )
        for earlier, later in pairwise(ordered):
            if later.start < earlier.end:
                raise RuleViolationError(
                    "Sessions must not overlap "
                    f"({describe(earlier)} and {describe(later)})."
                )

        reason = reason.strip() if reason else None
        return cls(
            employee_id=employee_id,
            day=day,
            stored_sessions=[OverrideSession(start=s.start, end=s.end) for s in ordered],
            reason=reason or None,
            submitted_at=to_the_minute(now),
        )


class OverrideSession(Base):
    """One session of an override, as it is stored."""

    __tablename__ = "override_sessions"
    __table_args__ = (
        CheckConstraint("ended_at > started_at", name="override_session_order"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    override_id: Mapped[int] = mapped_column(ForeignKey("day_overrides.id"))
    start: Mapped[datetime] = mapped_column("started_at", UtcDateTime)
    end: Mapped[datetime] = mapped_column("ended_at", UtcDateTime)


class DayStatus(StrEnum):
    # Every clock event of the day found its counterpart.
    COMPLETE = "complete"
    # The day was entered by hand; its clock events are set aside.
    OVERRIDDEN = "overridden"
    # A clock event of the day has no counterpart.
    INCOMPLETE = "incomplete"
    # The employee is clocked in.
    IN_PROGRESS = "in_progress"


@dataclass(frozen=True)
class DayFigures:
    worked: timedelta
    # Time not worked between the day's first clock-in and last clock-out.
    breaks: timedelta
    overtime: timedelta
    shortfall: timedelta


@dataclass(frozen=True)
class Workday:
    """One calendar day of an employee, in the employee's own time zone."""

    date: date
    status: DayStatus
    sessions: tuple[Session, ...]
    unpaired: tuple[ClockEvent, ...]

    # Only a complete or overridden day has figures. As long as something
    # about a day is unknown, any number given for it would be a guess.
    figures: DayFigures | None


def evaluate_workdays(
    pairing: Pairing,
    *,
    time_zone: ZoneInfo,
    standard_daily: timedelta,
    first_day: date,
    last_day: date,
    overrides: Mapping[date, DayOverride] | None = None,
) -> list[Workday]:
    """The days from first_day to last_day on which anything was recorded.

    Every session lies within one day, so each day stands on its own:
    replacing one with an override leaves the others as they were.
    """
    overrides = overrides or {}

    def day_of(moment: datetime) -> date:
        return moment.astimezone(time_zone).date()

    sessions: defaultdict[date, list[Session]] = defaultdict(list)
    for session in pairing.sessions:
        sessions[day_of(session.start)].append(session)

    unpaired: defaultdict[date, list[ClockEvent]] = defaultdict(list)
    for event in pairing.unpaired:
        unpaired[day_of(event.occurred_at)].append(event)

    in_progress = {day_of(pairing.open_since)} if pairing.open_since else set()

    workdays = []
    days = sessions.keys() | unpaired.keys() | in_progress | overrides.keys()
    for day in sorted(days):
        if not first_day <= day <= last_day:
            continue
        if day in overrides:
            overridden = overrides[day].sessions
            workdays.append(
                Workday(
                    date=day,
                    status=DayStatus.OVERRIDDEN,
                    sessions=overridden,
                    unpaired=(),
                    figures=_figures(overridden, standard_daily),
                )
            )
            continue
        if unpaired[day]:
            status = DayStatus.INCOMPLETE
        elif day in in_progress:
            status = DayStatus.IN_PROGRESS
        else:
            status = DayStatus.COMPLETE
        workdays.append(
            Workday(
                date=day,
                status=status,
                sessions=tuple(sessions[day]),
                unpaired=tuple(unpaired[day]),
                figures=(
                    _figures(sessions[day], standard_daily)
                    if status is DayStatus.COMPLETE
                    else None
                ),
            )
        )
    return workdays


def _figures(sessions: Sequence[Session], standard_daily: timedelta) -> DayFigures:
    worked = sum((session.duration for session in sessions), _NO_TIME)
    presence = (
        max(session.end for session in sessions)
        - min(session.start for session in sessions)
        if sessions
        else _NO_TIME
    )

    # A day on which no time was worked, such as one overridden to "no work",
    # is not measured against the standard.
    measured = worked > _NO_TIME
    return DayFigures(
        worked=worked,
        breaks=presence - worked,
        overtime=max(worked - standard_daily, _NO_TIME) if measured else _NO_TIME,
        shortfall=max(standard_daily - worked, _NO_TIME) if measured else _NO_TIME,
    )
