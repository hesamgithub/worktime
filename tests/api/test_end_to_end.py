"""One story per feature, told through the HTTP API.

The rules themselves are tested in tests/domain. These tests show that
the layers are connected: a request goes through the router, the service,
the database and the calculation, and the right answer comes back.
"""

from typing import Any

from fastapi.testclient import TestClient

from tests.support import FakeClock, Recorder, at

WEEK = {"from": "2026-03-02", "to": "2026-03-08"}  # Monday to Sunday


def hire(client: TestClient, name: str, **details: Any) -> int:
    email = f"{name.split()[0].lower()}@example.com"
    response = client.post("/employees", json={"name": name, "email": email} | details)
    assert response.status_code == 201, response.text
    employee_id: int = response.json()["id"]
    return employee_id


def monday(start: str, end: str) -> dict[str, str]:
    """A session on Monday 2 March, in Vienna time, as the API expects it."""
    return {
        "start": f"2026-03-02T{start}:00+01:00",
        "end": f"2026-03-02T{end}:00+01:00",
    }


def summary(client: TestClient, employee_id: int) -> dict[str, Any]:
    response = client.get(f"/employees/{employee_id}/summary", params=WEEK)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_an_employee_is_created_changed_deactivated_and_brought_back(
    client: TestClient,
) -> None:
    ada = hire(client, "Ada Lovelace")
    assert client.get(f"/employees/{ada}").json() == {
        "id": ada,
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "time_zone": "Europe/Vienna",  # the configured defaults
        "standard_daily_minutes": 480,
        "active": True,
        "deactivated_at": None,
    }

    # Validation: the email must be unique and the time zone must exist.
    taken = client.post(
        "/employees", json={"name": "Imposter", "email": "ADA@example.com"}
    )
    assert taken.status_code == 409
    unknown_zone = client.patch(f"/employees/{ada}", json={"time_zone": "Mars/Base"})
    assert unknown_zone.status_code == 422

    changed = client.patch(f"/employees/{ada}", json={"standard_daily_minutes": 360})
    assert changed.json()["standard_daily_minutes"] == 360
    assert client.get(f"/employees/{ada}").json()["standard_daily_minutes"] == 360

    # Deleting deactivates: hidden from the list, but kept.
    assert client.delete(f"/employees/{ada}").status_code == 204
    assert client.get("/employees").json() == []
    assert client.get(f"/employees/{ada}").json()["active"] is False

    assert client.post(f"/employees/{ada}/reactivate").json()["active"] is True
    assert len(client.get("/employees").json()) == 1


def test_the_clock_is_open_during_working_hours_and_accepts_any_sequence(
    client: TestClient, clock: FakeClock
) -> None:
    ada = hire(client, "Ada Lovelace")

    # Before 06:00 the clock is closed.
    clock.move_to(at("Mon 05:59"))
    early = client.post(f"/employees/{ada}/clock-in")
    assert early.status_code == 409
    assert early.json() == {
        "detail": "Clocking is only possible during working hours, 06:00 to 20:00."
    }

    # During the day anything is recorded: out without having clocked in,
    # in twice in a row.
    clock.move_to(at("Mon 08:00"))
    assert client.post(f"/employees/{ada}/clock-out").status_code == 201
    assert client.post(f"/employees/{ada}/clock-in").status_code == 201
    clock.advance(hours=1)
    assert client.post(f"/employees/{ada}/clock-in").status_code == 201

    # A double click is recorded too; evaluation sets it aside later.
    clock.advance(seconds=20)
    assert client.post(f"/employees/{ada}/clock-in").status_code == 201

    # At 20:00 sharp the clock still accepts; a second later it is closed.
    clock.move_to(at("Mon 20:00"))
    assert client.post(f"/employees/{ada}/clock-out").status_code == 201
    clock.advance(seconds=1)
    assert client.post(f"/employees/{ada}/clock-out").status_code == 409

    events = client.get(f"/employees/{ada}/clock-events").json()
    assert [(event["type"], event["occurred_at"]) for event in events] == [
        ("OUT", "2026-03-02T07:00:00Z"),
        ("IN", "2026-03-02T07:00:00Z"),
        ("IN", "2026-03-02T08:00:00Z"),
        ("IN", "2026-03-02T08:00:20Z"),
        ("OUT", "2026-03-02T19:00:00Z"),
    ]

    # Someone unknown or deactivated is turned away at any time.
    clock.move_to(at("Tue 08:00"))
    assert client.post("/employees/99/clock-in").status_code == 404
    client.delete(f"/employees/{ada}")
    assert client.post(f"/employees/{ada}/clock-in").status_code == 409


def test_working_hours_follow_the_employees_time_zone(
    client: TestClient, clock: FakeClock
) -> None:
    in_vienna = hire(client, "Ada Lovelace")
    in_new_york = hire(client, "Grace Hopper", time_zone="America/New_York")

    # 08:00 in Vienna is 02:00 in New York.
    clock.move_to(at("Mon 08:00"))

    assert client.post(f"/employees/{in_vienna}/clock-in").status_code == 201
    assert client.post(f"/employees/{in_new_york}/clock-in").status_code == 409


