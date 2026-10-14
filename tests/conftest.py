from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.support import FakeClock
from worktime.api.app import create_app


@pytest.fixture
def clock() -> FakeClock:
    # A Monday morning.
    return FakeClock(datetime(2026, 3, 2, 9, 0, tzinfo=UTC))


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'worktime.db'}"


@pytest.fixture
def client(database_url: str, clock: FakeClock) -> Iterator[TestClient]:
    with TestClient(create_app(database_url, clock)) as client:
        yield client
