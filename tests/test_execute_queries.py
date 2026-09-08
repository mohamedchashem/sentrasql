"""Tests for graph.node_execute_queries.execute_queries (Node 6).

Implemented contracts so far:

1. Error/guardrail passthrough. When ``state.error`` is already set -- the
   graph is already routing toward its error path -- or when the plan has not
   passed guardrail validation (``state.guardrail_status != "passed"``), the
   node returns the state completely untouched: no read-only connection is
   opened and no SQL is executed, even if ``sql_main`` would fail hard.

2. Successful main-query execution populates ``state.main_results`` with the
   raw rows as a list of column-name-keyed dicts. Main queries may
   legitimately return many rows (e.g. a grouped breakdown), so there is no
   "exactly one row" rule -- the live test runs a country-grouped aggregate
   and confirms every group comes back with the projected keys and values.

3. Zero rows is a valid, non-failure outcome. A main query matching no rows
   stores an empty list (distinct from ``None`` = "not executed yet") and sets
   no error.

4. A database-level failure never crashes the graph: any ``sqlite3.Error``
   raised while connecting or executing is translated into the exact reason
   string ``"query_execution_failed:main"`` on ``state.error``, with no
   exception propagating out of the node and no ``main_results`` written.

5. Every companion runs after the main query succeeds, and write-back is
   atomic: the main query's rows and every companion's validated count are
   collected in local variables and written to ``state.main_results`` /
   ``CompanionQuery.excluded_count`` (with ``status`` flipped to ``"success"``)
   only when the main query and every companion have all succeeded.

6. A companion whose count is genuinely 0 is a normal, valid result, and
   companions still run even when the main query returned zero rows -- each
   exclusion count is independently meaningful and disclosures need it.

7. A companion failure partway through a multi-companion set is a hard stop:
   the exact rule-suffixed reason is set on ``state.error``, remaining
   companions never execute (verified by recording every executed statement),
   and -- via the object-identity rigor established in validate_guardrails's
   tests -- no ``main_results`` and no companion's ``excluded_count`` or
   ``status`` were written, including for companions that had already
   succeeded.

8. Grouped main queries carry a top-level aggregate (DESIGN_LOG.md section
   20): when the executed main SQL is a grouped SELECT and returned at least
   one row, ``state.main_results`` becomes the wrapper ``{"rows": [...],
   "total": {...}}`` where ``rows`` is the row-level breakdown and ``total``
   is the ungrouped aggregate record produced by a second lightweight query
   derived from the main SQL. A grouped query that matched no rows still
   stores the empty list. Failures of the derived total query set the
   dedicated reasons ``"query_execution_failed:total"`` /
   ``"query_result_shape_invalid:total"`` and never write partial results.
"""

import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from db.connect import connect_readonly
from graph import node_execute_queries as nodes_module
from graph.node_execute_queries import execute_queries
from graph.state import CompanionQuery, GraphState, RuleName

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DB_PATH = _PROJECT_ROOT / "data" / "processed" / "sentrasql.db"

_QUERY_EXECUTION_FAILED_MAIN = "query_execution_failed:main"

_MAIN_SUM_REVENUE_SQL = (
    "SELECT SUM(quantity * unit_price) AS revenue FROM transactions"
)


def _ready_state(sql_main: str) -> GraphState:
    """Build a state that has cleared every gate: no error, guardrail passed."""
    return GraphState(
        raw_query="test query",
        sql_main=sql_main,
        sql_companions={},
        guardrail_status="passed",
        error=None,
    )


def _reference_rows(sql: str) -> list[tuple]:
    """Run ``sql`` over a fresh read-only connection and return the raw rows."""
    conn = connect_readonly(_DB_PATH)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


