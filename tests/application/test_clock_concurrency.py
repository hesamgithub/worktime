from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from tests.support import FakeClock
from worktime.application.services import ClockService, EmployeeService, ReportService
from worktime.domain.clocking import ClockEventType
from worktime.infrastructure.database import Database

SIMULTANEOUS_REQUESTS = 8


@pytest.fixture
def database(database_url: str) -> Iterator[Database]:
    database = Database(database_url)
    database.create_schema()
    yield database
    database.dispose()


def test_simultaneous_clock_ins_are_all_recorded_and_count_as_one(
    database: Database, clock: FakeClock
) -> None:
    employees = EmployeeService(database, clock)
    clock_service = ClockService(database, clock)
    employee_id = employees.create(name="Ada Lovelace", email="ada@example.com").id
    start_together = Barrier(SIMULTANEOUS_REQUESTS)

    def clock_in() -> None:
        start_together.wait()
        clock_service.record(employee_id, ClockEventType.IN)

    with ThreadPoolExecutor(SIMULTANEOUS_REQUESTS) as pool:
        for future in [pool.submit(clock_in) for _ in range(SIMULTANEOUS_REQUESTS)]:
            future.result()  # raises if any request failed

    # Nobody is turned away, and every event is kept as it came.
    assert len(clock_service.list_events(employee_id)) == SIMULTANEOUS_REQUESTS

    # When the hours are read, the repeats are one clock-in, not a muddle
    # of unpaired ones.
    today = clock.now.date()
    report = ReportService(database, clock).employee_report(employee_id, today, today)
    [day] = report.workdays
    assert report.clocked_in_since == clock.now
    assert day.unpaired == ()
