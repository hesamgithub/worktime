"""Helpers shared by the tests."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from worktime.domain.clocking import Session

VIENNA = ZoneInfo("Europe/Vienna")

# The first week of March 2026, the week most tests take place in.
_WEEKDAYS = {
    "Sun": "2026-03-01",
    "Mon": "2026-03-02",
    "Tue": "2026-03-03",
    "Wed": "2026-03-04",
    "Thu": "2026-03-05",
    "Fri": "2026-03-06",
    "Sat": "2026-03-07",
}


def at(text: str, zone: ZoneInfo = VIENNA) -> datetime:
    """A local moment, expressed in UTC.

    Written as "Mon 08:00" for a day of the first week of March 2026, or as
    "2026-10-25 02:30" for any other day.
    """
    day, clock_time = text.split()
    local = datetime.fromisoformat(f"{_WEEKDAYS.get(day, day)}T{clock_time}")
    return local.replace(tzinfo=zone).astimezone(UTC)


def session(text: str, zone: ZoneInfo = VIENNA) -> Session:
    """A session written as "Mon 08:00 - Mon 17:00"."""
    start, end = text.split(" - ")
    return Session(at(start, zone), at(end, zone))


class FakeClock:
    """A clock that shows whatever time a test sets it to."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def move_to(self, now: datetime) -> None:
        self.now = now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class Recorder:
    """Clocks an employee in and out through the API at given local times."""

    def __init__(self, client: TestClient, clock: FakeClock, employee_id: int) -> None:
        self._client = client
        self._clock = clock
        self._employee_id = employee_id

    def clock_in(self, moment: str) -> None:
        self._clock_at(moment, "clock-in")

    def clock_out(self, moment: str) -> None:
        self._clock_at(moment, "clock-out")

    def work(self, start: str, end: str) -> None:
        self.clock_in(start)
        self.clock_out(end)

    def _clock_at(self, moment: str, action: str) -> None:
        self._clock.move_to(at(moment))
        response = self._client.post(f"/employees/{self._employee_id}/{action}")
        assert response.status_code == 201, response.text
