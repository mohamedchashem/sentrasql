"""Node 6 - ``execute_queries`` (one node per module).

Deterministic, no LLM: opens one read-only connection, executes
``state.sql_main``, the grouped-query total, and every companion in
order with strict count-shape validation and atomic write-back, so a
database-level failure routes to the error path instead of crashing
the graph.

This module is the private home of the execution helpers and reason
constants that ``execute_queries`` alone depends on:
``_grouped_total_sql`` (derives the scalar top-level aggregate from a
grouped main statement, DESIGN_LOG.md section 20), ``_fetch_query``,
and the ``query_execution_failed:*`` / ``query_result_shape_invalid:*``
reason codes."""

from __future__ import annotations

import sqlite3

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from db.connect import connect_readonly
from graph.node_shared import _MAIN_DB_PATH
from graph.state import GraphState, RuleName


# Machine-readable ``state.error`` reason for a main-query execution failure in
# ``execute_queries`` (Node 6). Two distinct conditions share the one code
# because both mean the main query could not produce trustworthy
# ``state.main_results``: a real database-level exception (``sqlite3.Error``,
# e.g. ``sqlite3.OperationalError``) raised while connecting or executing
# ``state.sql_main``, and an execution that somehow returned no usable tabular
# result set. The ``:main`` suffix namespaces the failure to the main query;
# companion failures use rule-suffixed sibling codes built inline where they
# are set -- ``query_execution_failed:<rule>`` for a ``sqlite3.Error`` while
# running a companion, ``query_result_shape_invalid:<rule>`` for a companion
# result that fails the strict one-row/one-column count-shape rule.
_QUERY_EXECUTION_FAILED_MAIN_REASON = "query_execution_failed:main"


# Machine-readable ``state.error`` reason codes for the grouped top-level
# aggregate query that ``execute_queries`` additionally runs per DESIGN_LOG.md
# section 20 (a grouped main query's ungrouped total). Both are deliberately
# distinct from the main-query codes above: the main query already succeeded
# by the time this derived query runs, so blaming "main" for its failure would
# be a present-but-wrong diagnosis. ``query_execution_failed:total`` covers a
# database-level exception while running the derived total query, and
# ``query_result_shape_invalid:total`` covers a total result that is not the
# single-row, single-column aggregate record the derived query is guaranteed
# to produce when compile_sql built the statement. Neither path is reachable
# on a guardrail-passed, compile_sql-built statement; both exist so an
# upstream inconsistency can never crash the graph or silently drop the total.
_QUERY_EXECUTION_FAILED_TOTAL_REASON = "query_execution_failed:total"


_QUERY_RESULT_SHAPE_INVALID_TOTAL_REASON = "query_result_shape_invalid:total"


def _grouped_total_sql(main_sql: str) -> str | None:
    """Return the ungrouped aggregate SQL for ``main_sql`` when it is grouped.

    DESIGN_LOG.md section 20 requires every grouped main result to carry a
    top-level ungrouped aggregate (``result.total``) alongside its row-level
    breakdown. The cleanest source for that aggregate is the already-validated
    ``main_sql`` itself, transformed rather than re-authored: this function
    parses ``main_sql`` and, when it is a SELECT carrying a GROUP BY, returns a
    second lightweight SELECT that is identical except that the group-by
    projections are dropped, the GROUP BY clause is removed, and any
    LIMIT/OFFSET/ORDER BY are removed -- leaving exactly the same FROM, WHERE
    (base filters and every AND-composed rule exclusion intact), and the same
    aggregate expression, which is precisely the scalar query the same intent
    would have compiled. This cannot introduce a table, column, function, or
    join that the main query did not already carry past guardrail validation,
    and its single row is never subject to the row-limit ceiling the grouped
    statement's LIMIT enforces.

    Returns ``None`` when ``main_sql`` is not a grouped SELECT -- a scalar
    query needs no total -- or when sqlglot cannot parse it (an exotic
    statement that could not have passed ``validate_guardrails``; the node
    then simply omits the total rather than failing the already-executed main
    query over a derived-query nicety).
    """
    try:
        statement = parse_one(main_sql, read="sqlite")
    except ParseError:
        return None
    if not isinstance(statement, exp.Select):
        return None
    group = statement.args.get("group")
    if group is None:
        return None

    # Group-by columns, by name. compile_sql projects each group-by dimension
    # as an unaliased column, so stripping exactly those projections is safe.
    grouped_names = {expression.name for expression in group.expressions}
    total = statement.copy()
    kept_expressions: list[exp.Expression] = []
    for projection in total.expressions:
        base = (
            projection.unalias() if isinstance(projection, exp.Alias) else projection
        )
        if isinstance(base, exp.Column) and base.name in grouped_names:
            continue
        kept_expressions.append(projection)
    if not kept_expressions:
        # A SELECT projecting only its group-by columns is not an aggregate
        # query; there is no metric to total.
        return None

    total.set("expressions", kept_expressions)
    total.set("group", None)
    total.set("limit", None)
    total.set("offset", None)
    total.set("order", None)
    return total.sql(dialect="sqlite")


