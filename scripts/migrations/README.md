# Applied migrations

Every migration in this directory has already been run against production
data. They're kept because each one documents the shape of a schema change
that `Base.metadata.create_all` can't express -- it only ever *creates*
tables, never alters existing ones (see `DATABASE.md`).

They live here rather than in `scripts/` so that the handful of scripts
that are actually run day to day -- `run-*.sh` and `start-*.sh` -- aren't
buried among sixteen one-shots.

Re-running one is not expected to be necessary and is not guaranteed safe.
Read the script before you do.
