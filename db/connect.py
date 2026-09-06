"""Open SQLite connections in genuine read-only mode (driver-enforced).

Every consumer that must only ever *read* the project database -- most
immediately the ``execute_queries`` graph node (``graph/nodes.py``) and any
verification/analysis script -- needs a connection on which a write fails at
the driver level regardless of what SQL is executed against it. A naming
convention ("only run SELECTs here") is not an enforcement layer; this module
is the enforcement layer.

It lives in its own small module rather than inside ``db/load.py`` because
``db.load`` is the *write* layer (its module docstring scopes it to inserting
transaction rows), while this module is connection plumbing for the *read*
side. Each ``db`` submodule keeps one responsibility (load / transform /
timezones / guardrails), so a connection helper gets a module of its own.

Mechanism
---------
Read-only access is requested at *open* time, before any SQL runs, with
SQLite's URI filename facility::

    uri = f"{Path(db_path).resolve().as_uri()}?mode=ro"
    return sqlite3.connect(uri, uri=True)

The ``mode=ro`` query parameter makes SQLite open the database file itself
without write permission, so any statement that would mutate the database
(INSERT / UPDATE / DELETE / CREATE / DROP / ...) is rejected by the driver
with ``sqlite3.OperationalError`` -- "attempt to write a readonly database" --
before a single byte of the file can change. Because the file handle is
read-only, two further properties follow:

* Connecting to a **missing** path raises ``sqlite3.OperationalError``
  ("unable to open database file"); an empty database is never created behind
  the caller's back (contrast with the ``mode=rwc`` URI parameter).
* The connection itself creates no rollback-journal / WAL sidecar files
  (verified by ``scripts/verify_readonly.py``).

Why this spelling (verified, not assumed)
-----------------------------------------
The URI spelling was checked against the exact interpreters this repository
actually uses before being committed to: CPython 3.12.14 with the bundled
SQLite 3.53.1 (the project's ``.venv``) and CPython 3.14.5 with SQLite 3.50.4.
On both, ``file:///C:/.../sentrasql.db?mode=ro`` -- as produced by
``Path.as_uri()``, which also percent-encodes characters such as the spaces in
this project's checkout path -- connects, serves SELECTs, and rejects writes
with the OperationalError above. Two plausible weaker alternatives were
deliberately not chosen:

* ``PRAGMA query_only = ON`` after a normal connect also makes the driver
  reject writes, but the file was already opened read-write; enforcement then
  rides on a per-connection pragma instead of the OS-level open mode.
  ``mode=ro`` is strictly stronger and is the documented, idiomatic approach
  in CPython's ``sqlite3`` module (see "SQLite URI filenames" in the standard
  library documentation).
* ``immutable=1`` in the URI is for files the caller asserts will *never*
  change, from any process; SQLite skips locking on that promise, which is the
  wrong assumption for a live data file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Open ``db_path`` in genuine read-only mode and return the connection.

    ``db_path`` is the path to an existing SQLite database file. The returned
    connection is opened via SQLite's URI facility with ``mode=ro``, so the
    file is opened without write permission and every write attempt fails at
    the driver level with ``sqlite3.OperationalError`` ("attempt to write a
    readonly database") no matter what SQL is executed; SELECTs and other pure
    reads work normally. The caller owns the connection lifecycle
    (``conn.close()``, or ``with connect_readonly(path) as conn:``). Passing a
    path that does not exist raises ``sqlite3.OperationalError`` rather than
    creating a new, empty database file.
    """
    uri = f"{Path(db_path).resolve().as_uri()}?mode=ro"
    return sqlite3.connect(uri, uri=True)
