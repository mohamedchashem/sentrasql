"""Node 4 - ``compile_sql`` (one node per module).

Deterministic, no LLM: builds ``state.sql_main`` (plus
``state.sql_total`` for grouped queries) and ``state.sql_companions``
from ``state.query_intent`` and ``state.applicable_rules`` using the
sqlglot expression API. See the ``compile_sql`` docstring for the
implemented cases (base case plus all four policy rules), the four
early-validation gates, and the grouped-query total.

This module is the private home of every SQL-compilation helper and
constant that ``compile_sql`` alone depends on (metric/column/condition
builders, rule predicates, exclusion count selects, intent-validation
helpers, and the compile-side schema/filter constants). Shared helpers
used by more than one node live in ``graph.node_shared``."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime

from sqlglot import exp

from db.connect import connect_readonly
from graph.node_shared import _MAIN_DB_PATH
from graph.state import (
    CompanionQuery,
    Filters,
    GraphState,
    QueryIntent,
    RuleName,
)


# The single fact table every base-case query reads. Rule-specific queries may
# later join other whitelisted tables (e.g. country_timezones), but the plain
# base case never leaves this table.
_MAIN_TABLE = "transactions"


# Every column the base case may select, group by, or filter on. A query that
# needs a column from another table requires the join logic that belongs to the
# rule-specific tasks, not to this base case.
_TRANSACTIONS_COLUMNS = frozenset(
    {
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
    }
)


# The single time/datetime column a date-range filter constrains. The typed
# ``DateRangeFilter`` carries no column name -- the filter kind itself implies
# its target, because ``transactions`` has exactly one ISO-8601 time column
# (``invoice_timestamp``). This fixed binding lives here rather than being
# spelled inline in every condition builder that consumes a date range.
_DATE_FILTER_COLUMN = "invoice_timestamp"


# Canonical derived metrics: business measures that are not a single column but
# a fixed expression over transactions columns. ``revenue`` is line amount
# (quantity * unit_price), summed over signed quantities exactly as recorded --
# see DESIGN_LOG.md section 4.6 for why this is the "net" definition and that
# it implies no verified reconciliation of returns against sales. Each entry is
# a zero-argument builder so every compiled query gets a fresh AST.
_DERIVED_METRICS = {
    "revenue": lambda: exp.column("quantity") * exp.column("unit_price"),
}


# Every metric the compiler can aggregate over: the derived metrics above plus
# the plain transactions columns. The early-validation gates use it to decide
# what is a real, known metric before judging which aggregations apply to it.
_KNOWN_METRICS = _TRANSACTIONS_COLUMNS | frozenset(_DERIVED_METRICS)


# The metrics whose aggregation is an *amount*: the only ones for which the
# additive aggregations (SUM, AVG) are defined. ``revenue`` is derived
# (quantity * unit_price); ``quantity`` and ``unit_price`` are the two numeric
# measure columns of transactions. Every other known metric is a
# dimension/identifier/time column where SUM/AVG is not meaningful -- e.g. AVG
# over ``customer_id`` is defined on a REAL column yet is not sensible to
# average -- so the aggregation/metric gate rejects those combinations
# (``invalid_intent:aggregation_metric_mismatch``).
_NUMERIC_MEASURE_METRICS = frozenset({"revenue", "quantity", "unit_price"})


# Machine-readable ``state.error`` reason codes for the four early-validation
# gates that run before any SQL construction in ``compile_sql``. The graph
# routes to the error path whenever ``state.error`` is not ``None``
# (graph/build.py ``_route_after_execution``); ``handle_error`` is responsible
# for phrasing these codes into a user-facing message.
_AGGREGATION_METRIC_MISMATCH_REASON = "invalid_intent:aggregation_metric_mismatch"


_INVALID_GROUP_BY_REASON = "invalid_intent:invalid_group_by"


_INVALID_DATE_RANGE_REASON = "invalid_intent:invalid_date_range"


_NO_MATCHING_DATA_REASON = "invalid_intent:no_matching_data"


_RULE_MISMATCH_REASON = "invalid_intent:rule_mismatch"


# Rule NET_VS_GROSS (rule 1) resolves which of its three named revenue/quantity
# filter variants a query asks for (DESIGN_LOG.md section 4.6 rule 1). Exactly
# one variant applies per query -- the variants are mutually exclusive and never
# compose with each other -- and only the two non-default variants contribute a
# WHERE condition:
#   "net"                    -> nothing extra (the default; signed amounts summed
#                               exactly as recorded need no filter)
#   "gross_of_cancellations" -> invoice_id NOT LIKE 'C%' (exclude the "C"-flagged
#                               cancelled invoices)
#   "returns"                -> quantity < 0 (keep only return line items,
#                               independent of the cancellation flag)
# Each non-net entry is a zero-argument builder so every compiled query gets a
# fresh AST, mirroring ``_DERIVED_METRICS``; the column names go through
# ``_column`` so a typo cannot silently compile a bogus reference.
_NET_GROSS_VARIANT_CONDITIONS: dict[
    str, Callable[[], exp.Condition] | None
] = {
    "net": None,
    "gross_of_cancellations": lambda: exp.Like(
        this=_column("invoice_id"),
        expression=exp.Literal.string("C%"),
        negate=True,
    ),
    "returns": lambda: exp.LT(
        this=_column("quantity"), expression=exp.Literal.number(0)
    ),
}


# QueryIntent.aggregation value -> sqlglot aggregate-expression class. The
# Literal type in graph/state.py already constrains the keys; this map keeps the
# AST construction data-driven rather than a chain of if/elif.
_AGGREGATION_CLASSES = {
    "sum": exp.Sum,
    "avg": exp.Avg,
    "count": exp.Count,
    "min": exp.Min,
    "max": exp.Max,
}


# Stable output alias for every companion "how many rows were excluded" query.
# The graph node that reads companion results (execute_queries) consumes this
# column, so it is a single named constant, never spelled inline.
_EXCLUDED_COUNT_ALIAS = "excluded_count"


def _metric_expression(metric: str) -> exp.Expression:
    """Return the sqlglot expression a ``metric`` name aggregates over.

    A metric is either a canonical derived metric (``revenue``) or, when it is
    a plain ``transactions`` column name (e.g. ``unit_price``, ``quantity``),
    that column itself. Anything else is not something the base case can
    compile, so it fails loudly rather than silently emitting a bogus column.
    """
    builder = _DERIVED_METRICS.get(metric)
    if builder is not None:
        return builder()
    if metric in _TRANSACTIONS_COLUMNS:
        return exp.column(metric)
    raise ValueError(
        f"Unsupported metric {metric!r}: not a derived metric "
        f"({', '.join(sorted(_DERIVED_METRICS))}) nor a transactions column."
    )


def _column(name: str) -> exp.Column:
    """Return ``exp.column(name)`` after verifying the column exists on the main table."""
    if name not in _TRANSACTIONS_COLUMNS:
        raise ValueError(
            f"Unknown column {name!r}: the base case only supports columns on "
            f"table {_MAIN_TABLE!r} (got {sorted(_TRANSACTIONS_COLUMNS)})."
        )
    return exp.column(name)


def _literal(value: object) -> exp.Expression:
    """Convert a Python scalar into the matching sqlglot literal node."""
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    if isinstance(value, (int, float)):
        return exp.Literal.number(value)
    if isinstance(value, str):
        return exp.Literal.string(value)
    raise ValueError(f"Unsupported filter literal {value!r} of type {type(value).__name__}.")


def _filter_conditions(filters: Filters) -> list[exp.Condition]:
    """Compile ``query_intent.filters`` (a typed ``Filters`` object) into SQL conditions.

    Each filter kind is read through its own ``*_present`` flag before its
    paired value is read -- never through the value itself, and never by
    iterating the object like a dict. Each kind contributes its conditions
    independently and each condition is returned separately so callers keep
    them distinguishable; ``Select.where`` ANDs them. A present country filter
    compiles to one equality on the ``country`` column. A date-range filter
    compiles to zero, one, or two boundary comparisons against the single time
    column (``_DATE_FILTER_COLUMN``) depending on which boundaries are
    present, which is what makes half-open ranges like "since March" (start
    present, end absent) or "before December" (end present, start absent)
    representable.
    """
    conditions: list[exp.Condition] = []
    if filters.country.present:
        conditions.append(
            exp.EQ(this=_column("country"), expression=_literal(filters.country.value))
        )
    if filters.date_range.start_present:
        conditions.append(
            exp.GTE(
                this=_column(_DATE_FILTER_COLUMN),
                expression=_literal(filters.date_range.start),
            )
        )
    if filters.date_range.end_present:
        conditions.append(
            exp.LTE(
                this=_column(_DATE_FILTER_COLUMN),
                expression=_literal(filters.date_range.end),
            )
        )
    return conditions


def _negate_equality(condition: exp.EQ) -> exp.NEQ:
    """Return the inequality form of an equality condition, built on a copy.

    Rule exclusions are the negation of a predicate the code authored once (for
    rule AVG_EXCLUDE_ZERO_PRICE: the companion counts ``unit_price = 0`` rows
    while the main query excludes ``unit_price != 0`` rows). The equality node
    is deep-copied first and the copy's two operands are mounted on the new
    inequality node, so the original predicate object is never aliased into
    more than one SQL tree and no caller ever re-authors the operand column or
    literal by hand.
    """
    copied = condition.copy()
    return exp.NEQ(this=copied.this, expression=copied.expression)


def _negate_inequality(condition: exp.NEQ) -> exp.EQ:
    """Return the equality form of an inequality condition, on a copy.

    Rule PRODUCT_EXCLUDE_NONPRODUCT is the mirror image of rule 2's equality
    pattern: its shared predicate selects the *excluded* rows as an inequality
    (``line_item_type != 'product'`` -- the fee/adjustment rows the companion
    counts), so the main query's complement is the equality
    (``line_item_type = 'product'`` -- the product rows that stay in scope).
    As in ``_negate_equality``, the shared predicate is deep-copied first and
    the copy's two operands are mounted on the new equality node, so the
    original predicate object is never aliased into more than one SQL tree and
    no caller ever re-authors the operand column or literal by hand.
    """
    copied = condition.copy()
    return exp.EQ(this=copied.this, expression=copied.expression)


def _negate_is_null(condition: exp.Is) -> exp.Is:
    """Return the ``IS NOT NULL`` form of an ``IS NULL`` condition, on a copy.

    Rule CUSTOMER_EXCLUDE_NULL is the IS-NULL analogue of rule 2's equality
    pattern: the companion counts ``customer_id IS NULL`` rows while the main
    query keeps only ``customer_id IS NOT NULL`` rows. As in
    ``_negate_equality``, the shared predicate is deep-copied first and the
    copy's operands are mounted on the replacement node -- here the copied
    ``NULL`` expression is wrapped in ``NOT`` (which sqlglot renders as
    ``IS NOT NULL``), so the original predicate object is never aliased into
    more than one SQL tree and no caller ever re-authors the operand column by
    hand.
    """
    copied = condition.copy()
    return exp.Is(this=copied.this, expression=exp.Not(this=copied.expression))


def _metric_aggregate(intent: QueryIntent) -> exp.Expression:
    """Return the aliased aggregate expression ``intent`` projects in SELECT.

    Authored exactly once and shared by ``_aggregate_select`` (the main query)
    and ``_scalar_aggregate_select`` (the grouped-query total), so the total
    can never drift from the main query's aggregate expression: both project
    the same aggregate node type over the same operand, aliased to the same
    metric name.
    """
    aggregation_class = _AGGREGATION_CLASSES.get(intent.aggregation)
    if aggregation_class is None:
        raise ValueError(f"Unsupported aggregation {intent.aggregation!r}.")
    aggregate = aggregation_class(this=_metric_expression(intent.metric))
    return exp.alias_(aggregate, intent.metric)


def _aggregate_select(
    intent: QueryIntent,
    conditions: list[exp.Condition] | None,
) -> exp.Select:
    """Compile the main aggregate SELECT for ``intent`` over ``conditions``.

    ``conditions`` is a ready-made list of sqlglot WHERE conditions (the shared
    base filters plus any rule exclusion the caller appended) or ``None`` for a
    query with no WHERE clause. Projection order is stable: group-by columns in
    intent order first, then the aggregate expression aliased to the metric so
    execution results carry a stable name.
    """
    select_expressions: list[exp.Expression] = []
    group_by_expressions: list[exp.Column] = []
    if intent.group_by:
        for column_name in intent.group_by:
            select_expressions.append(_column(column_name))
            group_by_expressions.append(exp.column(column_name))

    select_expressions.append(_metric_aggregate(intent))

    query = exp.select(*select_expressions).from_(_MAIN_TABLE)
    if conditions:
        query = query.where(*conditions)
    if group_by_expressions:
        query = query.group_by(*group_by_expressions)
    return query


def _scalar_aggregate_select(
    intent: QueryIntent,
    conditions: list[exp.Condition],
) -> exp.Select:
    """Compile the scalar (ungrouped) aggregate SELECT over ``conditions``.

    This is the shape ``compile_sql`` uses for the grouped-query total: the
    main query's own aggregate expression (``_metric_aggregate``) over the main
    query's *identical* WHERE conditions, with no group-by columns and no GROUP
    BY clause. ``conditions`` must already be deep copies -- never the objects
    attached to the main query's tree, exactly like the companion queries'
    copied conditions. ``compile_sql`` never emits LIMIT/OFFSET/ORDER BY on
    either query, so there is nothing further to strip.
    """
    query = exp.select(_metric_aggregate(intent)).from_(_MAIN_TABLE)
    if conditions:
        query = query.where(*conditions)
    return query


def _count_excluded_select(
    conditions: list[exp.Condition],
) -> exp.Select:
    """Compile the companion ``COUNT(*)`` SELECT for the excluded rows.

    ``conditions`` already carries one rule's exclusion predicate (e.g. the
    shared ``unit_price = 0`` or ``customer_id IS NULL`` predicate) AND-composed
    after the copied base filters, so this query counts exactly the rows that
    rule pushed out of the main query's result.
    """
    count_star = exp.alias_(exp.Count(this=exp.Star()), _EXCLUDED_COUNT_ALIAS)
    query = exp.select(count_star).from_(_MAIN_TABLE)
    if conditions:
        query = query.where(*conditions)
    return query


def _parse_filter_datetime(value: object) -> datetime:
    """Parse an ISO-8601 date/datetime string into a ``datetime``.

    Date-range filter endpoints arrive as ISO-8601 strings (the schema stores
    ``invoice_timestamp`` as an ISO-8601 TEXT column). Both ``T``-separated and
    space-separated spellings are accepted, as are date-only endpoints.
    """
    if not isinstance(value, str):
        raise ValueError(
            "Date-range filter endpoints must be ISO-8601 strings, got "
            f"{value!r} of type {type(value).__name__}."
        )
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"Unparseable date-range filter endpoint {value!r}: expected an "
            "ISO-8601 date or datetime string."
        ) from exc


def _aggregation_metric_mismatch_error(intent: QueryIntent) -> str | None:
    """Return the aggregation/metric-mismatch reason when SUM/AVG targets a non-measure.

    Only the numeric measure metrics (``revenue``, ``quantity``,
    ``unit_price``) can be summed or averaged; averaging e.g. a country or a
    ``customer_id`` -- the latter a REAL column that is still not sensible to
    average -- is rejected (``invalid_intent:aggregation_metric_mismatch``).
    COUNT/MIN/MAX are left alone: they are defined for any metric the compiler
    knows. A metric the compiler does not know at all is deliberately not
    judged here (it still fails loudly later in ``_metric_expression``), so
    this gate only reports on real metrics.
    """
    if intent.aggregation not in ("sum", "avg"):
        return None
    if intent.metric not in _KNOWN_METRICS:
        return None
    if intent.metric not in _NUMERIC_MEASURE_METRICS:
        return _AGGREGATION_METRIC_MISMATCH_REASON
    return None


def _live_transactions_columns(conn: sqlite3.Connection) -> set[str]:
    """Introspect the live ``transactions`` column set from ``conn``."""
    rows = conn.execute(f"PRAGMA table_info({_MAIN_TABLE})").fetchall()
    return {row[1] for row in rows}


def _invalid_group_by_error(
    group_by: list[str] | None, live_columns: set[str]
) -> str | None:
    """Return the invalid-group-by reason when a group-by field is unusable.

    Existence is judged against the live schema introspected from the database
    at runtime (never against a duplicated hardcoded column list), and the
    field must be a legitimate grouping dimension -- the numeric measure
    columns (``quantity``, ``unit_price``) are measures, not dimensions, so
    grouping on them is rejected here too.
    """
    if not group_by:
        return None
    for name in group_by:
        if name not in live_columns or name in _NUMERIC_MEASURE_METRICS:
            return _INVALID_GROUP_BY_REASON
    return None


def _invalid_date_range_error(filters: Filters) -> str | None:
    """Return the invalid-date-range reason when a full range filter is reversed.

    A date-range filter's two boundaries are independent (each carries its own
    ``*_present`` flag), so ordering only exists when both are present: a
    partial range -- start present and end absent, or the reverse -- cannot be
    reversed and always passes this gate. When both boundaries are present and
    the start is later than the end, it is a hard failure -- never silently
    swapped.
    """
    date_range = filters.date_range
    if not (date_range.start_present and date_range.end_present):
        return None
    start = _parse_filter_datetime(date_range.start)
    end = _parse_filter_datetime(date_range.end)
    if start > end:
        return _INVALID_DATE_RANGE_REASON
    return None


def _no_matching_data_error(
    filters: Filters,
    live_columns: set[str],
    conn: sqlite3.Connection,
) -> str | None:
    """Return the no-matching-data reason when a country filter matches nothing.

    Deliberate v1 scope decision, not an oversight: when an exact scalar filter
    value matches no real row the query is refused outright. There is no fuzzy
    correction or suggestion -- a near-miss country name is a hard error, never
    auto-corrected -- and a genuinely zero-answer filter (e.g. a real country
    with no sales in the data) is refused for the same reason, because the
    system cannot distinguish a near-miss from a real zero-cohort value.
    Existence is gated per filter kind: only the scalar country filter (when
    its ``present`` flag is ``True``) is checked against the live rows.
    Date-range endpoints are deliberately NOT existence-gated in v1.
    """
    if not filters.country.present or "country" not in live_columns:
        return None
    row = conn.execute(
        f"SELECT 1 FROM {_MAIN_TABLE} WHERE country = ? LIMIT 1",
        (filters.country.value,),
    ).fetchone()
    if row is None:
        return _NO_MATCHING_DATA_REASON
    return None


def _rule_mismatch_error(
    intent: QueryIntent, applicable_rules: list[RuleName]
) -> str | None:
    """Return the rule-mismatch reason when applicable_rules contradicts intent.

    Structural-only gate (per task scope): the inert ``net_gross`` field only
    takes meaning when rule NET_VS_GROSS fires, so a non-default variant with
    that rule absent indicates a bug upstream in rule detection -- fail loudly.
    Rules 2-4 applicability-versus-intent checks belong to the future
    ``detect_applicable_rules`` node, not here.
    """
    if (
        intent.net_gross != "net"
        and RuleName.NET_VS_GROSS not in applicable_rules
    ):
        return _RULE_MISMATCH_REASON
    return None


def _validate_intent(
    intent: QueryIntent, applicable_rules: list[RuleName]
) -> str | None:
    """Run compile_sql's four early-validation gates; return the first reason.

    Gates, in order: (1) aggregation/metric compatibility, (2) group-by field
    validity against the live schema, (3) filter sanity -- a reversed date
    range first, then filter values with no matching rows -- and (4)
    rule/intent cross-consistency. Returns ``None`` when the intent is valid.
    The read-only live database is opened only when a gate needs it (group-by
    schema existence and no-matching-row checks) and is always closed before
    returning.
    """
    reason = _aggregation_metric_mismatch_error(intent)
    if reason is not None:
        return reason

    needs_live_db = bool(intent.group_by) or intent.filters.country.present
    if needs_live_db:
        conn = connect_readonly(_MAIN_DB_PATH)
        try:
            live_columns = _live_transactions_columns(conn)
            reason = _invalid_group_by_error(intent.group_by, live_columns)
            if reason is not None:
                return reason
            reason = _invalid_date_range_error(intent.filters)
            if reason is not None:
                return reason
            reason = _no_matching_data_error(intent.filters, live_columns, conn)
            if reason is not None:
                return reason
        finally:
            conn.close()
    else:
        reason = _invalid_date_range_error(intent.filters)
        if reason is not None:
            return reason

    return _rule_mismatch_error(intent, applicable_rules)


def compile_sql(state: GraphState) -> GraphState:
    """Node 4. Deterministic, no LLM. Builds state.sql_main (plus state.sql_total for grouped queries) and state.sql_companions from state.query_intent and state.applicable_rules, using shared filter parameters so main and companion queries cannot structurally drift apart. Rule NET_VS_GROSS produces no companion query, per policy — its disclosure is sourced directly from query_intent's resolved filter, not from a CompanionQuery.

    Implemented cases:

    * Base case -- ``state.applicable_rules`` is empty: ``sql_main`` is a plain
      aggregate query compiled straight from ``query_intent`` (aggregation over
      the metric, optional WHERE from filters, optional GROUP BY), and
      ``sql_companions`` is set to ``{}``.
    * ``AVG_EXCLUDE_ZERO_PRICE`` alone -- the main query keeps every base
      filter and AND-composes ``unit_price != 0`` (``Select.where`` ANDs, it
      never replaces existing filters), so zero-price rows drop out of the
      average. ``sql_companions`` then carries exactly one CompanionQuery for
      this rule that counts those excluded rows: the same base filters
      AND-composed with ``unit_price = 0`` as a ``COUNT(*)``.
    * ``CUSTOMER_EXCLUDE_NULL`` alone -- the same pattern over the customer
      dimension: the main query keeps every base filter and AND-composes
      ``customer_id IS NOT NULL``, and ``sql_companions`` carries exactly one
      CompanionQuery that counts the excluded rows (the same base filters
      AND-composed with ``customer_id IS NULL`` as a ``COUNT(*)``).
    * ``PRODUCT_EXCLUDE_NONPRODUCT`` alone -- the same pattern with its
      direction inverted: the main query keeps every base filter and
      AND-composes ``line_item_type = 'product'``, so fee/adjustment/voucher
      rows drop out of the product answer, and ``sql_companions`` carries
      exactly one CompanionQuery that counts those excluded rows (the same
      base filters AND-composed with ``line_item_type != 'product'`` as a
      ``COUNT(*)``).
    * Every pair of the three exclusion rules, and all three together --
      each fired rule independently follows the shared-predicate pattern
      above, and the rules compose by AND: the main query keeps every base
      filter and AND-composes every fired rule's negation (rules 2+3:
      ``unit_price != 0 AND customer_id IS NOT NULL``; adding rule 4 appends
      ``AND line_item_type = 'product'``), and ``sql_companions`` carries one
      CompanionQuery per fired rule.
    * ``NET_VS_GROSS`` (rule 1) alone -- structurally different from rules
      2-4: it produces no companion query at all (its disclosure is
      direct-filter-based, sourced from ``query_intent``'s resolved variant,
      not from a CompanionQuery), and it does not unconditionally add an
      AND-composed condition. Instead it selects which of its three named
      filter variants applies to the main query's WHERE clause, from
      ``intent.net_gross``: ``net`` adds nothing at all (the default, when no
      explicit variant was requested), ``gross_of_cancellations``
      AND-composes ``invoice_id NOT LIKE 'C%'``, and ``returns``
      AND-composes ``quantity < 0``. Exactly one variant applies per query --
      the three variants are mutually exclusive and never compose with each
      other. ``sql_companions`` stays ``{}``.
    * ``NET_VS_GROSS`` plus any exclusion rule(s) -- the chosen variant
      condition composes by AND on the same main query alongside every fired
      exclusion rule's negation, exactly as two exclusion rules compose with
      each other (e.g. gross-of-cancellations revenue excluding zero-price
      rows becomes ``... AND invoice_id NOT LIKE 'C%' AND unit_price <> 0``),
      while ``sql_companions`` gains entries only from the fired exclusion
      rules -- never from this rule.
    * Grouped queries -- whenever ``query_intent.group_by`` is non-empty,
      regardless of the rule set -- additionally compile ``state.sql_total``:
      the scalar (ungrouped) top-level aggregate over the *identical* base
      filter and rule conditions the main query applies (each condition
      deep-copied, never referenced, before its second attachment to the new
      tree, exactly like the companion queries), projecting only the same
      aggregate expression the main query's SELECT carries, with the group-by
      columns and the GROUP BY clause dropped (compile_sql never emits
      LIMIT/OFFSET/ORDER BY, so there is nothing further to strip).
      ``sql_companions`` is untouched by the total: it serves no disclosure
      rule, so those keys stay exactly the fired rules. Scalar queries set
      ``state.sql_total`` to ``None`` -- their single result row already *is*
      the top-level aggregate.

    The exclusion-rule cases (rules 2-4) follow the same anti-drift structure:
    the base filter conditions are compiled once and deep-copied into each
    query, and each rule's two clauses (the companion predicate and the
    main-query negation) derive from one shared predicate object -- authored a
    single time -- that is copied before being reused (companion) or negated
    (main). No code path authors either clause independently, so the queries
    cannot drift apart. The grouped-query total extends the same guarantee:
    its WHERE conditions are deep copies of the exact conditions the main
    query carries, and its projection reuses the main query's own aggregate
    expression, so the total and the main query cannot drift apart either.

    Every compile starts with four early-validation gates (see
    ``_validate_intent``): an aggregation that does not apply to the metric,
    a group-by field missing from the live schema or not a grouping dimension,
    a reversed date-range filter or a filter value with no matching rows, and
    an ``applicable_rules``/``query_intent`` cross-consistency contradiction
    (a non-default ``net_gross`` variant while rule 1 is absent) all set
    ``state.error`` to an ``invalid_intent:...`` reason and return without
    building any SQL, routing to the graph's error path. A rule that is
    genuinely not implemented yet still fails loudly (``NotImplementedError``)
    rather than being silently dropped. If ``query_intent`` is None the node is
    inert and returns ``state`` unchanged -- ``extract_query_intent`` is still
    a stub, and the rest of the skeleton graph must keep running until it
    lands. The SQL is built with the sqlglot expression API -- never string
    concatenation -- so downstream AST-based guardrails see real structure.
    """

    intent = state.query_intent
    if intent is None:
        # No intent produced yet (Node 2 is a stub): stay inert like the other
        # stubs so the skeleton graph still runs end to end. Once intent
        # extraction exists, a missing intent here is a pipeline bug.
        return state

    # Early-validation gates. Before any SQL construction or rule dispatch is
    # attempted, the four intent checks run (aggregation/metric compatibility,
    # group-by validity against the live schema, filter sanity, and
    # rule/intent cross-consistency). A failed gate routes to the error path
    # exactly like every other failing node in this graph: set state.error to
    # the machine-readable reason and return without compiling any SQL --
    # sql_main and sql_total stay None and sql_companions stays empty so
    # nothing downstream can mistake an unvalidated or partial plan for a real
    # one. The graph edge wiring (graph/build.py) routes on ``state.error is
    # not None``.
    reason = _validate_intent(intent, state.applicable_rules)
    if reason is not None:
        state.error = reason
        state.sql_main = None
        state.sql_total = None
        state.sql_companions = {}
        return state

    # Rule dispatch. Implemented so far: the base case (no rules) and every
    # combination of all four rules -- the three exclusion rules
    # AVG_EXCLUDE_ZERO_PRICE (rule 2), CUSTOMER_EXCLUDE_NULL (rule 3), and
    # PRODUCT_EXCLUDE_NONPRODUCT (rule 4) plus NET_VS_GROSS (rule 1). A rule
    # that is genuinely not implemented yet still fails loudly rather than
    # being silently dropped.
    implemented_rules = {
        RuleName.NET_VS_GROSS,
        RuleName.AVG_EXCLUDE_ZERO_PRICE,
        RuleName.CUSTOMER_EXCLUDE_NULL,
        RuleName.PRODUCT_EXCLUDE_NONPRODUCT,
    }
    remaining_rules = set(state.applicable_rules) - implemented_rules
    if remaining_rules:
        names = ", ".join(rule.value for rule in state.applicable_rules)
        raise NotImplementedError(
            "compile_sql implements the base case (no applicable rules) and "
            "every combination of NET_VS_GROSS, AVG_EXCLUDE_ZERO_PRICE, "
            "CUSTOMER_EXCLUDE_NULL, and PRODUCT_EXCLUDE_NONPRODUCT; this "
            f"rule set is not implemented yet (got: {names})."
        )

    # Compile the user's canonical base filters exactly once; every SQL query
    # below receives its own deep copy of these conditions so the main and all
    # companion WHERE clauses share one structural source and cannot drift
    # apart (DESIGN_LOG.md section 4.6 rule 6).
    base_conditions = _filter_conditions(intent.filters)

    # Each implemented rule contributes one shared exclusion predicate,
    # authored a single time per compile call:
    #   - that rule's companion appends a verbatim copy, so it counts exactly
    #     the rows the predicate selects (unit_price = 0 / customer_id IS NULL
    #     / line_item_type != 'product')
    #   - the main query appends a negated copy, so it keeps the rows the
    #     predicate rejects (unit_price != 0 / customer_id IS NOT NULL /
    #     line_item_type = 'product')
    # The negation is specific to each predicate's kind (equality -> NEQ,
    # IS NULL -> IS NOT NULL, inequality -> EQ), so each entry pairs the shared
    # predicate with the matching negation helper. Rule 4 is the mirror image
    # of rule 2: its shared predicate is already an inequality (the excluded
    # non-product rows), so negating it must collapse back to the equality
    # that keeps products. When multiple rules fire they compose by AND: every
    # fired rule appends its own negation onto the same main conditions
    # (Select.where ANDs, it never replaces existing filters) and its own
    # CompanionQuery into the same dict.
    exclusion_specs = (
        (
            RuleName.AVG_EXCLUDE_ZERO_PRICE,
            exp.column("unit_price").eq(0),
            _negate_equality,
        ),
        (
            RuleName.CUSTOMER_EXCLUDE_NULL,
            exp.column("customer_id").is_(exp.Null()),
            _negate_is_null,
        ),
        (
            RuleName.PRODUCT_EXCLUDE_NONPRODUCT,
            exp.column("line_item_type").neq("product"),
            _negate_inequality,
        ),
    )

    main_conditions = [condition.copy() for condition in base_conditions]

    # Rule NET_VS_GROSS (rule 1) is structurally different from the exclusion
    # rules (2-4): it produces no companion query and does not AND-compose a
    # condition unconditionally. When it fires it instead selects which of its
    # three named filter variants -- net (default), gross-of-cancellations
    # (invoice_id NOT LIKE 'C%'), or returns (quantity < 0) -- the main query
    # applies to its amounts, from ``intent.net_gross``. Exactly one variant
    # applies per query (the three are mutually exclusive, never composable
    # with each other), and the chosen condition AND-composes onto the same
    # main-query conditions the exclusion rules below also append to.
    if RuleName.NET_VS_GROSS in state.applicable_rules:
        variant_builder = _NET_GROSS_VARIANT_CONDITIONS.get(intent.net_gross)
        if variant_builder is not None:
            main_conditions.append(variant_builder())

    companions: dict[RuleName, CompanionQuery] = {}
    for rule, shared_predicate, negate in exclusion_specs:
        if rule not in state.applicable_rules:
            continue
        main_conditions.append(negate(shared_predicate))
        companion_conditions = [
            condition.copy() for condition in base_conditions
        ]
        companion_conditions.append(shared_predicate.copy())
        companions[rule] = CompanionQuery(
            rule=rule,
            sql=_count_excluded_select(companion_conditions).sql(
                dialect="sqlite"
            ),
        )

    state.sql_main = _aggregate_select(intent, main_conditions).sql(
        dialect="sqlite"
    )

    # Grouped-query total. When the main query is grouped, its top-level
    # aggregate is not directly answerable from the rows it returns (each row
    # is one group), so compile the scalar ungrouped aggregate over the *same*
    # conditions the main query carries. Each condition is copied, never
    # referenced, before its second attachment to this new tree -- the same
    # anti-drift discipline the companion queries above use -- and the
    # projection is the main query's own aggregate expression with the
    # group-by columns and the GROUP BY clause dropped. Scalar queries need no
    # separate total: their single result row already is the top-level
    # aggregate, so state.sql_total stays None for them.
    if intent.group_by:
        total_conditions = [condition.copy() for condition in main_conditions]
        state.sql_total = _scalar_aggregate_select(
            intent, total_conditions
        ).sql(dialect="sqlite")
    else:
        state.sql_total = None

    state.sql_companions = companions
    return state