class ExecuteQueriesGatePassthroughTest(unittest.TestCase):
    """Node 6 gating: errored / not-passed states are returned unchanged."""

    def test_errored_state_is_returned_completely_unchanged(self):
        # The realistic errored arrival: an upstream compile_sql gate failure
        # left state.error set while guardrail_status stayed "pending" (the
        # guardrail node passes errored states through untouched). The broken
        # sql_main proves no execution is attempted on this path.
        state = GraphState(
            raw_query="test query",
            query_intent=None,
            sql_main=(
                "SELECT SUM(quantity * unit_price) AS revenue FROM missing_table"
            ),
            sql_companions={},
            guardrail_status="pending",
            error="invalid_intent:aggregation_metric_mismatch",
        )
        stored_dump = state.model_dump()

        result = execute_queries(state)

        # Completely untouched: same error, no main_results, no field mutated.
        self.assertEqual(result.model_dump(), stored_dump)
        self.assertEqual(result.error, "invalid_intent:aggregation_metric_mismatch")
        self.assertIsNone(result.main_results)

    def test_guardrail_not_passed_state_is_returned_completely_unchanged(self):
        # A state whose plan never passed the guardrail stage (status still
        # "pending", no error) must not be executed either -- the sql_main that
        # would fail at the database level proves no connection is opened.
        state = GraphState(
            raw_query="test query",
            query_intent=None,
            sql_main=(
                "SELECT SUM(quantity * unit_price) AS revenue FROM missing_table"
            ),
            sql_companions={},
            guardrail_status="pending",
            error=None,
        )
        stored_dump = state.model_dump()

        result = execute_queries(state)

        self.assertEqual(result.model_dump(), stored_dump)
        self.assertIsNone(result.error)
        self.assertIsNone(result.main_results)


@unittest.skipUnless(
    _DB_PATH.exists(), f"real database not present at {_DB_PATH}"
)
class ExecuteQueriesMainQueryLiveDbTest(unittest.TestCase):
    """Node 6 main-query execution against the live read-only database."""

    def test_successful_multi_row_main_query_populates_main_results(self):
        # A grouped breakdown legitimately returns many rows: one per distinct
        # country in the live database (43, per the loaded dataset). Main
        # queries get no "exactly one row" rule, unlike companions. Per
        # DESIGN_LOG.md section 20 the grouped result is the wrapper
        # {"rows": [...], "total": {...}} once at least one row exists.
        sql = (
            "SELECT country, SUM(quantity * unit_price) AS revenue "
            "FROM transactions GROUP BY country"
        )
        state = _ready_state(sql)

        result = execute_queries(state)

        # No error, and main_results is the grouped wrapper dict -- not None.
        self.assertIsNone(result.error)
        self.assertIsNotNone(result.main_results)
        self.assertIsInstance(result.main_results, dict)
        self.assertEqual(set(result.main_results.keys()), {"rows", "total"})

        rows = result.main_results["rows"]
        reference_rows = _reference_rows(sql)
        self.assertGreater(len(reference_rows), 1)
        self.assertEqual(len(rows), len(reference_rows))

        # Every returned record is a column-name-keyed dict carrying exactly the
        # projected columns, and its values match the independently-run
        # reference query row for row.
        by_country = {record["country"]: record for record in rows}
        for country, revenue in reference_rows:
            self.assertIn(country, by_country)
            self.assertEqual(by_country[country]["revenue"], revenue)
            self.assertEqual(
                set(by_country[country].keys()), {"country", "revenue"}
            )

        # The top-level aggregate record mirrors the scalar query for the same
        # intent (same WHERE, no GROUP BY), independently recomputed here.
        total = result.main_results["total"]
        self.assertEqual(set(total.keys()), {"revenue"})
        expected_total = _reference_rows(
            "SELECT SUM(quantity * unit_price) AS revenue FROM transactions"
        )[0][0]
        self.assertEqual(total["revenue"], expected_total)

    def test_grouped_total_ignores_row_limit_clause_and_preserves_where(self):
        # The total must be the aggregate over the whole filtered population,
        # never the truncated LIMIT window of the grouped breakdown. Here the
        # grouped SQL is written exactly as validate_guardrails would leave it
        # for a row-limited query (LIMIT + OFFSET present): the derived total
        # drops LIMIT/OFFSET but must keep every WHERE condition verbatim.
        sql = (
            "SELECT country, SUM(quantity * unit_price) AS revenue "
            "FROM transactions "
            "WHERE country IN ('United Kingdom', 'Germany') "
            "GROUP BY country LIMIT 1 OFFSET 1"
        )
        state = _ready_state(sql)

        result = execute_queries(state)

        self.assertIsNone(result.error)
        self.assertEqual(len(result.main_results["rows"]), 1)
        # Full filtered total, unaffected by LIMIT/OFFSET on the breakdown.
        expected_total = _reference_rows(
            "SELECT SUM(quantity * unit_price) AS revenue FROM transactions "
            "WHERE country IN ('United Kingdom', 'Germany')"
        )[0][0]
        self.assertEqual(
            result.main_results["total"]["revenue"], expected_total
        )

    def test_zero_rows_is_a_valid_non_error_outcome(self):
        # A filter that matches nothing still executes successfully. Zero rows
        # is explicitly NOT an error: main_results becomes the empty list
        # (distinct from None = "not executed yet") and no error is set.
        # A grouped query with no rows keeps the empty list -- with no
        # breakdown there is no wrapper and no total (a SUM over no rows is
        # NULL, not a trustworthy aggregate).
        sql = (
            "SELECT country, SUM(quantity * unit_price) AS revenue "
            "FROM transactions WHERE country = 'Atlantis' GROUP BY country"
        )
        state = _ready_state(sql)

        result = execute_queries(state)

        self.assertIsNone(result.error)
        self.assertEqual(_reference_rows(sql), [])
        self.assertEqual(result.main_results, [])

    def test_database_level_failure_sets_exact_reason_and_does_not_raise(self):
        # First prove this SQL genuinely fails at the database level, so the
        # test is exercising the real exception path and not a silent no-op.
        sql = (
            "SELECT SUM(quantity * unit_price) AS revenue FROM missing_table"
        )
        with self.assertRaises(sqlite3.OperationalError):
            _reference_rows(sql)
        state = _ready_state(sql)

        # Must not raise: the node translates the sqlite3.OperationalError into
        # the exact machine-readable reason on state.error instead of letting
        # it crash the graph.
        result = execute_queries(state)

        self.assertEqual(result.error, _QUERY_EXECUTION_FAILED_MAIN)
        self.assertIsNone(result.main_results)


