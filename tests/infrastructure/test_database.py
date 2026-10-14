"""The guarantees that make check-then-write safe on SQLite.

A use case such as "is this email taken? then insert" is only correct if
no other writer can slip in between the check and the write.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from worktime.infrastructure.database import Database


@pytest.fixture
def database(database_url: str) -> Iterator[Database]:
    database = Database(database_url)
    with database.write() as session:
        session.execute(text("CREATE TABLE note (body TEXT NOT NULL)"))
    yield database
    database.dispose()


@pytest.fixture
def impatient_database(database: Database, database_url: str) -> Iterator[Database]:
    """A second handle on the same file that gives up on a lock quickly."""
    other = Database(database_url, busy_timeout_seconds=0.05)
    yield other
    other.dispose()


def count_notes(database: Database) -> int:
    with database.read() as session:
        count: int = session.execute(text("SELECT count(*) FROM note")).scalar_one()
        return count


def test_a_write_takes_the_lock_before_its_first_statement(
    database: Database, impatient_database: Database
) -> None:
    with database.write() as first:
        # The first transaction has only looked so far, not written.
        first.execute(text("SELECT count(*) FROM note"))

        with (
            pytest.raises(OperationalError, match="database is locked"),
            impatient_database.write() as second,
        ):
            second.execute(text("SELECT count(*) FROM note"))


def test_a_read_is_not_blocked_by_a_write_in_progress(
    database: Database, impatient_database: Database
) -> None:
    with database.write() as writer:
        writer.execute(text("INSERT INTO note VALUES ('uncommitted')"))

        assert count_notes(impatient_database) == 0

    assert count_notes(impatient_database) == 1


def test_a_failed_write_leaves_nothing_behind(database: Database) -> None:
    with pytest.raises(RuntimeError), database.write() as session:
        session.execute(text("INSERT INTO note VALUES ('discarded')"))
        raise RuntimeError("something went wrong")

    assert count_notes(database) == 0
