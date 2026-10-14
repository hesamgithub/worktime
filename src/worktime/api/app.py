"""Assembles the application: database, services, routes and error handling."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging
import sqlite3

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

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
        try:
            yield
        finally:
            database.dispose()

    app = FastAPI(
        title="worktime",
        description="Record and evaluate employees' working hours.",
        version=__version__,
        lifespan=lifespan,
    )
    app.include_router(router)

    @app.exception_handler(OperationalError)
    async def handle_database_error(
        _request: Request, error: OperationalError
    ) -> JSONResponse:
        code = getattr(error.orig, "sqlite_errorcode", None)
        if code is not None and code & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "Database is busy. Please try again."},
                headers={"Retry-After": "1"},
            )
        logging.getLogger(__name__).error(
            "Database operation failed", exc_info=error
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Database operation failed."},
        )

    @app.exception_handler(DomainError)
    async def handle_domain_error(
        _request: Request, error: DomainError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=_STATUS_CODES.get(type(error), status.HTTP_400_BAD_REQUEST),
            content={"detail": str(error)},
        )

    return app
