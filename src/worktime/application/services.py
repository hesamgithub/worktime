"""The use cases. Every public method of a service is one transaction.

Services work with the database session directly. Objects loaded in a
writing transaction are saved when it commits, so changing them is enough.
"""

from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from worktime.config import settings
from worktime.domain.clocking import (
    ClockEvent,
    ClockEventType,
    Session,
    pair_events,
    within_working_hours,
)
from worktime.domain.employee import Employee
from worktime.domain.errors import ConflictError, NotFoundError
from worktime.domain.reporting import EmployeeReport, Statistics, check_period
from worktime.domain.workday import DayOverride, Workday, evaluate_workdays
from worktime.infrastructure.database import Database

Clock = Callable[[], datetime]
"""Tells the current moment, as a time-zone-aware datetime in UTC."""


def system_clock() -> datetime:
    return datetime.now(UTC)


class EmployeeService:
    def __init__(self, database: Database, clock: Clock) -> None:
        self._database = database
        self._clock = clock

    def create(
        self,
        *,
        name: str,
        email: str,
        time_zone: str | None = None,
        standard_daily_minutes: int | None = None,
    ) -> Employee:
        employee = Employee.create(
            name=name,
            email=email,
            time_zone=settings.default_time_zone if time_zone is None else time_zone,
            standard_daily_minutes=(
                settings.default_standard_daily_minutes
                if standard_daily_minutes is None
                else standard_daily_minutes
            ),
        )
        with self._database.write() as db:
            _require_free_email(db, employee)
            db.add(employee)
            db.flush()
            return employee

    def get(self, employee_id: int) -> Employee:
        with self._database.read() as db:
            return _require_employee(db, employee_id)

    def list_all(self, *, include_inactive: bool = False) -> list[Employee]:
        query = select(Employee).order_by(Employee.id)
        if not include_inactive:
            query = query.where(Employee.deactivated_at.is_(None))
        with self._database.read() as db:
            return list(db.scalars(query))

    def update(
        self,
        employee_id: int,
        *,
        name: str | None = None,
        email: str | None = None,
        time_zone: str | None = None,
        standard_daily_minutes: int | None = None,
    ) -> Employee:
        with self._database.write() as db:
            employee = _require_employee(db, employee_id)
            employee.change(
                name=name,
                email=email,
                time_zone=time_zone,
                standard_daily_minutes=standard_daily_minutes,
            )
            _require_free_email(db, employee)
            return employee

    def deactivate(self, employee_id: int) -> Employee:
        with self._database.write() as db:
            employee = _require_employee(db, employee_id)
            employee.deactivate(self._clock())
            return employee

    def reactivate(self, employee_id: int) -> Employee:
        with self._database.write() as db:
            employee = _require_employee(db, employee_id)
            employee.reactivate()
            return employee


