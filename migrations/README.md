# Alembic migrations

This directory is the authoritative schema history from the 1 October 2026
baseline onward. The older scripts under `scripts/migrations/` are retained as
historical evidence of changes made before the database recorded a revision.

The baseline is a complete static rendering of the ORM schema, so a blank database
can be built with `alembic upgrade head`. Existing production databases must be
verified against that baseline and then stamped; do not run the baseline's table
creation against an existing database.

Always load the same `AUTOMATION_DATABASE_URL` used by the service, back up first,
and run `alembic current` plus `alembic upgrade head` as a release step.
