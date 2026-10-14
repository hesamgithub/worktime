"""Renders the database schema as SQL.

The tables are defined once, by the domain's stored classes. Running this module prints them as
SQL, which is how schema.sql in the project root is produced:

    python -m worktime.infrastructure.schema > schema.sql
"""

from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateIndex, CreateTable

from worktime.infrastructure.database import Base

_HEADER = """\
-- Database schema of worktime, for SQLite.
--
-- Generated from the domain's stored classes in src/worktime/domain/
-- with: python -m worktime.infrastructure.schema > schema.sql
--
-- The application creates these tables itself when it starts, so this file
-- does not have to be applied by hand. All timestamps are stored in UTC.
"""


def render_schema() -> str:
    dialect = sqlite.dialect()
    statements = []
    for table in Base.metadata.sorted_tables:
        statements.append(CreateTable(table).compile(dialect=dialect))
        statements += [
            CreateIndex(index).compile(dialect=dialect)
            for index in sorted(table.indexes, key=lambda index: str(index.name))
        ]
    body = "\n\n".join(f"{_tidy(str(statement))};" for statement in statements)
    return f"{_HEADER}\n{body}\n"


def _tidy(statement: str) -> str:
    """Drop the blank edges and trailing spaces the SQL compiler leaves."""
    return "\n".join(line.rstrip() for line in statement.strip().splitlines())


if __name__ == "__main__":
    print(render_schema(), end="")
