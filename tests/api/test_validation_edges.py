"""Boundary inputs fail predictably without changing recorded hours."""

import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from tests.support import FakeClock, at


def hire(client: TestClient) -> int:
    response = client.post(
        "/employees", json={"name": "Ada", "email": "ada@example.com"}
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.parametrize("day", ["0001-01-01", "9999-12-31"])
def test_extreme_report_dates_are_rejected(client: TestClient, day: str) -> None:
    employee = hire(client)
    for path in (f"/employees/{employee}/summary", "/statistics"):
        response = client.get(path, params={"from": day, "to": day})
        assert response.status_code == 422


def test_override_seconds_are_rejected_without_replacing_existing_hours(
    client: TestClient, clock: FakeClock
) -> None:
    employee = hire(client)
    clock.move_to(at("Tue 12:00"))
    path = f"/employees/{employee}/overrides/2026-03-02"
    session = {
        "start": "2026-03-02T08:00:00+01:00",
        "end": "2026-03-02T17:00:00+01:00",
    }
    assert client.put(path, json={"sessions": [session]}).status_code == 200
    before = client.get(path).json()
    session["end"] = "2026-03-02T20:00:59+01:00"
    assert client.put(path, json={"sessions": [session]}).status_code == 422
    assert client.get(path).json() == before


def test_reversed_event_period_is_rejected(client: TestClient) -> None:
    employee = hire(client)
    response = client.get(
        f"/employees/{employee}/clock-events",
        params={"from": "2026-03-03T09:00:00Z", "to": "2026-03-02T09:00:00Z"},
    )
    assert response.status_code == 422


def test_busy_database_returns_retryable_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Exercise the HTTP mapping without waiting for the production lock timeout.
    def busy(*args: object, **kwargs: object) -> None:
        original = sqlite3.OperationalError("database is locked")
        original.sqlite_errorcode = sqlite3.SQLITE_BUSY
        raise OperationalError("BEGIN IMMEDIATE", {}, original)

    monkeypatch.setattr(client.app.state.employees, "create", busy)
    response = client.post(
        "/employees", json={"name": "Ada", "email": "ada@example.com"}
    )
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"


def test_totals_preserve_seconds_before_display_rounding(
    client: TestClient, clock: FakeClock
) -> None:
    employee = hire(client)
    for day in ("Mon", "Tue"):
        clock.move_to(at(f"{day} 08:00:00"))
        assert client.post(f"/employees/{employee}/clock-in").status_code == 201
        clock.advance(seconds=40)
        assert client.post(f"/employees/{employee}/clock-out").status_code == 201
    response = client.get(
        f"/employees/{employee}/summary",
        params={"from": "2026-03-02", "to": "2026-03-03"},
    )
    assert response.status_code == 200
    report = response.json()
    assert [day["worked_minutes"] for day in report["workdays"]] == [0, 0]
    assert report["totals"]["worked_minutes"] == 1
