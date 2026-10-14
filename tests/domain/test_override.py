"""What an override of a day must look like to be accepted.

Working hours are 06:00 to 20:00; see worktime/config.py.
"""

from datetime import date

import pytest

from tests.support import VIENNA, at, session
from worktime.domain.errors import RuleViolationError
from worktime.domain.workday import DayOverride

MONDAY = date(2026, 3, 2)


def declare(day: date, *sessions: str, now: str = "Wed 12:00") -> DayOverride:
    return DayOverride.create(
        employee_id=1,
        day=day,
        sessions=[session(text) for text in sessions],
        reason=None,
        time_zone=VIENNA,
        now=at(now),
    )


# Each case: the day, its sessions, and the reason for the refusal.
REFUSED = {
    "sessions overlap": (
        MONDAY,
        ["Mon 08:00 - Mon 12:30", "Mon 12:00 - Mon 17:00"],
        "must not overlap",
    ),
    "a session ends before it starts": (
        MONDAY,
        ["Mon 17:00 - Mon 08:00"],
        "end after it starts",
    ),
    "a session starts before working hours": (
        MONDAY,
        ["Mon 05:59 - Mon 12:00"],
        "within the working hours of 2026-03-02, 06:00 to 20:00",
    ),
    "a session ends after working hours": (
        MONDAY,
        ["Mon 12:00 - Mon 20:01"],
        "within the working hours",
    ),
    "a session belongs to another day": (
        MONDAY,
        ["Tue 08:00 - Tue 16:00"],
        "within the working hours of 2026-03-02",
    ),
    "the day is not over yet": (
        date(2026, 3, 4),  # the Wednesday on which the override is made
        ["Wed 08:00 - Wed 09:00"],
        "not over yet",
    ),
}


@pytest.mark.parametrize(
    ("day", "sessions", "reason"), REFUSED.values(), ids=REFUSED.keys()
)
def test_an_override_is_refused_when(
    day: date, sessions: list[str], reason: str
) -> None:
    with pytest.raises(RuleViolationError, match=reason):
        declare(day, *sessions)


def test_sessions_may_touch_fill_the_working_hours_and_come_in_any_order() -> None:
    override = declare(MONDAY, "Mon 12:00 - Mon 20:00", "Mon 06:00 - Mon 12:00")

    assert [s.start for s in override.sessions] == [at("Mon 06:00"), at("Mon 12:00")]
    assert override.sessions[-1].end == at("Mon 20:00")


def test_whether_a_day_is_over_is_judged_in_the_employees_time_zone() -> None:
    # Half past midnight on Tuesday in Vienna is still Monday in UTC.
    override = declare(MONDAY, "Mon 08:00 - Mon 16:00", now="Tue 00:30")

    assert override.day == MONDAY
