"""SQLite engine set-up and transaction handling."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, event, make_url, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from worktime.config import settings

# The domain's stored classes register their tables with Base when imported.
from worktime.domain import clocking, employee, workday  # noqa: F401
from worktime.domain.base import Base

_WRITE_OPTION = "worktime_write"


class Database:
    """Hands out sessions for read-only and for writing transactions.

    A writing transaction takes SQLite's write lock when it begins, before
    its first statement runs. Checks made inside it therefore cannot be
    invalidated by another writer before the transaction commits.
    """

    def __init__(
        self,
        url: str,
        busy_timeout_seconds: float = settings.database_busy_timeout_seconds,
    ) -> None:
        _ensure_parent_directory(url)
        self._engine = _create_engine(url, busy_timeout_seconds)
        self._read_sessions = sessionmaker(self._engine, expire_on_commit=False)
        self._write_sessions = sessionmaker(
            self._engine.execution_options(**{_WRITE_OPTION: True}),
            expire_on_commit=False,
        )

    def create_schema(self) -> None:
        Base.metadata.create_all(self._engine)

    @contextmanager
    def read(self) -> Iterator[Session]:
        """A transaction that never commits."""
        with self._read_sessions() as session:
            yield session

    @contextmanager
    def write(self) -> Iterator[Session]:
        """A transaction that commits on success and rolls back on error."""
        with self._write_sessions.begin() as session:
            yield session

    def is_healthy(self) -> bool:
        try:
            with self.read() as session:
                session.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return False
        return True

    def dispose(self) -> None:
        self._engine.dispose()


def _ensure_parent_directory(url: str) -> None:
    path = make_url(url).database
    if path and path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)


def _create_engine(url: str, busy_timeout_seconds: float) -> Engine:
    engine = create_engine(url, connect_args={"timeout": busy_timeout_seconds})
    event.listen(engine, "connect", _configure_connection)
    event.listen(engine, "begin", _begin_transaction)
    return engine


def _configure_connection(dbapi_connection: Any, _connection_record: Any) -> None:
    # Switch off the driver's implicit BEGIN, so that _begin_transaction
    # decides how each transaction starts.
    dbapi_connection.isolation_level = None
    cursor = dbapi_connection.cursor()
    # SQLite does not enforce foreign keys unless asked to, per connection.
    cursor.execute("PRAGMA foreign_keys = ON")
    # Write-ahead logging lets readers proceed while a write is in progress.
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.close()


def _begin_transaction(connection: Connection) -> None:
    is_write = connection.get_execution_options().get(_WRITE_OPTION, False)
    connection.exec_driver_sql("BEGIN IMMEDIATE" if is_write else "BEGIN")
