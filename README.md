# worktime

A small REST API for recording and evaluating employees' working hours.
Employees clock in and out during working hours; the API pairs those events
into sessions, evaluates them day by day, and reports time worked, overtime
and shortfall per employee and across all employees.

Built with FastAPI, SQLAlchemy and SQLite.

- [Central design decision](#central-design-decision-immutable-clocks-full-day-overrides)
- [Getting started](#getting-started)
- [Using the API](#using-the-api)
- [How hours are evaluated](#how-hours-are-evaluated)
- [Database schema](#database-schema)
- [Configuration](#configuration)
- [Design](#design)
- [What is tested, and why](#what-is-tested-and-why)
- [Limitations and possible extensions](#limitations-and-possible-extensions)

## Central design decision: immutable clocks, full-day overrides

### Terminology

- **Clock event**: an employee clocking in or out at a terminal, with a
  badge or through an online portal. It is **timestamped by the server** at
  the moment it happens.
- **Override**: the **complete working hours** of one day, declared later by the
  employee or a manager, for example to account for a forgotten clock-in or
  clock-out.

### The problem

Clock events do not always reflect the hours an employee actually worked.
Employees may forget to clock out, terminals may malfunction, or hours may
need to be adjusted for other reasons.

However, clock events are timestamped by the server when employees use
physical or online terminals. They represent what the system actually
recorded, and we want to preserve that history without modification.

The challenge is therefore: how do we allow arbitrary changes to working
hours without compromising the original records or introducing complex
reconciliation rules?

### The solution

We keep clock events and manually declared hours entirely separate.

- **Clock events are immutable.** They are never updated or deleted.
- **Clock events are interpreted only when hours are read.** Pairing them
  and spotting missing ones happens at read time, so the terminal never
  refuses an event because of earlier ones; a double clock-in is accepted.
  It refuses only outside working hours, or for an unknown or deactivated
  employee.
- **There are no corrections, only full-day overrides.** Each override
  declares the complete working hours for a day, independently of the
  recorded events.
- **Only one source is used per day.** If an override exists, it takes
  precedence entirely. Otherwise, the recorded clock events are used.
- **Only one override can exist for a day.** A new override replaces the
  previous one. (That is why a whole day's hours are declared at once, not
  partial corrections.)

**We never merge the two, even when an override bears no resemblance to the
recorded events.** An override is kept and validated on its own: it can only
be submitted for a past day, and its sessions must fall within that day's
working hours and must not overlap.

### Example

Suppose an employee's clock events indicate 09:00–12:00 and 13:00–17:00,
totalling 7 hours. They submit an override declaring 08:30–12:00 and
13:00–18:00. The system calculates 8.5 hours from the override alone, and
the original clock events remain untouched. Removing the override restores
the original events as the basis for calculation.

### Consider a few everyday mistakes

- **An employee selects OUT when arriving.** The mistake is an incorrect event,
  not simply missing information.
- **An employee clocks in twice.** Both events are preserved; evaluation sets
  aside a repeat or flags an unmatched entry.
- **An employee forgets to clock out and clocks in again tomorrow.** Yesterday
  remains incomplete, while tomorrow is evaluated independently.
- **An employee records a valid IN–OUT pair but forgets a break.** The events
  pair correctly, yet overstate the hours worked.

These cases explain the separation: recording preserves what happened at the
terminal, evaluation identifies what can be inferred, and a full-day override
declares the hours that should count—even when the original events appear
complete.

### Why entire days?

Changes to hours naturally happen in the context of a full day: an employee
reviews their hours, makes adjustments, and submits the complete result.
Any imaginable user interface would follow that flow. Making each override a
full-day replacement eliminates the need to reconcile partial changes with
recorded events.

This separation keeps the calculation logic simple while preserving the
original history. It also leaves room for future features, such as approval
workflows or measuring discrepancies between recorded and declared hours.

### Why interpret clock events only when reading?

Writing a clock event never depends on the events before it, and nothing
derived from them (sessions, days, totals) is stored; it is all calculated
when hours are read. Had sessions been stored instead, every clock-in would
first have to read the open session, decide whether to start one, close one
or refuse, and keep stored totals up to date, so reading and writing would
be entangled.

This decoupling keeps the system simpler, and makes it considerably easier
to maintain and to extend over time. The cost is that reports are
recalculated each time they are requested.

**The guiding principle: preserve what was recorded, allow what counts to
change, and never mix the two.**

## Getting started

### Locally

Requires Python 3.12 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn worktime.api.app:create_app --factory --reload
```

The package is installed in editable mode, so code changes take effect
without reinstalling. The API is then available at http://127.0.0.1:8000,
with interactive documentation at http://127.0.0.1:8000/docs. The database
is a file named `worktime.db` in the current directory.

### With Docker

```bash
docker compose up --build
```

The API listens on port 8000; set `WORKTIME_PORT` to publish it on another
port. The database is kept in the named volume `worktime-data`, so it
survives `docker compose down`. To discard it as well:

```bash
docker compose down --volumes
```

### Tests

```bash
pytest
```

See [What is tested, and why](#what-is-tested-and-why).

## Using the API

| Area | Method and path | Purpose |
|---|---|---|
| Employees | `POST /employees` | Create |
| | `GET /employees` | List; `include_inactive=true` adds deactivated ones |
| | `GET /employees/{id}` | Read |
| | `PATCH /employees/{id}` | Update |
| | `DELETE /employees/{id}` | Deactivate; recorded hours are kept |
| | `POST /employees/{id}/reactivate` | Reactivate |
| Clock | `POST /employees/{id}/clock-in` | Record a clock-in now |
| | `POST /employees/{id}/clock-out` | Record a clock-out now |
| | `GET /employees/{id}/clock-events` | List recorded events; optional `from`, `to` |
| Overrides | `PUT /employees/{id}/overrides/{date}` | Create or replace a past day's declared sessions |
| | `GET /employees/{id}/overrides/{date}` | Read a day's override |
| | `DELETE /employees/{id}/overrides/{date}` | Remove it; the clock events apply again |
| Reports | `GET /employees/{id}/summary?from=&to=` | Evaluate one employee |
| | `GET /statistics?from=&to=` | Compare all employees |
| Service | `GET /health` | Health check |

All durations are whole minutes. All timestamps are UTC in responses and
must carry a time zone offset in requests.

### Working-time CRUD

The challenge's working-time CRUD requirement is implemented through
full-day declarations. `PUT` creates a day's override or replaces an
existing one, `GET` reads it, and `DELETE` removes it. The summary endpoint
shows the resulting evaluated hours.

Deleting an override restores the recorded clock events as the basis for
evaluation. To declare that no work was done, submit an override with an
empty session list. Individual clock events cannot be edited or deleted;
they preserve the original recording history, as explained in
[the central design decision](#central-design-decision-immutable-clocks-full-day-overrides).

### A short walk-through

```bash
# Create an employee. Time zone and standard daily time have defaults.
curl -X POST localhost:8000/employees \
  -H 'content-type: application/json' \
  -d '{"name": "Ada Lovelace", "email": "ada@example.com"}'

# Clock in, and later out. The server stamps the time. This works during
# working hours only, 06:00 to 20:00 in the employee's time zone.
curl -X POST localhost:8000/employees/1/clock-in
curl -X POST localhost:8000/employees/1/clock-out

# Evaluate a week.
curl 'localhost:8000/employees/1/summary?from=2026-03-02&to=2026-03-08'

# Override a past day by stating all of its sessions.
curl -X PUT localhost:8000/employees/1/overrides/2026-03-02 \
  -H 'content-type: application/json' \
  -d '{
        "sessions": [
          {"start": "2026-03-02T08:00:00+01:00", "end": "2026-03-02T12:00:00+01:00"},
          {"start": "2026-03-02T12:30:00+01:00", "end": "2026-03-02T17:00:00+01:00"}
        ],
        "reason": "Forgot to clock out"
      }'

# Compare everyone.
curl 'localhost:8000/statistics?from=2026-03-02&to=2026-03-08'
```

Errors are returned as `{"detail": "..."}` with status 404 (not found),
409 (conflict, such as an email already in use or clocking outside working
hours) or 422 (a rule was broken, such as overlapping sessions).

## How hours are evaluated

### Recording

- **Time can only be recorded during working hours**, 06:00 to 20:00 in
  the employee's own time zone, both ends included. Outside them the clock
  is closed and answers with 409, and an override may not reach beyond them
  either.
- **Within working hours the clock accepts any sequence.** Clocking in
  twice, or out without having clocked in, is recorded like any other
  event. Whether events fit together is decided when hours are evaluated.
  Apart from the hours, only an unknown or deactivated employee is turned
  away.
- **Times are stamped by the server**, to the second. A clock event cannot
  be backdated. Every event is stored as it comes, including a double click;
  repeats are set aside when hours are evaluated.
- **Clock events are permanent.** Nothing edits or deletes them; see
  [The central design decision](#central-design-decision-immutable-clocks-full-day-overrides).

### From events to days

Each time a report is requested, an employee's events go through these
steps. Nothing derived is stored.

1. **Repeats are dropped.** The same action again within one minute is a
   double click, not a second event.
2. **Events are paired.** A clock-out pairs with the most recent clock-in of
   the same calendar day, in the employee's time zone, forming a *session*.
   An event left without a counterpart is *unpaired*. A clock-in with
   nothing after it means the employee is clocked in, until working hours
   end; after that it is a forgotten clock-out.
3. **Each day gets a status**, and only days that count get figures:

   | Status | When | Counts |
   |---|---|---|
   | `complete` | Every event of the day found its counterpart | Yes |
   | `overridden` | The day has an override, which is used instead of its events | Yes |
   | `incomplete` | An unpaired event falls on the day | No |
   | `in_progress` | The employee is clocked in | No |

4. **Overtime and shortfall** are the time worked above or below the
   employee's standard daily time.

Because working hours lie within one day, a session never crosses midnight
and every day stands on its own.

An incomplete day contributes nothing to the totals. As long as part of a
day is unknown, any figure for it would be a guess; the report lists the
day and the event that needs attention instead, and says how many days
were left out.

### Overrides

How overrides relate to clock events is described under
[The central design decision](#central-design-decision-immutable-clocks-full-day-overrides). In addition: an
override is refused if a session ends before it starts, if sessions
overlap, if a session lies outside the day's working hours, or if the day is
not over yet. An empty list of sessions means no work was done. Replacing
one day never changes its neighbours.

## Database schema

The schema is in [`schema.sql`](schema.sql): four tables, for employees,
clock events, day overrides and the sessions of an override.

The file does not have to be applied by hand; the application creates the
tables itself when it starts. It is generated from the domain's stored
classes in `src/worktime/domain/`, and a test fails if the two ever
disagree. After changing a table, regenerate it:

```bash
python -m worktime.infrastructure.schema > schema.sql
```

There are no migrations. Changing the schema of an existing database means
recreating it or adding a migration tool such as Alembic.

## Configuration

Settings are read from environment variables, named `WORKTIME_` followed by
the setting's name in capitals, and validated when the application starts.
Every one is optional and falls back to its default. An invalid value stops
the application from starting, with a message naming it.

| Variable | Default | Meaning |
|---|---|---|
| `WORKTIME_DATABASE_URL` | `sqlite:///worktime.db` | Where the database file is |
| `WORKTIME_DATABASE_BUSY_TIMEOUT_SECONDS` | `5.0` | How long a writer waits for the lock |
| `WORKTIME_DEFAULT_TIME_ZONE` | `Europe/Vienna` | Time zone of new employees |
| `WORKTIME_DEFAULT_STANDARD_DAILY_MINUTES` | `480` | Standard daily time of new employees |
| `WORKTIME_WORKING_HOURS_START` | `06:00` | Start of working hours, in each employee's local time |
| `WORKTIME_WORKING_HOURS_END` | `20:00` | End of working hours; must be later on the same day |
| `WORKTIME_REPEAT_WINDOW_MINUTES` | `1` | Window in which a repeated clock action is ignored |

The settings are defined in [`src/worktime/config.py`](src/worktime/config.py)
and read once, at start-up. The Docker image sets `WORKTIME_DATABASE_URL`
to a file in its volume.

## Design

### Layers

The code follows a domain-driven layout: the business concepts and rules
live in `domain/`, the use cases in `application/`, and the HTTP layer in
`api/`, with dependencies pointing inwards.

```
src/worktime/
  config.py            reads and validates the settings from the environment
  domain/              business concepts and rules
    base.py              what makes domain classes storable
    employee.py          the employee and its validation
    clocking.py          clock events, working hours, pairing events into sessions
    workday.py           overrides, and the evaluation of days
    reporting.py         totals, the report of one employee, statistics
    errors.py            not found, conflict, rule violation
  application/
    services.py        the use cases; each public method is one transaction
  infrastructure/
    database.py        SQLite set-up and transactions
    schema.py          renders schema.sql
  api/
    app.py             assembles the application and maps errors to status codes
    routes.py          the endpoints
    schemas.py         request and response shapes
```

The stored domain classes (`Employee`, `ClockEvent`, `DayOverride`) are
SQLAlchemy models: each is defined once and serves both as the domain
object and as the table. Services load and change them through the
database session, which saves the changes when a writing transaction
commits; there are no separate repositories or table classes to translate
between.

The rules that took the most thought, pairing events and evaluating days,
are plain functions that take values and return values. They need no
database and are tested without one.

### Decisions and their trade-offs

| Decision | Why | Trade-off |
|---|---|---|
| Store clock events, not sessions | Recording needs no knowledge of earlier events, so nothing is refused on their account and there is nothing to race on | Sessions are rebuilt on every read |
| Calculate everything on read | Results cannot go stale, and an override needs no clean-up | More work per report; an override changes reports of past periods |
| The clock does not judge the sequence of events | Nobody is blocked at the door by a mistake made earlier | Correctness depends on someone following up on incomplete days |
| Recording only during working hours | Every session lies within one day: no splitting at midnight, no separate handling of night time, and each day can be evaluated and overridden on its own | Work outside those hours cannot be recorded at all, by the clock or by an override; someone still at work at closing time cannot clock out and needs an override for the day |
| Only complete or overridden days count | Every total is backed by a fully known day | One stray event removes a day from the totals until the day is overridden |
| Whole-day overrides | Clock data is never touched; a day cannot be half overridden; overlap is a real, checkable rule | The whole day is sent even to fix one time; earlier overrides are not kept |
| Overtime by daily duration | One number per employee is enough | No weekly rule and no tolerance for a few minutes |
| Deactivate instead of delete | Recorded hours and past statistics stay intact | No true erasure of a person |
| SQLite | No server to run; trivial to test; one writer keeps concurrency simple | Single host; writes queue behind each other |
| Create tables at start-up | Nothing extra for four tables | No schema versioning; a model change needs a migration tool |
| Domain classes are also the tables | One definition per concept, no repositories and no mapping code | The domain depends on SQLAlchemy, and a changed object is saved without an explicit call |

"No overlapping sessions" appears twice. Sessions built from clock events
cannot overlap, because they come from one ordered sequence of events.
Sessions entered by hand could, so an override is checked and refused.

### Concurrency

SQLite allows one writer at a time. Every writing transaction asks for the
write lock as it begins (`BEGIN IMMEDIATE`), before its first statement. A
check made inside the transaction, such as "is this email taken?", therefore
cannot be invalidated by another request before the transaction commits.
One request is one transaction; a check never lives in a different
transaction than the write it guards.

Read-only requests do not ask for the lock. Write-ahead logging lets them
proceed while a write is in progress, and a busy timeout makes a waiting
writer queue briefly instead of failing. Foreign keys are switched on for
every connection, because SQLite ignores them otherwise.

The tests cover this directly: a second writer is kept out while a write
transaction is open, a reader is not, and eight simultaneous clock-ins are
all accepted and count as one.

## What is tested, and why

The tests are a selection, not blanket coverage. Each rule is tested once,
at the level where it lives, and the selection follows the decisions that
are particular to this project.

| Tests | What they establish |
|---|---|
| `tests/domain/test_clocking.py` | How any sequence of clock events is read. One table of cases: doubled, missing and stray events, events of different days, repeats. This is the price of a clock that accepts any sequence, so it is tested most closely. Also when a last clock-in stops counting as "clocked in", and that "the same day" follows the employee's time zone. |
| `tests/domain/test_workday.py` | A table of days with their figures (overtime, shortfall, breaks). Also that an incomplete day has no figures, and that an override replaces a day without touching its neighbours. |
| `tests/domain/test_override.py` | When an override is refused: overlapping sessions, a session that ends before it starts or lies outside working hours, a day that is not over. |
| `tests/infrastructure/test_database.py` | That a writer holds the lock before its first statement, that readers are not blocked, and that a failed write leaves nothing behind. Check-then-write logic depends on these. |
| `tests/application/test_clock_concurrency.py` | Eight simultaneous clock-ins are all accepted, and count as a single clock-in when hours are read. |
| `tests/api/test_end_to_end.py` | One story per feature through the HTTP API: an employee's lifecycle, the clock being open during working hours only and accepting any sequence within them, working hours following each employee's time zone, a forgotten clock-out put right by an override, a week's summary, and statistics across employees. They show that the layers are connected. |
| `tests/infrastructure/test_schema.py` | That `schema.sql` matches the table definitions. |
| `tests/test_config.py` | That a setting can be changed through the environment, and that impossible working hours or an unknown time zone stop the application from starting. |

Left untested on purpose: behaviour that comes from the frameworks, such as
request parsing and routing, and simple arithmetic such as adding up totals,
which the end-to-end stories exercise anyway.

## Limitations and possible extensions

- **No users, roles or approval.** Anyone who can reach the API can do
  everything, and an override does not record who made it.
- **No period closing.** An override can change the report of a period
  that was already looked at.
- **Daytime work only.** Working hours are the same for everyone and lie
  within one day. Night work, and any work before or after those hours,
  cannot be recorded. Work outside a company's usual hours is still working
  time, so a real system would need a way to capture it.
- **No working-day calendar.** The system knows nothing about weekends,
  holidays or absences. A day without any record does not appear, and a
  short day at the weekend shows a shortfall.
- **Daily overtime only**, with no weekly rule, no tolerance, and no
  automatic deduction of breaks: an unclocked break counts as work.
- **Settings apply retroactively.** Changing an employee's standard daily
  time or time zone re-evaluates their past days too.
- **Statistics evaluate employees one by one.** That is fine for a small
  workforce and would need batching for thousands.
- **Changing the working hours does not rewrite the past.** Events recorded
  under wider hours stay as they are and keep counting.
- **Busy database and extreme dates are not handled specially.** A write
  that waits longer than the busy timeout for another write, or a report
  period reaching the year 9999, ends in a server error rather than a
  tailored response. Neither happens in normal use.

## AI assistance

LLMs were used as both thinking partners and coding partners during the implementation of this repository.
