-- Database schema of worktime, for SQLite.
--
-- Generated from the domain's stored classes in src/worktime/domain/
-- with: python -m worktime.infrastructure.schema > schema.sql
--
-- The application creates these tables itself when it starts, so this file
-- does not have to be applied by hand. All timestamps are stored in UTC.

CREATE TABLE employees (
	id INTEGER NOT NULL,
	name VARCHAR NOT NULL,
	email VARCHAR NOT NULL,
	time_zone VARCHAR NOT NULL,
	standard_daily_minutes INTEGER NOT NULL,
	deactivated_at DATETIME,
	PRIMARY KEY (id),
	UNIQUE (email)
);

CREATE TABLE clock_events (
	id INTEGER NOT NULL,
	employee_id INTEGER NOT NULL,
	type VARCHAR NOT NULL,
	occurred_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT clock_event_type CHECK (type IN ('IN', 'OUT')),
	FOREIGN KEY(employee_id) REFERENCES employees (id)
);

CREATE INDEX ix_clock_events_employee_time ON clock_events (employee_id, occurred_at);

CREATE TABLE day_overrides (
	id INTEGER NOT NULL,
	employee_id INTEGER NOT NULL,
	work_date DATE NOT NULL,
	reason VARCHAR,
	submitted_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (employee_id, work_date),
	FOREIGN KEY(employee_id) REFERENCES employees (id)
);

CREATE TABLE override_sessions (
	id INTEGER NOT NULL,
	override_id INTEGER NOT NULL,
	started_at DATETIME NOT NULL,
	ended_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT override_session_order CHECK (ended_at > started_at),
	FOREIGN KEY(override_id) REFERENCES day_overrides (id)
);
