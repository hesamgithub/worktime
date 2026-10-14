"""The values that shape how the application behaves.

Each one can be set through an environment variable named WORKTIME_ followed
by the setting's name in capitals, for example WORKTIME_WORKING_HOURS_START.
Whatever is not set keeps the default given below.

The environment is read once, when this module is first imported. A value
that is not valid stops the application from starting.
"""

from datetime import time, timedelta
from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WORKTIME_", frozen=True)

    # The database is a file, named relative to the directory the
    # application is started from.
    database_url: str = "sqlite:///worktime.db"

    # How long a writer waits for another writer to finish before giving up.
    database_busy_timeout_seconds: float = Field(5.0, gt=0, allow_inf_nan=False)

    # Applied to new employees that do not specify their own.
    default_time_zone: str = "Europe/Vienna"
    default_standard_daily_minutes: int = Field(8 * 60, ge=1, le=24 * 60)

    # Working hours, in each employee's local time. Time can only be
    # recorded within them: the clock is closed outside, and an override
    # may not reach beyond them either. They lie within one calendar day, so
    # that no session can cross midnight.
    working_hours_start: time = time(6, 0)
    working_hours_end: time = time(20, 0)

    # The same clock action repeated within this time is a double click or
    # a retry, not a second event.
    repeat_window_minutes: float = Field(1, ge=0, allow_inf_nan=False)

    @property
    def repeat_window(self) -> timedelta:
        return timedelta(minutes=self.repeat_window_minutes)

    @field_validator("repeat_window_minutes")
    @classmethod
    def _representable_repeat_window(cls, value: float) -> float:
        try:
            timedelta(minutes=value)
        except OverflowError:
            raise ValueError("repeat window is too large") from None
        return value

    @field_validator("working_hours_start", "working_hours_end")
    @classmethod
    def _local_whole_minute(cls, value: time) -> time:
        if value.tzinfo is not None or value.second or value.microsecond:
            raise ValueError("working hours must be local times in whole minutes")
        return value

    @field_validator("default_time_zone")
    @classmethod
    def _known_time_zone(cls, name: str) -> str:
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"'{name}' is not a known time zone") from None
        return name

    @model_validator(mode="after")
    def _working_hours_within_one_day(self) -> Self:
        if self.working_hours_start >= self.working_hours_end:
            raise ValueError("working hours must start before they end")
        return self


settings = Settings()