@unittest.skipUnless(
    _DB_PATH.exists(), f"real database not present at {_DB_PATH}"
)
class ExecuteQueriesGroupedTotalFailureTest(unittest.TestCase):
    """Node 6 grouped-total failure paths never crash and never write.

    The derived total query (DESIGN_LOG.md section 20) is guaranteed to
    succeed on a compile_sql-built, guardrail-passed statement, so these paths
    are exercised by mocking ``_fetch_query``: the main query's fetch returns
    grouped rows and the total query's fetch fails or returns a non-scalar
    shape. Each failure must set its dedicated ``:total`` reason -- never the
    main-query code -- and write nothing back.
    """

    _GROUPED_SQL = (
        "SELECT country, SUM(quantity * unit_price) AS revenue "
        "FROM transactions GROUP BY country"
    )
    _GROUPED_ROWS = (["country", "revenue"], [("United Kingdom", 1.0)])
    _TOTAL_COLUMNS = ["revenue"]
    _TOTAL_ROW = (123.0,)

    def _run_with_fetch_side_effect(self, side_effects):
        state = _ready_state(self._GROUPED_SQL)
        with mock.patch.object(
            nodes_module, "_fetch_query", side_effect=side_effects
        ):
            return execute_queries(state)

    def test_total_database_failure_sets_total_reason_and_writes_nothing(self):
        result = self._run_with_fetch_side_effect(
            [
                self._GROUPED_ROWS,
                sqlite3.OperationalError("boom"),
            ]
        )

        # The main query already succeeded, so the main-query reason code must
        # not be (mis)used; the dedicated total reason is set instead.
        self.assertEqual(result.error, "query_execution_failed:total")
        self.assertIsNone(result.main_results)

    def test_total_shape_invalid_sets_total_reason_and_writes_nothing(self):
        # A derived total returning two rows fails the single-row shape rule.
        result = self._run_with_fetch_side_effect(
            [
                self._GROUPED_ROWS,
                (["revenue"], [self._TOTAL_ROW, (456.0,)]),
            ]
        )

        self.assertEqual(result.error, "query_result_shape_invalid:total")
        self.assertIsNone(result.main_results)


class _RecordingConnection:
    """Delegate to a real read-only connection while logging every execute.

    ``sqlite3.Connection`` rejects attribute assignment, so a small delegating
    wrapper returned in place of a real connection is how these tests record
    the exact statements ``execute_queries`` ran (and therefore prove which
    companions were never executed).
    """

    def __init__(self, real: sqlite3.Connection, log: list[str]) -> None:
        self._real = real
        self._log = log

    def execute(self, sql, *args, **kwargs):
        self._log.append(sql)
        return self._real.execute(sql, *args, **kwargs)

    def close(self) -> None:
        self._real.close()


def _state_with_companions(
    sql_main: str,
    companions: dict[RuleName, CompanionQuery],
) -> GraphState:
    """Build a gate-cleared state carrying ``sql_main`` and ``companions``."""
    return GraphState(
        raw_query="test query",
        sql_main=sql_main,
        sql_companions=companions,
        guardrail_status="passed",
        error=None,
    )


def _count_companion(rule: RuleName, where: str) -> CompanionQuery:
    """A pending companion counting transactions matching ``where``."""
    return CompanionQuery(
        rule=rule,
        sql=(
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            f"WHERE {where}"
        ),
    )


