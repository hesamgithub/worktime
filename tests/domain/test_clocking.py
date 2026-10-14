"""How a sequence of clock events is read.

Within working hours the clock accepts every event, so pairing has to make
sense of any sequence: whatever fits together becomes a session, and
whatever is left over is reported as unpaired instead of being rejected.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tests.support import VIENNA, at, session
from worktime.domain.clocking import ClockEvent, ClockEventType, Pairing, pair_events

# Working hours are 06:00 to 20:00, and an action repeated within a minute
# is ignored; see worktime/config.py.


def events(*entries: str, zone: ZoneInfo = VIENNA) -> list[ClockEvent]:
    """Clock events written as "IN Mon 08:00", numbered in the order given."""
    return [
        ClockEvent(
            id=number,
            employee_id=1,
            type=ClockEventType(entry.split(maxsplit=1)[0]),
            occurred_at=at(entry.split(maxsplit=1)[1], zone),
        )
        for number, entry in enumerate(entries, start=1)
    ]


def described(found: Pairing) -> list[tuple[ClockEventType, datetime]]:
    return [(event.type, event.occurred_at) for event in found.unpaired]


def expected(*entries: str) -> list[tuple[ClockEventType, datetime]]:
    return [(event.type, event.occurred_at) for event in events(*entries)]


# Each case: the recorded events, the sessions they form, the events left over.
CASES = {
    "a clock-in and a clock-out make a session": (
        ["IN Mon 08:00", "OUT Mon 17:00"],
        ["Mon 08:00 - Mon 17:00"],
        [],
    ),
    "a break separates two sessions": (
        ["IN Mon 08:00", "OUT Mon 12:00", "IN Mon 12:45", "OUT Mon 17:00"],
        ["Mon 08:00 - Mon 12:00", "Mon 12:45 - Mon 17:00"],
        [],
    ),
    "double clock-in: the most recent one counts": (
        ["IN Mon 08:00", "IN Mon 08:20", "OUT Mon 12:00"],
        ["Mon 08:20 - Mon 12:00"],
        ["IN Mon 08:00"],
    ),
    "double clock-out: the first one counts": (
        ["IN Mon 08:00", "OUT Mon 12:00", "OUT Mon 12:10"],
        ["Mon 08:00 - Mon 12:00"],
        ["OUT Mon 12:10"],
    ),
    "a clock-out without a clock-in stays alone": (
        ["OUT Mon 17:00"],
        [],
        ["OUT Mon 17:00"],
    ),
    "a forgotten clock-out does not swallow the next day": (
        ["IN Mon 08:00", "IN Tue 08:05", "OUT Tue 16:30"],
        ["Tue 08:05 - Tue 16:30"],
        ["IN Mon 08:00"],
    ),
    "a forgotten clock-in leaves the clock-out alone": (
        ["IN Mon 08:00", "OUT Mon 17:00", "OUT Tue 17:00"],
        ["Mon 08:00 - Mon 17:00"],
        ["OUT Tue 17:00"],
    ),
    "events of different days never belong together": (
        ["IN Mon 19:00", "OUT Tue 07:00"],
        [],
        ["IN Mon 19:00", "OUT Tue 07:00"],
    ),
    "the whole of the working hours can be one session": (
        ["IN Mon 06:00", "OUT Mon 20:00"],
        ["Mon 06:00 - Mon 20:00"],
        [],
    ),
    "a stray event between two sessions leaves them untouched": (
        [
            "IN Mon 08:00",
            "OUT Mon 10:00",
            "OUT Mon 13:00",
            "IN Mon 17:00",
            "OUT Mon 19:00",
        ],
        ["Mon 08:00 - Mon 10:00", "Mon 17:00 - Mon 19:00"],
        ["OUT Mon 13:00"],
    ),
    "the same action a minute later is a repeat": (
        ["IN Mon 08:00", "IN Mon 08:01", "OUT Mon 12:00"],
        ["Mon 08:00 - Mon 12:00"],
        [],
    ),
    "clocking out and back in within a minute loses nothing": (
        ["IN Mon 08:00:10", "OUT Mon 08:00:30", "IN Mon 08:00:50", "OUT Mon 17:00"],
        ["Mon 08:00:10 - Mon 08:00:30", "Mon 08:00:50 - Mon 17:00"],
        [],
    ),
    "the same action two minutes later is not": (
        ["IN Mon 08:00", "IN Mon 08:02", "OUT Mon 12:00"],
        ["Mon 08:02 - Mon 12:00"],
        ["IN Mon 08:00"],
    ),
}


def pair(recorded: list[ClockEvent], now: str, zone: ZoneInfo = VIENNA) -> Pairing:
    return pair_events(recorded, now=at(now), time_zone=zone)


@pytest.mark.parametrize(
    ("recorded", "sessions", "left_over"), CASES.values(), ids=CASES.keys()
)
def test_events_are_paired_into_sessions(
    recorded: list[str], sessions: list[str], left_over: list[str]
) -> None:
    found = pair(events(*recorded), now="Sat 12:00")

    assert list(found.sessions) == [session(text) for text in sessions]
    assert described(found) == expected(*left_over)
    assert found.open_since is None


@pytest.mark.parametrize(
    ("now", "clocked_in", "left_over"),
    [
        pytest.param("Mon 11:00", True, [], id="during the day: clocked in"),
        pytest.param("Mon 20:00", True, [], id="at closing time: still clocked in"),
        pytest.param(
            "Mon 20:01", False, ["IN Mon 08:00"], id="after it: a forgotten clock-out"
        ),
    ],
)
def test_a_last_clock_in_stays_open_until_working_hours_end(
    now: str, clocked_in: bool, left_over: list[str]
) -> None:
    found = pair(events("IN Mon 08:00"), now=now)

    assert found.open_since == (at("Mon 08:00") if clocked_in else None)
    assert described(found) == expected(*left_over)


def test_the_same_day_is_judged_in_the_employees_time_zone() -> None:
    # 08:00 to 19:30 in New York is one working day there, although in UTC
    # the clock-out falls on the next date.
    new_york = ZoneInfo("America/New_York")
    recorded = events("IN Mon 08:00", "OUT Mon 19:30", zone=new_york)
    assert recorded[0].occurred_at.date() != recorded[1].occurred_at.date()

    found = pair(recorded, now="Sat 12:00", zone=new_york)

    assert len(found.sessions) == 1
    assert found.unpaired == ()
