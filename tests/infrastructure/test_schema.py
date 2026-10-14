from pathlib import Path

from worktime.infrastructure.schema import render_schema

SCHEMA_FILE = Path(__file__).parents[2] / "schema.sql"


def test_the_schema_file_matches_the_table_definitions() -> None:
    # schema.sql is a deliverable that is easy to forget when a table
    # changes. If this fails, regenerate it:
    #     python -m worktime.infrastructure.schema > schema.sql
    assert SCHEMA_FILE.read_text() == render_schema()
