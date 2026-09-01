"""One-off maintenance scripts (migrations, backfills, re-crops).

A package rather than loose files so the test suite can import them --
their safety properties (never resurrect a settled crop, never make a
match worse) are worth pinning down, and `python -m scripts.name` works
exactly the same either way.
"""
