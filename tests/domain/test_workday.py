"""How sessions are evaluated day by day.

Covers overtime, shortfall and breaks, and which days count at all.
"""

from datetime import date, timedelta

import pytest

from tests.support import VIENNA, at, session
from worktime.domain.clocking import ClockEvent, ClockEventType, Pairing
from worktime.domain.workday import (
    OverrideSession,
    DayOverride,
    DayStatus,
    Workday,
    evaluate_workdays,
)

STANDARD = timedelta(hours=8)
MONDAY = date(2026, 3, 2)
TUESDAY = date(2026, 3, 3)


def evaluate(
    *sessions: str,
    unpaired: tuple[str, ...] = (),
    open_since: str | None = None,
    overrides: dict[date, list[str]] | None = None,
) -> list[Workday]:
    """Evaluate sessions written as "Mon 08:00 - Mon 16:00"."""
    return evaluate_workdays(
        Pairing(
            sessions=tuple(session(text) for text in sessions),
            unpaired=tuple(_lone(text) for text in unpaired),
            open_since=at(open_since) if open_since else None,
        ),
        time_zone=VIENNA,
        standard_daily=STANDARD,
        first_day=date(2026, 1, 1),
        last_day=date(2026, 12, 31),
        overrides={
            day: DayOverride(
                employee_id=1,
                day=day,
                stored_sessions=[
                    OverrideSession(start=s.start, end=s.end)
                    for s in (session(text) for text in texts)
                ],
                reason=None,
                submitted_at=at("Sat 12:00"),
            )
            for day, texts in (overrides or {}).items()
        },
    )


def _lone(text: str) -> ClockEvent:
    type, moment = text.split(maxsplit=1)
    return ClockEvent(
        id=1, employee_id=1, type=ClockEventType(type), occurred_at=at(moment)
    )


def hours(value: float) -> timedelta:
    return timedelta(hours=value)


# Each case: what was worked on a Monday, then the day's figures in hours,
# for a standard day of eight hours.
#                                              worked overtime shortfall breaks
ONE_DAY = {
    "a standard day": (
        ["Mon 08:00 - Mon 16:00"],              8,     0,       0,        0),
    "time above the standard is overtime": (
        ["Mon 08:00 - Mon 17:30"],              9.5,   1.5,     0,        0),
    "time below it is shortfall": (
        ["Mon 08:00 - Mon 14:45"],              6.75,  0,       1.25,     0),
    "the gap between sessions is a break": (
        ["Mon 08:00 - Mon 12:00",
         "Mon 12:45 - Mon 17:00"],              8.25,  0.25,    0,        0.75),
    "a split day is still one day": (
        ["Mon 08:00 - Mon 10:00",
         "Mon 17:00 - Mon 19:00"],              4,     0,       4,        7),
    "the longest possible day": (
        ["Mon 06:00 - Mon 20:00"],              14,    6,       0,        0),
}  # fmt: skip


@pytest.mark.parametrize(
    ("worked", "total", "overtime", "shortfall", "breaks"),
    ONE_DAY.values(),
    ids=ONE_DAY.keys(),
)
def test_a_days_figures(
    worked: list[str], total: float, overtime: float, shortfall: float, breaks: float
) -> None:
    [day] = evaluate(*worked)

    assert day.date == MONDAY
    assert day.status is DayStatus.COMPLETE
    assert day.figures is not None
    assert day.figures.worked == hours(total)
    assert day.figures.overtime == hours(overtime)
    assert day.figures.shortfall == hours(shortfall)
    assert day.figures.breaks == hours(breaks)


def test_a_day_with_an_unpaired_event_is_incomplete_and_has_no_figures() -> None:
    monday, tuesday = evaluate(
        "Mon 13:00 - Mon 17:00", "Tue 08:00 - Tue 16:00", unpaired=("IN Mon 08:00",)
    )

    # Four hours were recorded for Monday, but nobody knows what the lone
    # clock-in stands for, so no figure is given at all.
    assert monday.status is DayStatus.INCOMPLETE
    assert monday.figures is None
    assert len(monday.sessions) == 1
    assert [event.type for event in monday.unpaired] == [ClockEventType.IN]
    # The neighbouring day is not affected.
    assert tuesday.status is DayStatus.COMPLETE
    assert tuesday.unpaired == ()


def test_a_day_is_in_progress_while_the_employee_is_clocked_in() -> None:
    [monday] = evaluate("Mon 08:00 - Mon 12:00", open_since="Mon 13:00")

    assert monday.status is DayStatus.IN_PROGRESS
    assert monday.figures is None


def test_an_override_takes_the_place_of_the_days_clock_events() -> None:
    monday, tuesday = evaluate(
        "Mon 13:00 - Mon 17:00",
        "Tue 08:00 - Tue 16:00",
        unpaired=("IN Mon 08:00",),
        overrides={MONDAY: ["Mon 08:00 - Mon 12:00", "Mon 12:30 - Mon 17:00"]},
    )

    assert monday.status is DayStatus.OVERRIDDEN
    assert monday.unpaired == ()
    assert [(s.start, s.end) for s in monday.sessions] == [
        (at("Mon 08:00"), at("Mon 12:00")),
        (at("Mon 12:30"), at("Mon 17:00")),
    ]
    assert monday.figures is not None
    assert monday.figures.worked == hours(8.5)
    # The neighbouring day is as the clock recorded it.
    assert tuesday.status is DayStatus.COMPLETE
    assert tuesday.date == TUESDAY


def test_a_day_overridden_with_no_work_is_not_measured_against_the_standard() -> None:
    [monday] = evaluate(unpaired=("OUT Mon 17:00",), overrides={MONDAY: []})

    assert monday.status is DayStatus.OVERRIDDEN
    assert monday.figures is not None
    assert monday.figures.worked == hours(0)
    assert monday.figures.shortfall == hours(0)