class ClockService:
    """Records clock-ins and clock-outs at the moment they happen.

    The clock is open during working hours only. Within them it does not
    judge whether an event fits the ones before it: a forgotten or doubled
    event is recorded all the same and sorted out when the hours are
    evaluated.
    """

    def __init__(self, database: Database, clock: Clock) -> None:
        self._database = database
        self._clock = clock

    def record(self, employee_id: int, type: ClockEventType) -> ClockEvent:
        """Record a clock-in or clock-out now.

        Every event is stored as it comes, without looking at the ones before
        it. A double click is recorded too; it is recognised and set aside
        when the hours are evaluated.
        """
        with self._database.write() as db:
            employee = _require_employee(db, employee_id)
            if not employee.is_active:
                raise ConflictError(f"Employee {employee_id} is deactivated.")

            event = ClockEvent.record(employee_id, type, self._clock())
            if not within_working_hours(event.occurred_at, _zone(employee)):
                raise ConflictError(
                    "Clocking is only possible during working hours, "
                    f"{settings.working_hours_start:%H:%M} to "
                    f"{settings.working_hours_end:%H:%M}."
                )

            db.add(event)
            db.flush()
            return event

    def list_events(
        self,
        employee_id: int,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[ClockEvent]:
        with self._database.read() as db:
            _require_employee(db, employee_id)
            return _events(db, employee_id, start=start, end=end)


class OverrideService:
    """Replaces what the clock recorded for a day with sessions entered by hand."""

    def __init__(self, database: Database, clock: Clock) -> None:
        self._database = database
        self._clock = clock

    def get(self, employee_id: int, day: date) -> DayOverride:
        with self._database.read() as db:
            _require_employee(db, employee_id)
            return _require_override(db, employee_id, day)

    def set(
        self,
        employee_id: int,
        day: date,
        *,
        sessions: Iterable[Session],
        reason: str | None = None,
    ) -> tuple[DayOverride, Workday]:
        """Override a day, replacing any earlier override for it.

        Returns the override and the day as it is evaluated with it.
        """
        now = self._clock()
        with self._database.write() as db:
            employee = _require_employee(db, employee_id)
            override = DayOverride.create(
                employee_id=employee_id,
                day=day,
                sessions=sessions,
                reason=reason,
                time_zone=_zone(employee),
                now=now,
            )
            earlier = _override(db, employee_id, day)
            if earlier is not None:
                db.delete(earlier)
                db.flush()
            db.add(override)
            db.flush()
            [workday] = _evaluate(db, employee, day, day, now)
            return override, workday

    def remove(self, employee_id: int, day: date) -> None:
        """Remove a day's override, so that its clock events apply again."""
        with self._database.write() as db:
            _require_employee(db, employee_id)
            db.delete(_require_override(db, employee_id, day))


class ReportService:
    """Evaluates recorded hours. Everything is calculated when it is asked for."""

    def __init__(self, database: Database, clock: Clock) -> None:
        self._database = database
        self._clock = clock

    def employee_report(
        self, employee_id: int, first_day: date, last_day: date
    ) -> EmployeeReport:
        check_period(first_day, last_day)
        now = self._clock()
        with self._database.read() as db:
            employee = _require_employee(db, employee_id)
            return _report(db, employee, first_day, last_day, now)

    def statistics(self, first_day: date, last_day: date) -> Statistics:
        """The reports of all employees for the same period.

        Deactivated employees are included as long as they have a record in
        the period, so that past periods stay complete.
        """
        check_period(first_day, last_day)
        now = self._clock()
        with self._database.read() as db:
            employees = list(db.scalars(select(Employee).order_by(Employee.id)))
            reports = [
                _report(db, employee, first_day, last_day, now)
                for employee in employees
            ]
        return Statistics([r for r in reports if r.employee.is_active or r.workdays])


def _require_employee(db: DbSession, employee_id: int) -> Employee:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise NotFoundError(f"Employee {employee_id} does not exist.")
    return employee


def _require_free_email(db: DbSession, employee: Employee) -> None:
    # Without no_autoflush the session would first try to save the changed
    # employee, and the database would refuse a taken email before this
    # check could report it properly.
    with db.no_autoflush:
        holder = db.scalars(
            select(Employee).where(Employee.email == employee.email)
        ).one_or_none()
    if holder is not None and holder is not employee:
        raise ConflictError(f"The email address {employee.email} is already in use.")


def _override(db: DbSession, employee_id: int, day: date) -> DayOverride | None:
    return db.scalars(
        select(DayOverride).where(
            DayOverride.employee_id == employee_id, DayOverride.day == day
        )
    ).one_or_none()


def _require_override(db: DbSession, employee_id: int, day: date) -> DayOverride:
    override = _override(db, employee_id, day)
    if override is None:
        raise NotFoundError(f"Employee {employee_id} has no override for {day}.")
    return override


def _events(
    db: DbSession,
    employee_id: int,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[ClockEvent]:
    """An employee's events in order of time, from start to before end."""
    query = (
        select(ClockEvent)
        .where(ClockEvent.employee_id == employee_id)
        .order_by(ClockEvent.occurred_at, ClockEvent.id)
    )
    if start is not None:
        query = query.where(ClockEvent.occurred_at >= start)
    if end is not None:
        query = query.where(ClockEvent.occurred_at < end)
    return list(db.scalars(query))


def _zone(employee: Employee) -> ZoneInfo:
    return ZoneInfo(employee.time_zone)


def _local_midnight(day: date, time_zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, time_zone).astimezone(UTC)


def _evaluate(
    db: DbSession, employee: Employee, first_day: date, last_day: date, now: datetime
) -> list[Workday]:
    """Load what was recorded for an employee and evaluate the given days."""
    time_zone = _zone(employee)

    # Events are only ever paired within one day, so those of the period's
    # own days are all that is needed.
    events = _events(
        db,
        employee.id,
        start=_local_midnight(first_day, time_zone),
        end=_local_midnight(last_day + timedelta(days=1), time_zone),
    )
    overrides = db.scalars(
        select(DayOverride).where(
            DayOverride.employee_id == employee.id,
            DayOverride.day >= first_day,
            DayOverride.day <= last_day,
        )
    )
    return evaluate_workdays(
        pair_events(events, now=now, time_zone=time_zone),
        time_zone=time_zone,
        standard_daily=timedelta(minutes=employee.standard_daily_minutes),
        first_day=first_day,
        last_day=last_day,
        overrides={override.day: override for override in overrides},
    )


def _report(
    db: DbSession, employee: Employee, first_day: date, last_day: date, now: datetime
) -> EmployeeReport:
    time_zone = _zone(employee)
    today = now.astimezone(time_zone).date()
    todays_events = _events(db, employee.id, start=_local_midnight(today, time_zone))
    return EmployeeReport(
        employee=employee,
        workdays=_evaluate(db, employee, first_day, last_day, now),
        clocked_in_since=pair_events(
            todays_events, now=now, time_zone=time_zone
        ).open_since,
    )
