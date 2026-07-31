"""SQLite persistence. See schema.sql for the data model and db.Database
for the only supported way to touch it."""

from vhfwatch.store.db import Database

__all__ = ["Database"]
