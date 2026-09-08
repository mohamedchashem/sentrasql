"""Node 5 - ``validate_guardrails`` (one node per module).

Deterministic, AST-based: runs the main query, the grouped-query total
(``state.sql_total`` when present), and every companion through
``db.guardrails.validate_sql`` against the live schema, routing any
rejection to the error path or writing the guardrail-enforced SQL and
truncation flags back on a full pass (DESIGN_LOG.md section 20).

The module-level ``_live_schema`` introspection helper belongs here
because ``validate_guardrails`` is the only node that needs the full
per-table schema shape ``validate_sql`` expects."""

from __future__ import annotations

import sqlite3

from db.connect import connect_readonly
from db.guardrails import validate_sql
from graph.node_shared import _MAIN_DB_PATH
from graph.state import CompanionQuery, GraphState


def _live_schema(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """Introspect every live table's ``{table: {column}}`` schema from ``conn``.

    Returns the shape ``db.guardrails.validate_sql`` expects -- each
    non-internal table name in ``sqlite_master`` mapped to the set of its
    columns from ``PRAGMA table_info``, read over the same read-only connection
    that ``_live_transactions_columns`` uses. Internal ``sqlite_%`` tables are
    skipped so only application tables land in the guardrail whitelist.
    """
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return {
        row[0]: {
            col[1] for col in conn.execute(f'PRAGMA table_info("{row[0]}")')
        }
        for row in rows
    }


def validate_guardrails(state: GraphState) -> GraphState:
    """Node 5. Deterministic, AST-based. Parses state.sql_main, the grouped-query
    total (``state.sql_total`` when compile_sql produced one), and every entry
    in state.sql_companions before execution. Enforces SELECT-only, whitelisted
    tables/columns, no destructive keywords, bounded result size. Sets
    state.guardrail_status to 'passed' or 'failed'. On failure, must set
    state.error and the graph must route to an error path — never to execution.

    Every generated statement goes through the same ``db.guardrails.validate_sql``
    gate against the live schema, and the total is no exception: a grouped
    query's ungrouped total is a separate statement that will touch the
    database, so it must be independently validated exactly like the main query
    and the companions (DESIGN_LOG.md section 20). The total is validated only
    after the main query has passed and before the companions; on the success
    path its enforced (row-limited) SQL is stored back onto ``state.sql_total``.
    The total's own truncation flag is deliberately not stored: a scalar
    single-row aggregate can never actually be cut by row-limit enforcement.
    """

    if state.error is not None:
        # Error passthrough: an upstream failure (e.g. one of compile_sql's
        # invalid_intent gates) has already routed the graph toward the error
        # path. compile_sql guarantees sql_main is None on every such path, so
        # there is no SQL to validate -- return the state completely untouched.
        return state

    if state.sql_main is None:
        # No main query to validate (upstream compile_sql was inert rather than
        # errored, e.g. its intent was missing). Nothing this stage can reject;
        # keep the previous stub behavior of passing through untouched.
        return state

    # Fetch the live schema once over a read-only connection, reusing the same
    # introspection pattern compile_sql's runtime gates use.
    conn = connect_readonly(_MAIN_DB_PATH)
    try:
        schema = _live_schema(conn)
    finally:
        conn.close()

    # Validate the main query, remembering the enforced SQL and truncation flag
    # validate_sql returns. Nothing is written back yet: the write-back happens
    # only once the whole plan has passed (below), so a later failure can never
    # leave a half-enforced state behind.
    passed, reason, enforced_main_sql, main_truncated = validate_sql(
        state.sql_main, schema
    )
    if not passed:
        # A rejected main query is a hard stop: mirror the guardrail's own
        # reason string verbatim, mark the stage failed, and route to the error
        # path. sql_companions is deliberately left completely untouched here.
        state.error = reason
        state.guardrail_status = "failed"
        return state

    # Grouped-query total validation runs only after the main query has passed
    # (and before the companions). compile_sql compiles state.sql_total only
    # when query_intent.group_by is non-empty; a scalar query carries
    # sql_total=None and skips this stage entirely. When a total exists it goes
    # through the very same validate_sql gate as the main query and every
    # companion -- it is a separate statement that will touch the database, so
    # it must be independently validated, never assumed safe because the main
    # query passed. Its enforced SQL is collected here and written back on the
    # success path below; its truncation flag is deliberately not stored, since
    # a scalar single-row aggregate can never actually be cut by a row limit.
    enforced_total_sql: str | None = None
    if state.sql_total is not None:
        passed, reason, enforced_sql, _ = validate_sql(state.sql_total, schema)
        if not passed:
            # A rejected total is a hard stop exactly like a rejected main
            # query or companion: mirror the guardrail's own reason string
            # verbatim, mark the stage failed, and route to the error path. The
            # return halts before any companion is validated, and nothing is
            # written back -- no enforced SQL and no truncation flags land
            # anywhere, preserving the invariant that on any failure the state
            # stays exactly as compile_sql produced it.
            state.error = reason
            state.guardrail_status = "failed"
            return state
        enforced_total_sql = enforced_sql

    # Companion validation runs only after the main query (and the grouped-query
    # total, when one exists) has passed. Every
    # CompanionQuery in state.sql_companions goes through the same validate_sql
    # gate against the very same schema already fetched above. Each companion's
    # enforced result is collected next to the companion itself, and the
    # write-back is deferred until every statement has passed.
    enforced_companions: list[tuple[CompanionQuery, str, bool]] = []
    for companion in state.sql_companions.values():
        passed, reason, enforced_sql, truncated = validate_sql(
            companion.sql, schema
        )
        if not passed:
            # A rejected companion is a hard stop, exactly like a rejected main
            # query: mirror the guardrail's own reason string verbatim, mark
            # the stage failed, and route to the error path. The return halts
            # the loop, so companions after the failing one are never
            # validated. Nothing is written back either -- no enforced SQL and
            # no truncation flags land on the companions validated before this
            # failure, preserving the hard invariant that on any failure
            # state.sql_companions stays exactly as compile_sql produced it
            # (and sql_main stays exactly as it was too).
            state.error = reason
            state.guardrail_status = "failed"
            return state
        enforced_companions.append((companion, enforced_sql, truncated))

    # Success path: the main query, the grouped-query total (when present), and
    # every companion all passed the same validate_sql gate, so the
    # guardrail-enforced (row-limited) statements are now the plan to execute.
    # Overwrite state.sql_main with validate_sql's enforced main SQL and
    # propagate its truncation flag to state.main_truncated, store the enforced
    # total back onto state.sql_total when one was validated, then do the same
    # per companion -- each companion receives its own enforced SQL and its own
    # truncation flag from its own validate_sql result, never a value copied
    # across the whole plan.
    state.sql_main = enforced_main_sql
    state.main_truncated = main_truncated
    if enforced_total_sql is not None:
        state.sql_total = enforced_total_sql
    for companion, enforced_sql, truncated in enforced_companions:
        companion.sql = enforced_sql
        companion.truncated = truncated
    state.guardrail_status = "passed"
    return state