def test_a_forgotten_clock_out_is_put_right_with_an_override(
    client: TestClient, clock: FakeClock
) -> None:
    ada = hire(client, "Ada Lovelace")
    Recorder(client, clock, ada).clock_in("Mon 08:00")  # and never out
    clock.move_to(at("Wed 12:00"))
    override = f"/employees/{ada}/overrides/2026-03-02"

    # Before: the day is incomplete, says what is missing, and counts for nothing.
    before = summary(client, ada)
    [day] = before["workdays"]
    assert day["status"] == "incomplete"
    assert day["worked_minutes"] is None
    assert [(e["type"], e["missing"]) for e in day["needs_attention"]] == [
        ("IN", "clock_out")
    ]
    assert before["totals"]["worked_minutes"] == 0
    assert before["totals"]["incomplete_days"] == 1

    # An override is refused if sessions overlap or leave the working hours.
    overlapping = client.put(
        override,
        json={"sessions": [monday("08:00", "12:30"), monday("12:00", "17:00")]},
    )
    assert overlapping.status_code == 422
    assert "must not overlap" in overlapping.json()["detail"]
    too_late = client.put(override, json={"sessions": [monday("08:00", "21:00")]})
    assert too_late.status_code == 422
    assert "within the working hours" in too_late.json()["detail"]

    # The whole day is sent, and takes the place of the clock events.
    accepted = client.put(
        override,
        json={
            "sessions": [monday("08:00", "12:00"), monday("12:30", "17:00")],
            "reason": "Forgot to clock out",
        },
    )
    assert accepted.status_code == 200
    after = summary(client, ada)
    [day] = after["workdays"]
    assert day["status"] == "overridden"
    assert day["worked_minutes"] == 510
    assert day["overtime_minutes"] == 30
    assert day["break_minutes"] == 30
    assert after["totals"]["overridden_days"] == 1

    # The clock event itself was never touched.
    assert len(client.get(f"/employees/{ada}/clock-events").json()) == 1

    # Removing the override brings back what the clock recorded.
    assert client.delete(override).status_code == 204
    assert summary(client, ada)["workdays"][0]["status"] == "incomplete"


def test_a_week_of_work_is_summarised_day_by_day(
    client: TestClient, clock: FakeClock
) -> None:
    ada = hire(client, "Ada Lovelace")
    record = Recorder(client, clock, ada)
    record.work("Mon 08:00", "Mon 12:00")
    record.work("Mon 12:30", "Mon 17:30")  # nine hours with a break
    record.work("Tue 08:00", "Tue 16:00")  # exactly the standard
    record.work("Wed 09:00", "Wed 15:00")  # two hours short
    record.work("Thu 06:00", "Thu 20:00")  # all of the working hours
    record.clock_in("Fri 08:00")  # still at work
    clock.move_to(at("Fri 10:00"))

    body = summary(client, ada)

    assert [
        (
            day["date"],
            day["status"],
            day["worked_minutes"],
            day["overtime_minutes"],
            day["shortfall_minutes"],
            day["break_minutes"],
        )
        for day in body["workdays"]
    ] == [
        ("2026-03-02", "complete", 540, 60, 0, 30),
        ("2026-03-03", "complete", 480, 0, 0, 0),
        ("2026-03-04", "complete", 360, 0, 120, 0),
        ("2026-03-05", "complete", 840, 360, 0, 0),
        ("2026-03-06", "in_progress", None, None, None, None),
    ]
    assert body["workdays"][0]["sessions"] == [
        {
            "start": "2026-03-02T07:00:00Z",
            "end": "2026-03-02T11:00:00Z",
            "worked_minutes": 240,
        },
        {
            "start": "2026-03-02T11:30:00Z",
            "end": "2026-03-02T16:30:00Z",
            "worked_minutes": 300,
        },
    ]
    assert body["clocked_in_since"] == "2026-03-06T07:00:00Z"
    assert body["totals"] == {
        "worked_minutes": 2220,
        "overtime_minutes": 420,
        "shortfall_minutes": 120,
        "break_minutes": 30,
        "counted_days": 4,
        "overridden_days": 0,
        "incomplete_days": 0,
        "in_progress_days": 1,
    }


def test_statistics_compare_all_employees(client: TestClient, clock: FakeClock) -> None:
    ada = Recorder(client, clock, hire(client, "Ada Lovelace"))
    grace = Recorder(client, clock, hire(client, "Grace Hopper"))
    linus = Recorder(client, clock, hire(client, "Linus Torvalds"))
    ada.work("Mon 08:00", "Mon 17:00")  # one hour of overtime
    ada.work("Tue 08:00", "Tue 16:00")
    grace.work("Mon 09:00", "Mon 15:00")  # two hours short
    linus.clock_in("Mon 08:00")  # the clock-out is forgotten
    clock.move_to(at("Sat 12:00"))

    response = client.get("/statistics", params=WEEK)

    assert response.status_code == 200
    body = response.json()
    assert [
        (
            row["employee"]["name"],
            row["totals"]["worked_minutes"],
            row["totals"]["counted_days"],
            row["totals"]["incomplete_days"],
        )
        for row in body["employees"]
    ] == [
        ("Ada Lovelace", 1020, 2, 0),
        ("Grace Hopper", 360, 1, 0),
        ("Linus Torvalds", 0, 0, 1),
    ]
    assert body["totals"]["worked_minutes"] == 1380
    assert body["totals"]["overtime_minutes"] == 60
    assert body["totals"]["shortfall_minutes"] == 120
    assert body["totals"]["incomplete_days"] == 1
    # Linus has no counted day, so he does not pull the averages down.
    assert body["averages"] == {
        "worked_minutes_per_day": 460,
        "worked_minutes_per_employee": 690,
        "overtime_minutes_per_employee": 30,
    }
