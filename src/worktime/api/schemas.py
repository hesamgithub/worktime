"""The shapes of requests and responses. Durations are whole minutes."""

from datetime import date, datetime, timedelta
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict

from worktime.domain.clocking import ClockEvent, ClockEventType, Session
from worktime.domain.employee import Employee
from worktime.domain.reporting import EmployeeReport, Statistics, Totals
from worktime.domain.workday import DayOverride, DayStatus, Workday

_STRICT = ConfigDict(extra="forbid")


class EmployeeCreate(BaseModel):
    model_config = _STRICT

    name: str
    email: str
    time_zone: str | None = None
    standard_daily_minutes: int | None = None


class EmployeeUpdate(BaseModel):
    model_config = _STRICT

    name: str | None = None
    email: str | None = None
    time_zone: str | None = None
    standard_daily_minutes: int | None = None


class EmployeeResponse(BaseModel):
    id: int
    name: str
    email: str
    time_zone: str
    standard_daily_minutes: int
    active: bool
    deactivated_at: datetime | None

    @classmethod
    def from_domain(cls, employee: Employee) -> Self:
        assert employee.id is not None
        return cls(
            id=employee.id,
            name=employee.name,
            email=employee.email,
            time_zone=employee.time_zone,
            standard_daily_minutes=employee.standard_daily_minutes,
            active=employee.is_active,
            deactivated_at=employee.deactivated_at,
        )


class ClockEventResponse(BaseModel):
    id: int
    employee_id: int
    type: ClockEventType
    occurred_at: datetime

    @classmethod
    def from_domain(cls, event: ClockEvent) -> Self:
        assert event.id is not None
        return cls(
            id=event.id,
            employee_id=event.employee_id,
            type=event.type,
            occurred_at=event.occurred_at,
        )


class SessionSchema(BaseModel):
    """A session by its start and end, in requests and in responses."""

    model_config = _STRICT

    start: AwareDatetime
    end: AwareDatetime

    def to_domain(self) -> Session:
        return Session(self.start, self.end)


class OverrideBody(BaseModel):
    model_config = _STRICT

    sessions: list[SessionSchema]
    reason: str | None = None


class OverrideResponse(BaseModel):
    employee_id: int
    date: date
    sessions: list[SessionSchema]
    reason: str | None
    submitted_at: datetime

    @classmethod
    def from_domain(cls, override: DayOverride) -> Self:
        return cls(
            employee_id=override.employee_id,
            date=override.day,
            sessions=[
                SessionSchema(start=session.start, end=session.end)
                for session in override.sessions
            ],
            reason=override.reason,
            submitted_at=override.submitted_at,
        )


class WorkedSessionResponse(BaseModel):
    start: datetime
    end: datetime
    worked_minutes: int

    @classmethod
    def from_domain(cls, session: Session) -> Self:
        return cls(
            start=session.start,
            end=session.end,
            worked_minutes=_minutes(session.duration),
        )


class UnpairedEventResponse(BaseModel):
    event_id: int
    type: ClockEventType
    occurred_at: datetime
    missing: Literal["clock_in", "clock_out"]

    @classmethod
    def from_domain(cls, event: ClockEvent) -> Self:
        assert event.id is not None
        return cls(
            event_id=event.id,
            type=event.type,
            occurred_at=event.occurred_at,
            missing="clock_out" if event.type == ClockEventType.IN else "clock_in",
        )


class WorkdayResponse(BaseModel):
    """One day of an employee. The figures are null unless the day counts."""

    date: date
    status: DayStatus
    worked_minutes: int | None
    break_minutes: int | None
    overtime_minutes: int | None
    shortfall_minutes: int | None
    sessions: list[WorkedSessionResponse]
    needs_attention: list[UnpairedEventResponse]

    @classmethod
    def from_domain(cls, workday: Workday) -> Self:
        figures = workday.figures
        return cls(
            date=workday.date,
            status=workday.status,
            worked_minutes=_minutes(figures.worked) if figures else None,
            break_minutes=_minutes(figures.breaks) if figures else None,
            overtime_minutes=_minutes(figures.overtime) if figures else None,
            shortfall_minutes=_minutes(figures.shortfall) if figures else None,
            sessions=[
                WorkedSessionResponse.from_domain(session)
                for session in workday.sessions
            ],
            needs_attention=[
                UnpairedEventResponse.from_domain(event) for event in workday.unpaired
            ],
        )


class OverriddenDayResponse(BaseModel):
    override: OverrideResponse
    workday: WorkdayResponse


class TotalsResponse(BaseModel):
    """Times add up the days that count: complete and overridden ones."""

    worked_minutes: int
    overtime_minutes: int
    shortfall_minutes: int
    break_minutes: int
    counted_days: int
    overridden_days: int
    incomplete_days: int
    in_progress_days: int

    @classmethod
    def from_domain(cls, totals: Totals) -> Self:
        return cls(
            worked_minutes=_minutes(totals.worked),
            overtime_minutes=_minutes(totals.overtime),
            shortfall_minutes=_minutes(totals.shortfall),
            break_minutes=_minutes(totals.breaks),
            counted_days=totals.counted_days,
            overridden_days=totals.overridden_days,
            incomplete_days=totals.incomplete_days,
            in_progress_days=totals.in_progress_days,
        )


class EmployeeSummaryResponse(BaseModel):
    employee: EmployeeResponse
    period: dict[Literal["from", "to"], date]
    clocked_in_since: datetime | None
    totals: TotalsResponse
    workdays: list[WorkdayResponse]

    @classmethod
    def from_domain(
        cls, report: EmployeeReport, first_day: date, last_day: date
    ) -> Self:
        return cls(
            employee=EmployeeResponse.from_domain(report.employee),
            period={"from": first_day, "to": last_day},
            clocked_in_since=report.clocked_in_since,
            totals=TotalsResponse.from_domain(report.totals),
            workdays=[
                WorkdayResponse.from_domain(workday) for workday in report.workdays
            ],
        )


class EmployeeTotalsResponse(BaseModel):
    employee: EmployeeResponse
    clocked_in_since: datetime | None
    totals: TotalsResponse

    @classmethod
    def from_domain(cls, report: EmployeeReport) -> Self:
        return cls(
            employee=EmployeeResponse.from_domain(report.employee),
            clocked_in_since=report.clocked_in_since,
            totals=TotalsResponse.from_domain(report.totals),
        )


class StatisticsResponse(BaseModel):
    """Averages are null when there is no counted day to average over."""

    period: dict[Literal["from", "to"], date]
    clocked_in: int
    totals: TotalsResponse
    averages: dict[str, int | None]
    employees: list[EmployeeTotalsResponse]

    @classmethod
    def from_domain(
        cls, statistics: Statistics, first_day: date, last_day: date
    ) -> Self:
        return cls(
            period={"from": first_day, "to": last_day},
            clocked_in=statistics.clocked_in,
            totals=TotalsResponse.from_domain(statistics.totals),
            averages={
                "worked_minutes_per_day": _average(statistics.average_worked_per_day),
                "worked_minutes_per_employee": _average(
                    statistics.average_worked_per_employee
                ),
                "overtime_minutes_per_employee": _average(
                    statistics.average_overtime_per_employee
                ),
            },
            employees=[
                EmployeeTotalsResponse.from_domain(report)
                for report in statistics.employees
            ],
        )


def _minutes(duration: timedelta) -> int:
    return duration // timedelta(minutes=1)


def _average(duration: timedelta | None) -> int | None:
    return None if duration is None else _minutes(duration)
