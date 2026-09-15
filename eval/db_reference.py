"""Read-only reference-query helpers for live eval cases.

Individual eval cases verify the graph's results against the real database by
running hand-written reference SQL on a connection that is fully independent
of the graph's own compiled queries. This module owns that plumbing: opening a
fresh read-only connection (via ``db.connect.connect_readonly`` -- the
project's genuine driver-level read-only layer, never duplicated here) and
executing an arbitrary SQL statement on it.

The one helper exposed today is ``run_reference_query``:

* It opens a **fresh** connection on every call and closes it before
  returning, so a case can never leak a connection and every call is
  independent of whatever the graph did (no shared cursor, no shared
  transaction, no row factory). This separation is the whole point of a
  reference query: it must be able to catch a bug in the graph's own
  connection handling.
* The connection is opened with SQLite's ``mode=ro`` URI parameter (see
  ``db.connect``), so the helper is physically unable to mutate the database:
  any write statement raises ``sqlite3.OperationalError`` ("attempt to write a
  readonly database") at the driver level before a single byte can change.
* Results come back as a plain ``list[tuple]`` -- the default ``sqlite3`` row
  shape, no ``sqlite3.Row`` objects and no column-name mapping -- because a
  reference query only needs positional values to compare against the graph's
  stated expectations.
* The database path defaults to the project's live database
  (``data/processed/sentrasql.db``, derived here exactly as
  ``graph/node_shared.py`` and the ``scripts/`` modules derive it), but a case
  can pass an explicit ``db_path`` when it needs to target another file.

This module performs no validation of the SQL and applies no query logic of
its own: it is deliberately a dumb, reliable pipe from a SQL string to its
result rows.
"""

from __future__ import annotations

from pathlib import Path

from db.connect import connect_readonly

# Path to the project's live read-only database, shared by the eval preflight
# (``eval.runner``) and as the default target for ``run_reference_query``.
# Derivation mirrors ``graph/node_shared.py``: project root / data / processed
# / sentrasql.db.
_MAIN_DB_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "processed"
    / "sentrasql.db"
)


def run_reference_query(
    sql: str, db_path: str | Path | None = None
) -> list[tuple]:
    """Execute ``sql`` on a fresh read-only connection and return its rows.

    Opens a new read-only connection to ``db_path`` (defaulting to the
    project's live database, ``data/processed/sentrasql.db``), executes ``sql``
    with the connection's default row factory (plain tuples), and returns every
    fetched row as ``list[tuple]``. The connection is always closed before this
    function returns, even when ``sql`` raises, so every call is self-contained
    and no connection is ever leaked back to a caller.

    Because the connection is opened through ``db.connect.connect_readonly``
    (SQLite URI ``mode=ro``), a write statement in ``sql`` is rejected at the
    driver level with ``sqlite3.OperationalError`` -- "attempt to write a
    readonly database" -- and the database file cannot be modified through this
    helper. Passing a ``db_path`` that does not exist raises
    ``sqlite3.OperationalError`` rather than creating an empty database file.

    Args:
        sql: The SQL statement to execute (SELECTs for real reference checks).
        db_path: Optional explicit database file to query. Defaults to the
            project's live ``data/processed/sentrasql.db``.

    Returns:
        The rows returned by ``sql`` as a ``list`` of plain ``tuple`` values,
        in row order. A SELECT that matches nothing returns ``[]``.
    """
    conn = connect_readonly(_MAIN_DB_PATH if db_path is None else db_path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()
