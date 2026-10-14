"""The HTTP endpoints. Each one hands the request to a service."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import AwareDatetime

from worktime.api.schemas import (
    ClockEventResponse,
    OverriddenDayResponse,
    OverrideBody,
    OverrideResponse,
    EmployeeCreate,
    EmployeeResponse,
    EmployeeSummaryResponse,
    EmployeeUpdate,
    StatisticsResponse,
    WorkdayResponse,
)
from worktime.application.services import (
    ClockService,
    OverrideService,
    EmployeeService,
    ReportService,
)
from worktime.domain.clocking import ClockEventType
from worktime.infrastructure.database import Database

router = APIRouter()


# The services are created once, when the application starts, and kept on
# the application. These functions fetch them for a request.


def _employees(request: Request) -> EmployeeService:
    service: EmployeeService = request.app.state.employees
    return service


def _clock(request: Request) -> ClockService:
    service: ClockService = request.app.state.clock
    return service


def _overrides(request: Request) -> OverrideService:
    service: OverrideService = request.app.state.overrides
    return service


def _reports(request: Request) -> ReportService:
    service: ReportService = request.app.state.reports
    return service


Employees = Annotated[EmployeeService, Depends(_employees)]
Clocking = Annotated[ClockService, Depends(_clock)]
Overrides = Annotated[OverrideService, Depends(_overrides)]
Reports = Annotated[ReportService, Depends(_reports)]

FirstDay = Annotated[date, Query(alias="from", description="First day of the period.")]
LastDay = Annotated[
    date, Query(alias="to", description="Last day of the period, included.")
]


# Service


@router.get("/health", tags=["service"])
def health(request: Request) -> dict[str, str]:
    database: Database = request.app.state.database
    if not database.is_healthy():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not reachable.",
        )
    return {"status": "ok"}


# Employees


@router.post("/employees", status_code=status.HTTP_201_CREATED, tags=["employees"])
def create_employee(body: EmployeeCreate, employees: Employees) -> EmployeeResponse:
    employee = employees.create(
        name=body.name,
        email=body.email,
        time_zone=body.time_zone,
        standard_daily_minutes=body.standard_daily_minutes,
    )
    return EmployeeResponse.from_domain(employee)


@router.get("/employees", tags=["employees"])
def list_employees(
    employees: Employees, include_inactive: bool = False
) -> list[EmployeeResponse]:
    return [
        EmployeeResponse.from_domain(employee)
        for employee in employees.list_all(include_inactive=include_inactive)
    ]


@router.get("/employees/{employee_id}", tags=["employees"])
def get_employee(employee_id: int, employees: Employees) -> EmployeeResponse:
    return EmployeeResponse.from_domain(employees.get(employee_id))


@router.patch("/employees/{employee_id}", tags=["employees"])
def update_employee(
    employee_id: int, body: EmployeeUpdate, employees: Employees
) -> EmployeeResponse:
    employee = employees.update(
        employee_id,
        name=body.name,
        email=body.email,
        time_zone=body.time_zone,
        standard_daily_minutes=body.standard_daily_minutes,
    )
    return EmployeeResponse.from_domain(employee)


@router.delete(
    "/employees/{employee_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["employees"],
)
def deactivate_employee(employee_id: int, employees: Employees) -> None:
    """Deactivate an employee. Their recorded hours are kept."""
    employees.deactivate(employee_id)


@router.post("/employees/{employee_id}/reactivate", tags=["employees"])
def reactivate_employee(employee_id: int, employees: Employees) -> EmployeeResponse:
    return EmployeeResponse.from_domain(employees.reactivate(employee_id))


# Clock

_CLOCK_NOTE = (
    "Records the event at the current moment. Outside working hours, in the "
    "employee's local time, the clock is closed and responds with 409."
)


@router.post(
    "/employees/{employee_id}/clock-in",
    status_code=status.HTTP_201_CREATED,
    description=_CLOCK_NOTE,
    tags=["clock"],
)
def clock_in(employee_id: int, clock: Clocking) -> ClockEventResponse:
    event = clock.record(employee_id, ClockEventType.IN)
    return ClockEventResponse.from_domain(event)


@router.post(
    "/employees/{employee_id}/clock-out",
    status_code=status.HTTP_201_CREATED,
    description=_CLOCK_NOTE,
    tags=["clock"],
)
def clock_out(employee_id: int, clock: Clocking) -> ClockEventResponse:
    event = clock.record(employee_id, ClockEventType.OUT)
    return ClockEventResponse.from_domain(event)


@router.get("/employees/{employee_id}/clock-events", tags=["clock"])
def list_clock_events(
    employee_id: int,
    clock: Clocking,
    start: Annotated[
        AwareDatetime | None,
        Query(alias="from", description="Only events at or after this moment."),
    ] = None,
    end: Annotated[
        AwareDatetime | None,
        Query(alias="to", description="Only events before this moment."),
    ] = None,
) -> list[ClockEventResponse]:
    events = clock.list_events(employee_id, start=start, end=end)
    return [ClockEventResponse.from_domain(event) for event in events]


# Overrides


@router.put("/employees/{employee_id}/overrides/{day}", tags=["overrides"])
def set_override(
    employee_id: int, day: date, body: OverrideBody, overrides: Overrides
) -> OverriddenDayResponse:
    """State the sessions really worked on a past day.

    The whole day is sent at once and takes the place of the day's clock
    events, which are kept. Sending an override again replaces the earlier
    one. An empty list of sessions means that no work was done.
    """
    override, workday = overrides.set(
        employee_id,
        day,
        sessions=[session.to_domain() for session in body.sessions],
        reason=body.reason,
    )
    return OverriddenDayResponse(
        override=OverrideResponse.from_domain(override),
        workday=WorkdayResponse.from_domain(workday),
    )


@router.get("/employees/{employee_id}/overrides/{day}", tags=["overrides"])
def get_override(
    employee_id: int, day: date, overrides: Overrides
) -> OverrideResponse:
    return OverrideResponse.from_domain(overrides.get(employee_id, day))


@router.delete(
    "/employees/{employee_id}/overrides/{day}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["overrides"],
)
def remove_override(employee_id: int, day: date, overrides: Overrides) -> None:
    """Remove a day's override, so that its clock events apply again."""
    overrides.remove(employee_id, day)


# Reports


@router.get("/employees/{employee_id}/summary", tags=["reports"])
def employee_summary(
    employee_id: int, first_day: FirstDay, last_day: LastDay, reports: Reports
) -> EmployeeSummaryResponse:
    """Evaluate an employee's recorded hours for a period of days.

    The days are calendar days in the employee's time zone. Only complete
    and overridden days contribute to the totals; incomplete days are listed
    with the events that need attention.
    """
    report = reports.employee_report(employee_id, first_day, last_day)
    return EmployeeSummaryResponse.from_domain(report, first_day, last_day)


@router.get("/statistics", tags=["reports"])
def statistics(
    first_day: FirstDay, last_day: LastDay, reports: Reports
) -> StatisticsResponse:
    """Compare the recorded hours of all employees for a period of days.

    Each employee's days are calendar days in their own time zone.
    Deactivated employees appear if they have a record in the period.
    """
    return StatisticsResponse.from_domain(
        reports.statistics(first_day, last_day), first_day, last_day
    )