def _fetch_query(
    conn: sqlite3.Connection, sql: str
) -> tuple[list[str], list[tuple]]:
    """Execute ``sql`` on ``conn`` and return ``(column_names, raw_rows)``.

    ``sql`` is executed as-is on the caller's connection; a database-level
    failure propagates to the caller, where each ``execute_queries`` failure
    site translates it. Column names come from ``cursor.description``, so a
    statement that produces no result set yields an empty column list.
    """
    cursor = conn.execute(sql)
    raw_rows = cursor.fetchall()
    column_names = [
        description[0] for description in (cursor.description or [])
    ]
    return column_names, raw_rows


def execute_queries(state: GraphState) -> GraphState:
    """Node 6. Execute ``state.sql_main`` and every companion query.

    Gating mirrors ``validate_guardrails``: a state already carrying
    ``state.error`` -- the graph is already routing toward its error path -- or
    whose plan never passed guardrail validation
    (``state.guardrail_status != "passed"``) is returned completely untouched:
    no connection is opened and no SQL is executed. A state that does reach the
    execution body always carries a real ``state.sql_main`` --
    ``validate_guardrails`` only flips ``guardrail_status`` to ``"passed"``
    after storing the enforced statement back -- so the main query is executed
    unconditionally from there.

    Execution opens one read-only connection (``db.connect.connect_readonly``)
    and first runs ``state.sql_main``. Any database-level exception raised
    while connecting or running the main query -- ``sqlite3.OperationalError``
    and the rest of the ``sqlite3.Error`` hierarchy -- is translated into
    ``state.error = _QUERY_EXECUTION_FAILED_MAIN_REASON``
    (``"query_execution_failed:main"``) and returned, so a raw exception can
    never propagate out of this node and crash the graph. A clean main
    execution gets a deliberately light structural backstop -- the executed
    statement must have produced a real column list and rows whose length
    matches it -- because ``compile_sql`` deterministically builds
    well-formed aggregate SELECTs and SQLite cannot hand a ragged or
    description-less row set back for one; a structurally unusable main result
    is an execution failure with the same reason code. Zero main rows is a
    valid, non-failure outcome (an empty list, distinct from ``None`` = "not
    executed yet").

    Grouped totals (DESIGN_LOG.md section 20): when the executed
    ``state.sql_main`` is a SELECT carrying a GROUP BY and returned at least
    one row, the node additionally derives and runs the matching ungrouped
    aggregate query (``_grouped_total_sql`` -- the same statement minus its
    group-by projections, GROUP BY clause, and LIMIT/OFFSET/ORDER BY, which
    preserves the FROM, every WHERE condition, and the aggregate expression
    verbatim) on the same connection, and stores the result as the wrapper
    ``{"rows": [...], "total": {...}}`` -- ``"rows"`` is exactly the row-level
    breakdown the main query produced and ``"total"`` is the single-record
    result of the derived query (what a scalar query for the same intent would
    have returned). The derived total is executed before any companion, and a
    database-level failure or a non-single-row/column result sets the dedicated
    reasons ``"query_execution_failed:total"`` / ``"query_result_shape_invalid:
    total"`` -- never the main-query code, which would wrongly blame a query
    that already succeeded. A grouped query that matched no rows keeps the
    empty list: with no breakdown there is nothing for a total to summarize,
    and a SUM over no rows is NULL, not a trustworthy aggregate.

    Only once the main query has succeeded do companions run, each in
    ``state.sql_companions`` dict order on the same connection -- including
    when the main query returned zero rows, because every exclusion count is
    independently meaningful and disclosures need it regardless. Every
    companion result is validated against the strict count-shape rule: exactly
    one row and exactly one column. ``compile_sql`` emits ``COUNT(*)``
    companions, so a count value of 0 is a normal, valid result; the shape
    rule fails only on zero rows or more than one row/column, never on the
    count value itself. Two rule-suffixed reason codes cover companion
    failures: ``query_execution_failed:<rule>`` for a database-level exception
    while running the companion, and ``query_result_shape_invalid:<rule>`` for
    a result that fails the shape rule. Either sets ``state.error`` and stops
    immediately -- remaining companions never execute.

    Write-back is atomic, mirroring ``validate_guardrails``: the main query's
    rows and every companion's validated count are accumulated in local
    variables while the queries run, and nothing touches the state until the
    main query and every companion have succeeded. Only then is the whole
    result written back in one pass (``state.main_results`` plus each
    ``CompanionQuery.status`` and ``.excluded_count``). On any failure the
    node returns with ``state.main_results`` and every companion exactly as
    they were when it ran -- no partial results are ever visible downstream.
    """

    if state.error is not None or state.guardrail_status != "passed":
        # Error/guardrail passthrough: never execute SQL against a state that is
        # already routing to the error path or whose plan never passed the
        # guardrail stage. This also covers the residual inert-upstream case
        # (compile_sql with no intent leaves sql_main None and guardrail_status
        # "pending"), which must keep passing through untouched.
        return state

    # Everything below accumulates into local variables; nothing is written to
    # the state until the atomic write-back block at the end.
    conn = None
    main_results: list[dict] | dict = []
    try:
        conn = connect_readonly(_MAIN_DB_PATH)

        # Main query first. sql_main is guaranteed non-None here (the gate
        # above, plus validate_guardrails only writing it back on a full pass),
        # and it must succeed before any companion runs.
        main_column_names, raw_main_rows = _fetch_query(conn, state.sql_main)
        # Light structural backstop on the main result shape: the statement
        # must have produced real columns and rows whose length matches them.
        # compile_sql builds these SELECTs deterministically, so this is
        # deliberately not an over-engineered schema validator.
        if not main_column_names:
            state.error = _QUERY_EXECUTION_FAILED_MAIN_REASON
            return state
        for row in raw_main_rows:
            if len(row) != len(main_column_names):
                state.error = _QUERY_EXECUTION_FAILED_MAIN_REASON
                return state
            main_results.append(dict(zip(main_column_names, row)))

        # Grouped top-level aggregate (DESIGN_LOG.md section 20). When the main
        # query is grouped and produced rows, derive and run the matching
        # ungrouped total query (guaranteed by _grouped_total_sql to be a
        # structurally restricted form of the already-executed, guardrail-
        # approved statement) and wrap the breakdown: main_results becomes
        # {"rows": [...], "total": {<scalar aggregate record>}}. The total runs
        # before any companion, and its failures use the dedicated
        # ":total"-suffixed reasons so the already-succeeded main query is
        # never blamed. Zero main rows stays the empty list (no breakdown, no
        # total), exactly as before this change.
        if main_results:
            total_sql = _grouped_total_sql(state.sql_main)
            if total_sql is not None:
                try:
                    total_column_names, total_rows = _fetch_query(
                        conn, total_sql
                    )
                except sqlite3.Error:
                    state.error = _QUERY_EXECUTION_FAILED_TOTAL_REASON
                    return state
                if (
                    len(total_rows) != 1
                    or len(total_column_names) != 1
                    or len(total_rows[0]) != len(total_column_names)
                ):
                    state.error = _QUERY_RESULT_SHAPE_INVALID_TOTAL_REASON
                    return state
                main_results = {
                    "rows": main_results,
                    "total": dict(zip(total_column_names, total_rows[0])),
                }

        # Companion queries. Each runs only after the main query succeeded, in
        # dict order, and even when the main query returned zero rows -- a
        # zero-row main is a valid outcome, and every exclusion count is still
        # independently meaningful for disclosure.
        companion_counts: dict[RuleName, int] = {}
        for rule, companion in state.sql_companions.items():
            try:
                column_names, rows = _fetch_query(conn, companion.sql)
            except sqlite3.Error:
                # A real database-level failure in this companion. Stop on the
                # exact rule-suffixed reason; later companions never execute.
                state.error = f"query_execution_failed:{rule.value}"
                return state
            # Strict count-shape validation: exactly one row and exactly one
            # column. A count value of 0 is a normal, valid result -- the
            # failure condition is zero rows or more than one row/column, never
            # the count value itself.
            if len(rows) != 1 or len(column_names) != 1:
                state.error = f"query_result_shape_invalid:{rule.value}"
                return state
            companion_counts[rule] = rows[0][0]
    except sqlite3.Error:
        # A database-level failure while connecting or while running the main
        # query (unreadable connection, syntax error, unknown table/column,
        # ...). Companion failures never reach this handler: they are
        # translated above with their own rule-suffixed reason and returned
        # immediately. Never let the exception escape and crash the graph.
        state.error = _QUERY_EXECUTION_FAILED_MAIN_REASON
        return state
    finally:
        if conn is not None:
            conn.close()

    # Atomic write-back: reached only when the main query and every companion
    # succeeded. Nothing above wrote to the state, so a failure can never leave
    # partial results behind -- main_results and every companion stay exactly
    # as they were when this node ran (the same invariant validate_guardrails
    # proves on its own failure path).
    state.main_results = main_results
    for rule, companion in state.sql_companions.items():
        companion.status = "success"
        companion.excluded_count = companion_counts[rule]
    return state
