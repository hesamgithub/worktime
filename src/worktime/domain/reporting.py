"""Adding up workdays, for one employee and across all of them."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from worktime.domain.employee import Employee
from worktime.domain.errors import RuleViolationError
from worktime.domain.workday import DayStatus, Workday

MAX_PERIOD_DAYS = 366

_NO_TIME = timedelta(0)


def check_period(first_day: date, last_day: date) -> None:
    """A period to report on runs from a first to a last day, both included."""
    if first_day > last_day:
        raise RuleViolationError("The period must not end before it starts.")
    if (last_day - first_day).days >= MAX_PERIOD_DAYS:
        raise RuleViolationError(
            f"The period must not be longer than {MAX_PERIOD_DAYS} days."
        )


@dataclass(frozen=True)
class Totals:
    """Figures added up over a number of workdays.

    Only days that count contribute time: complete days and overridden days.
    The other days are merely counted, so that a reader can tell how much
    the totals leave out.
    """

    worked: timedelta = _NO_TIME
    breaks: timedelta = _NO_TIME
    overtime: timedelta = _NO_TIME
    shortfall: timedelta = _NO_TIME

    # Days that contribute to the times above, overridden ones included.
    counted_days: int = 0
    overridden_days: int = 0
    incomplete_days: int = 0
    in_progress_days: int = 0

    def __add__(self, other: "Totals") -> "Totals":
        return Totals(
            worked=self.worked + other.worked,
            breaks=self.breaks + other.breaks,
            overtime=self.overtime + other.overtime,
            shortfall=self.shortfall + other.shortfall,
            counted_days=self.counted_days + other.counted_days,
            overridden_days=self.overridden_days + other.overridden_days,
            incomplete_days=self.incomplete_days + other.incomplete_days,
            in_progress_days=self.in_progress_days + other.in_progress_days,
        )


def total_up(workdays: Iterable[Workday]) -> Totals:
    totals = Totals()
    for workday in workdays:
        if workday.figures is None:
            totals += Totals(
                incomplete_days=int(workday.status is DayStatus.INCOMPLETE),
                in_progress_days=int(workday.status is DayStatus.IN_PROGRESS),
            )
        else:
            totals += Totals(
                worked=workday.figures.worked,
                breaks=workday.figures.breaks,
                overtime=workday.figures.overtime,
                shortfall=workday.figures.shortfall,
                counted_days=1,
                overridden_days=int(workday.status is DayStatus.OVERRIDDEN),
            )
    return totals


@dataclass(frozen=True)
class EmployeeReport:
    """One employee's recorded hours over a period."""

    employee: Employee
    workdays: list[Workday]
    clocked_in_since: datetime | None

    @property
    def totals(self) -> Totals:
        return total_up(self.workdays)


@dataclass(frozen=True)
class Statistics:
    """The reports of all employees over the same period, side by side."""

    employees: list[EmployeeReport]

    @property
    def totals(self) -> Totals:
        return sum((report.totals for report in self.employees), Totals())

    @property
    def clocked_in(self) -> int:
        """How many employees are clocked in right now."""
        return sum(r.clocked_in_since is not None for r in self.employees)

    @property
    def average_worked_per_day(self) -> timedelta | None:
        """Time worked per counted day, over all employees."""
        return _average(self.totals.worked, self.totals.counted_days)

    @property
    def average_worked_per_employee(self) -> timedelta | None:
        return _average(self.totals.worked, self._employees_with_counted_days)

    @property
    def average_overtime_per_employee(self) -> timedelta | None:
        return _average(self.totals.overtime, self._employees_with_counted_days)

    @property
    def _employees_with_counted_days(self) -> int:
        # Someone without a single counted day would only dilute the averages.
        return sum(r.totals.counted_days > 0 for r in self.employees)


def _average(total: timedelta, count: int) -> timedelta | None:
    return total / count if count else None
