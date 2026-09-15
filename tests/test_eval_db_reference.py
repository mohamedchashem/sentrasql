"""Tests for ``eval.db_reference.run_reference_query`` (live eval plumbing).

``run_reference_query`` is the helper eval cases will use to verify the
graph's results against the real database on a fresh read-only connection that
is fully independent of the graph's own compiled queries. Its contracts:

1. A SELECT returns the fetched rows as a plain ``list`` of plain ``tuple``
   values (default ``sqlite3`` row shape, no ``sqlite3.Row`` mapping) -- a
   trivial ``SELECT 1`` returns ``[(1,)]``.
2. The default database path (the project's live
   ``data/processed/sentrasql.db``) and an explicit identical ``db_path``
   argument produce the same result, and a real table query returns real,
   typed rows (an integer count from ``SELECT COUNT(*)``).
3. The connection really is the project's driver-level read-only layer: a
   valid write statement is rejected with ``sqlite3.OperationalError`` and the
   database row count is unchanged before/after the attempt. The write used is
   the exact statement ``scripts/verify_readonly.py`` already proves is
   rejected on a ``mode=ro`` connection, so this test never executes a write
   against the database.

Like the live-database tests in ``tests/test_execute_queries.py``, the whole
class runs only when the real database file is present.
"""

import sqlite3
import unittest
from pathlib import Path

from eval.db_reference import _MAIN_DB_PATH, run_reference_query

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DB_PATH = _PROJECT_ROOT / "data" / "processed" / "sentrasql.db"

# The exact valid INSERT scripts/verify_readonly.py uses to prove a mode=ro
# connection rejects writes at the driver level. If this statement ever
# "succeeded", the read-only layer would be broken -- the assertion below is
# precisely what fails.
_INSERT_SQL = (
    "INSERT INTO transactions (invoice_id, is_cancelled_invoice, stock_code,"
    " line_item_type, quantity, unit_price, country, invoice_timestamp)"
    " VALUES ('RO_TEST', 0, 'TEST', 'product', 1, 1.0, 'Testland',"
    " '2026-09-06 12:00:00')"
)


@unittest.skipUnless(
    _DB_PATH.exists(), f"real database not present at {_DB_PATH}"
)
class RunReferenceQueryLiveDbTest(unittest.TestCase):
    """``run_reference_query`` against the live read-only database."""

    def test_select_returns_list_of_plain_tuples(self):
        result = run_reference_query("SELECT 1")
        self.assertIsInstance(result, list)
        self.assertEqual(result, [(1,)])
        for row in result:
            self.assertIsInstance(row, tuple)

    def test_default_path_matches_explicit_path_and_returns_typed_count(self):
        by_default = run_reference_query("SELECT COUNT(*) FROM transactions")
        by_explicit = run_reference_query(
            "SELECT COUNT(*) FROM transactions", _DB_PATH
        )
        self.assertEqual(by_default, by_explicit)
        self.assertEqual(len(by_default), 1)
        self.assertIsInstance(by_default[0][0], int)

    def test_write_statement_is_rejected_and_nothing_persists(self):
        before = run_reference_query("SELECT COUNT(*) FROM transactions")[0][0]
        with self.assertRaises(sqlite3.OperationalError):
            run_reference_query(_INSERT_SQL)
        after = run_reference_query("SELECT COUNT(*) FROM transactions")[0][0]
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
