"""Load schema-ready transaction rows into the SQLite ``transactions`` table.

This module is the persistence-layer counterpart to ``db/transform.py``: a
DataFrame in the exact schema-ready shape produced by ``transform_raw_data``
(its ten columns, listed below in ``TRANSACTION_COLUMNS``) is inserted into the
``transactions`` table of a SQLite database. ``db/__init__.py`` makes it
importable as ``db.load``.

Guarantees implemented here:

* **Parameterized SQL only.** Every value is bound through ``?`` placeholders;
  no value is ever interpolated into a SQL string.
* **Single transaction / all-or-nothing.** All rows are inserted inside one
  transaction; a failure at any point rolls back every row already inserted.
* **Errors propagate.** Insert errors are not caught or swallowed here -- e.g.
  a ``sqlite3.IntegrityError`` (CHECK / NOT NULL violation) bubbles up to the
  caller after the transaction has been rolled back.
* **Native Python parameter values.** pandas stores cells as ``numpy`` scalars;
  CPython's ``sqlite3`` binds some of them (``numpy.bool_``, ``numpy.int64``)
  as BLOBs instead of integers, so every column is normalized to plain Python
  ``bool``/``int``/``float``/``str`` values and missing markers
  (``NaN`` / ``NaT`` / ``pd.NA``) become SQL ``NULL``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

# Exact schema-ready column order required by the transactions table
# (mirrors db/transform.py's output order and db/schema.sql's DDL).
TRANSACTION_COLUMNS = (
    "invoice_id",
    "is_cancelled_invoice",
    "stock_code",
    "description",
    "line_item_type",
    "quantity",
    "unit_price",
    "customer_id",
    "country",
    "invoice_timestamp",
)

_PLACEHOLDERS = ", ".join("?" for _ in TRANSACTION_COLUMNS)
_INSERT_SQL = (
    "INSERT INTO transactions "
    f"({', '.join(TRANSACTION_COLUMNS)}) VALUES ({_PLACEHOLDERS})"
)


def _parameter_rows(df: pd.DataFrame) -> zip:
    """Return an iterator of native-Python parameter tuples for ``df``.

    Columns are re-selected in ``TRANSACTION_COLUMNS`` order regardless of the
    input column order. Missing values are replaced with ``None`` and each
    column is materialized with ``Series.tolist()``, which converts pandas'
    numpy scalars to plain Python ``bool``/``int``/``float``/``str`` values.
    """
    frame = df.loc[:, TRANSACTION_COLUMNS].where(
        pd.notnull(df.loc[:, TRANSACTION_COLUMNS]), None
    )
    return zip(*(frame[column].tolist() for column in TRANSACTION_COLUMNS))


def load_transactions(db_path: str | Path, df: pd.DataFrame) -> None:
    """Insert every row of ``df`` into the ``transactions`` table.

    ``db_path`` is the path to the SQLite database file (created if missing).
    ``df`` must be in the exact schema-ready shape produced by
    ``db.transform.transform_raw_data``. All rows are inserted inside a single
    transaction (all-or-nothing) via one parameterized ``executemany``;
    insert errors are not caught and propagate to the caller after rollback.
    """
    missing_columns = [c for c in TRANSACTION_COLUMNS if c not in df.columns]
    if missing_columns:
        raise ValueError(
            "DataFrame is missing required transactions column(s): "
            f"{', '.join(missing_columns)}. Expected columns: "
            f"{', '.join(TRANSACTION_COLUMNS)}."
        )

    conn = sqlite3.connect(str(db_path))
    try:
        # ``with conn`` commits on success and rolls back on any exception,
        # so either every row is persisted or none are.
        with conn:
            conn.executemany(_INSERT_SQL, _parameter_rows(df))
    finally:
        conn.close()
