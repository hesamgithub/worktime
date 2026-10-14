"""Reading the settings from the environment."""

from datetime import time

import pytest
from pydantic import ValidationError

from worktime.config import Settings


def test_a_setting_can_be_changed_through_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKTIME_WORKING_HOURS_START", "09:00")

    settings = Settings()

    assert settings.working_hours_start == time(9, 0)
    assert settings.working_hours_end == time(20, 0)  # left at its default


@pytest.mark.parametrize(
    ("variables", "complaint"),
    [
        pytest.param(
            {"WORKTIME_WORKING_HOURS_START": "20:00", "WORKTIME_WORKING_HOURS_END": "06:00"},
            "must start before they end",
            id="working hours across midnight",
        ),
        pytest.param(
            {"WORKTIME_DEFAULT_TIME_ZONE": "Europe/Vienne"},
            "not a known time zone",
            id="an unknown time zone",
        ),
        pytest.param(
            {"WORKTIME_DEFAULT_STANDARD_DAILY_MINUTES": "0"},
            "greater than or equal to 1",
            id="a standard day of no time",
        ),
        pytest.param(
            {"WORKTIME_REPEAT_WINDOW_MINUTES": "nan"},
            "finite number",
            id="a repeat window that is not a number",
        ),
        pytest.param(
            {"WORKTIME_REPEAT_WINDOW_MINUTES": "1e300"},
            "too large",
            id="a repeat window that overflows",
        ),
        pytest.param(
            {"WORKTIME_WORKING_HOURS_START": "06:00:30"},
            "whole minutes",
            id="working hours with seconds",
        ),
        pytest.param(
            {"WORKTIME_WORKING_HOURS_START": "06:00:00+01:00"},
            "local times",
            id="working hours with an offset",
        ),
    ],
)
def test_an_invalid_value_stops_the_application_from_starting(
    monkeypatch: pytest.MonkeyPatch, variables: dict[str, str], complaint: str
) -> None:
    for name, value in variables.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError, match=complaint):
        Settings()