@unittest.skipUnless(
    _DB_PATH.exists(), f"real database not present at {_DB_PATH}"
)
class ExecuteQueriesCompanionLiveDbTest(unittest.TestCase):
    """Node 6 companion execution against the live read-only database."""

    def test_all_companions_succeed_and_write_back_atomically(self):
        customer_null = _count_companion(
            RuleName.CUSTOMER_EXCLUDE_NULL, "customer_id IS NULL"
        )
        zero_price = _count_companion(
            RuleName.AVG_EXCLUDE_ZERO_PRICE, "unit_price = 0"
        )
        companions = {
            customer_null.rule: customer_null,
            zero_price.rule: zero_price,
        }
        state = _state_with_companions(_MAIN_SUM_REVENUE_SQL, companions)

        # Capture the exact objects the node receives before it runs: the
        # identity asserts below prove the write-back mutated those same
        # objects rather than replacing the mapping.
        stored = state.sql_companions
        stored_customer = state.sql_companions[RuleName.CUSTOMER_EXCLUDE_NULL]
        stored_zero_price = state.sql_companions[RuleName.AVG_EXCLUDE_ZERO_PRICE]
        self.assertEqual(stored_customer.status, "pending")
        self.assertIsNone(stored_customer.excluded_count)

        result = execute_queries(state)

        self.assertIsNone(result.error)
        # The main query's result is written back too.
        expected_revenue = _reference_rows(_MAIN_SUM_REVENUE_SQL)[0][0]
        self.assertEqual(result.main_results, [{"revenue": expected_revenue}])
        # Every companion's validated count matches an independently-run
        # reference query, and its lifecycle status flipped to success.
        for rule in companions:
            self.assertEqual(
                result.sql_companions[rule].excluded_count,
                _reference_rows(companions[rule].sql)[0][0],
            )
            self.assertEqual(result.sql_companions[rule].status, "success")
        # Atomic write-back mutated the very objects the state carried, leaving
        # the mapping and each companion's SQL untouched.
        self.assertIs(result.sql_companions, stored)
        self.assertIs(
            result.sql_companions[RuleName.CUSTOMER_EXCLUDE_NULL], stored_customer
        )
        self.assertIs(
            result.sql_companions[RuleName.AVG_EXCLUDE_ZERO_PRICE], stored_zero_price
        )
        self.assertEqual(stored_customer.sql, customer_null.sql)
        self.assertEqual(stored_zero_price.sql, zero_price.sql)


    def test_zero_count_companion_is_valid_and_runs_after_zero_row_main(self):
        # A zero-row main query is a valid outcome, and companions still run:
        # each exclusion count is independently meaningful for disclosure, so
        # there is no shortcut that skips companion execution.
        main_sql = (
            "SELECT country, SUM(quantity * unit_price) AS revenue "
            "FROM transactions WHERE country = 'Atlantis' GROUP BY country"
        )
        zero_count = _count_companion(
            RuleName.AVG_EXCLUDE_ZERO_PRICE,
            "country = 'Atlantis' AND unit_price = 0",
        )
        state = _state_with_companions(main_sql, {zero_count.rule: zero_count})

        result = execute_queries(state)

        self.assertIsNone(result.error)
        self.assertEqual(result.main_results, [])
        # A companion count of 0 is a normal, valid result -- never a failure.
        self.assertEqual(
            result.sql_companions[RuleName.AVG_EXCLUDE_ZERO_PRICE].excluded_count,
            0,
        )
        self.assertEqual(
            result.sql_companions[RuleName.AVG_EXCLUDE_ZERO_PRICE].status,
            "success",
        )

    def test_shape_invalid_companion_sets_taxonomy_reason_and_stops(self):
        # A COUNT(*) companion that returns more than one row (here by an
        # injected GROUP BY) fails the strict one-row/one-column shape rule.
        succeeds = _count_companion(
            RuleName.CUSTOMER_EXCLUDE_NULL, "customer_id IS NULL"
        )
        shape_invalid = CompanionQuery(
            rule=RuleName.PRODUCT_EXCLUDE_NONPRODUCT,
            sql=(
                "SELECT COUNT(*) AS excluded_count FROM transactions "
                "GROUP BY country"
            ),
        )
        never_runs = _count_companion(
            RuleName.AVG_EXCLUDE_ZERO_PRICE, "unit_price = 0"
        )
        companions = {
            succeeds.rule: succeeds,
            shape_invalid.rule: shape_invalid,
            never_runs.rule: never_runs,
        }
        state = _state_with_companions(_MAIN_SUM_REVENUE_SQL, companions)
        stored = state.sql_companions

        executed_sqls: list[str] = []

        def recording_connect(path):
            return _RecordingConnection(connect_readonly(path), executed_sqls)

        with mock.patch.object(
            nodes_module, "connect_readonly", side_effect=recording_connect
        ):
            result = execute_queries(state)

        # The shape-invalid companion uses the query_result_shape_invalid
        # taxonomy reason, suffixed with its rule name.
        self.assertEqual(
            result.error,
            "query_result_shape_invalid:PRODUCT_EXCLUDE_NONPRODUCT",
        )
        # Hard stop: the later companion's SQL was never executed.
        self.assertEqual(
            executed_sqls,
            [_MAIN_SUM_REVENUE_SQL, succeeds.sql, shape_invalid.sql],
        )
        self.assertNotIn(never_runs.sql, executed_sqls)
        # Atomic invariant: no companion and no main result was written.
        self.assertIs(result.sql_companions, stored)
        for rule in companions:
            self.assertEqual(result.sql_companions[rule].status, "pending")
            self.assertIsNone(result.sql_companions[rule].excluded_count)
        self.assertIsNone(result.main_results)


    def test_companion_execution_failure_stops_and_writes_nothing(self):
        # First prove the failing companion's SQL genuinely fails at the
        # database level, so this test exercises the real exception path.
        succeeds = _count_companion(
            RuleName.CUSTOMER_EXCLUDE_NULL, "customer_id IS NULL"
        )
        fails = CompanionQuery(
            rule=RuleName.AVG_EXCLUDE_ZERO_PRICE,
            sql=(
                "SELECT COUNT(*) AS excluded_count FROM missing_table "
                "WHERE unit_price = 0"
            ),
        )
        never_runs = _count_companion(
            RuleName.PRODUCT_EXCLUDE_NONPRODUCT, "line_item_type <> 'product'"
        )
        companions = {
            succeeds.rule: succeeds,
            fails.rule: fails,
            never_runs.rule: never_runs,
        }
        state = _state_with_companions(_MAIN_SUM_REVENUE_SQL, companions)

        with self.assertRaises(sqlite3.OperationalError):
            _reference_rows(fails.sql)

        # Capture the exact objects the node receives before it runs.
        stored = state.sql_companions
        stored_succeeds = stored[RuleName.CUSTOMER_EXCLUDE_NULL]
        stored_fails = stored[RuleName.AVG_EXCLUDE_ZERO_PRICE]
        stored_never_runs = stored[RuleName.PRODUCT_EXCLUDE_NONPRODUCT]

        executed_sqls: list[str] = []

        def recording_connect(path):
            return _RecordingConnection(connect_readonly(path), executed_sqls)

        with mock.patch.object(
            nodes_module, "connect_readonly", side_effect=recording_connect
        ):
            result = execute_queries(state)

        # Exact reason string names the failing companion's rule.
        self.assertEqual(
            result.error, "query_execution_failed:AVG_EXCLUDE_ZERO_PRICE"
        )
        # Statement order proves the hard stop: main, the first (genuinely
        # successful) companion, then the failing one -- the later companion's
        # SQL is never executed.
        self.assertEqual(
            executed_sqls,
            [_MAIN_SUM_REVENUE_SQL, succeeds.sql, fails.sql],
        )
        self.assertNotIn(never_runs.sql, executed_sqls)

        # Atomic invariant with object-identity rigor: the mapping and every
        # entry object are exactly what the node received, and none was written
        # -- including the companion that genuinely succeeded before the
        # failure. No main results were written either.
        self.assertIs(result.sql_companions, stored)
        self.assertIs(
            result.sql_companions[RuleName.CUSTOMER_EXCLUDE_NULL], stored_succeeds
        )
        self.assertIs(
            result.sql_companions[RuleName.AVG_EXCLUDE_ZERO_PRICE], stored_fails
        )
        self.assertIs(
            result.sql_companions[RuleName.PRODUCT_EXCLUDE_NONPRODUCT],
            stored_never_runs,
        )
        for companion in (stored_succeeds, stored_fails, stored_never_runs):
            self.assertEqual(companion.status, "pending")
            self.assertIsNone(companion.excluded_count)
        self.assertEqual(stored_succeeds.sql, succeeds.sql)
        self.assertEqual(stored_fails.sql, fails.sql)
        self.assertEqual(stored_never_runs.sql, never_runs.sql)
        self.assertIsNone(result.main_results)


if __name__ == "__main__":
    unittest.main()