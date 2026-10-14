"""Assembles the application: database, services, routes and error handling."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from worktime import __version__
from worktime.config import settings
from worktime.api.routes import router
from worktime.application.services import (
    Clock,
    ClockService,
    OverrideService,
    EmployeeService,
    ReportService,
    system_clock,
)
from worktime.domain.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    RuleViolationError,
)
from worktime.infrastructure.database import Database

_STATUS_CODES: dict[type[DomainError], int] = {
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
    RuleViolationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
}


def create_app(
    database_url: str = settings.database_url, clock: Clock = system_clock
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(database_url)
        database.create_schema()

        app.state.database = database
        app.state.employees = EmployeeService(database, clock)
        app.state.clock = ClockService(database, clock)
        app.state.overrides = OverrideService(database, clock)
        app.state.reports = ReportService(database, clock)
        yield
        database.dispose()

    app = FastAPI(
        title="worktime",
        description="Record and evaluate employees' working hours.",
        version=__version__,
        lifespan=lifespan,
    )
    app.include_router(router)

    @app.exception_handler(DomainError)
    async def handle_domain_error(
        _request: Request, error: DomainError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=_STATUS_CODES.get(type(error), status.HTTP_400_BAD_REQUEST),
            content={"detail": str(error)},
        )

    return app
