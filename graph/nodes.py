"""Core pipeline nodes (2-7 plus 6.5), implemented incrementally.

Most functions are still stubs that return ``state`` unchanged. The real
implementations so far are ``detect_applicable_rules`` (Node 3) -- firing all
four policy rules: ``NET_VS_GROSS`` (rule 1), ``AVG_EXCLUDE_ZERO_PRICE``
(rule 2), ``CUSTOMER_EXCLUDE_NULL`` (rule 3), and
``PRODUCT_EXCLUDE_NONPRODUCT`` (rule 4) -- and ``compile_sql`` (Node 4), for
its base case plus all four policy rules, so Node 3's rule coverage now
matches Node 4's. The base case (``state.applicable_rules`` empty) builds
``state.sql_main`` straight from ``query_intent`` and clears
``state.sql_companions``. Rules ``AVG_EXCLUDE_ZERO_PRICE`` (rule 2),
``CUSTOMER_EXCLUDE_NULL`` (rule 3), and ``PRODUCT_EXCLUDE_NONPRODUCT`` (rule 4)
-- each alone or in any combination -- additionally compile one ``COUNT(*)``
companion query per fired rule and AND-compose that rule's negation
(``unit_price != 0`` / ``customer_id IS NOT NULL`` / ``line_item_type =
'product'``) into the main WHERE clause. Rule ``NET_VS_GROSS`` (rule 1) is
structurally different: it produces no companion query at all, and instead
selects which of its three named filter variants (net / gross-of-cancellations
/ returns) the main query applies -- see ``compile_sql``.

``validate_guardrails`` (Node 5) is also fully implemented: it runs the main
query and every companion through ``db.guardrails.validate_sql`` against the
live schema, routes any rejection to ``state.error`` with
``guardrail_status = "failed"``, and on a full pass stores each statement's
guardrail-enforced (row-limited) SQL and truncation flag back onto the state
(``state.sql_main`` / ``state.main_truncated`` for the main query,
``CompanionQuery.sql`` / ``.truncated`` per companion) with
``guardrail_status = "passed"``.

``extract_query_intent`` (Node 2) is now implemented: it assembles the stable
system prompt (``graph.system_prompt.build_system_prompt``) from the query
text, invokes the strict structured-output model (``graph.llm.get_intent_model``),
runs the result through post-parse normalization/validation
(``graph.intent_normalizer.normalize_and_validate_intent``), and stores the
normalized intent in ``state.query_intent``. Any failure -- a model/parse
exception or a normalization reason -- triggers exactly one retry reusing the
identical call configuration (recorded via ``state.intent_extraction_retried``);
a second failure sets ``state.error`` to
``intent_extraction_failed:<underlying_reason>`` and never sets
``state.query_intent``.

Before any SQL is constructed, ``compile_sql`` runs four early-validation
gates against ``query_intent``: aggregation/metric compatibility, group-by
validity against the live schema, filter sanity (date-range ordering and
filter values with no matching rows), and applicable_rules/query_intent
cross-consistency. An intent that fails a gate sets ``state.error`` to the
matching ``invalid_intent:...`` reason (never a fuzzy correction or a
suggestion) and routes to the graph's error path without building any SQL.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from sqlglot import exp
from langchain_core.messages import SystemMessage

from db.connect import connect_readonly
from db.guardrails import validate_sql
from graph.intent_normalizer import normalize_and_validate_intent
from graph.llm import get_intent_model
from graph.state import (
    CompanionQuery,
    Filters,
    GraphState,
    QueryIntent,
    RuleName,
)
from graph.system_prompt import build_system_prompt


def extract_query_intent(state: GraphState) -> GraphState:
    """Node 2. LLM call.

    Assembles the stable system prompt from the query text, invokes the strict
    structured-output model, runs the parsed intent through post-parse
    normalization/validation, and stores the normalized intent in
    ``state.query_intent``. This node contains no new prompt content and no
    new normalization logic -- it only wires the independently-built pieces
    (``build_system_prompt``, ``get_intent_model``,
    ``normalize_and_validate_intent``) plus the retry/error routing.

    Error passthrough mirrors ``validate_guardrails``: a state that already
    carries ``state.error`` is returned completely untouched (no prompt
    build, no model call).

    Retry policy (DESIGN_LOG.md section 16): any failure -- a model/parse
    exception or a normalization reason -- is retried exactly once with the
    *identical* call configuration (same prompt text, same messages, same
    model object), and the retry is recorded via
    ``state.intent_extraction_retried``. A second failure sets
    ``state.error`` to ``intent_extraction_failed:<underlying_reason>`` and
    never sets ``state.query_intent``.

    The query text used is ``state.normalized_query`` when non-empty, falling
    back to ``state.raw_query``: language normalization is a deferred v1
    feature, so in the current graph the entry state only ever carries
    ``raw_query``.
    """

    # Error passthrough: never touch a state already routing toward the error
    # path -- no prompt build, no model call, no field mutation.
    if state.error is not None:
        return state

    # The v1 graph entry carries only raw_query (language normalization is
    # deferred); once a normalization node exists it populates
    # normalized_query and this preference automatically takes effect.
    query_text = state.normalized_query if state.normalized_query else state.raw_query

    # Build the prompt once and reuse the identical messages for both
    # attempts: build_system_prompt is deterministic, and the retry must reuse
    # the exact same call configuration -- never a fresh/different attempt.
    prompt = build_system_prompt(query_text)
    model = get_intent_model()
    messages = [SystemMessage(content=prompt)]

    def _attempt() -> tuple[QueryIntent | None, str | None]:
        """Run one extraction attempt. Returns (intent, None) or (None, reason).

        ``reason`` is the underlying failure detail in machine-readable form:
        an exception's class name for a model/parse failure, the qualified
        name of an unexpected return type, or the exact
        ``intent_inconsistency:...`` reason from ``normalize_and_validate_intent``.
        """
        try:
            intent = model.invoke(messages)
        except Exception as exc:  # network error, timeout, structural parse failure
            return None, type(exc).__name__
        if not isinstance(intent, QueryIntent):
            return (
                None,
                "unexpected_result_type:"
                f"{type(intent).__module__}.{type(intent).__name__}",
            )
        normalized, reason = normalize_and_validate_intent(intent)
        if reason is not None:
            return None, reason
        return normalized, None

    intent, failure_reason = _attempt()
    if failure_reason is None:
        state.query_intent = intent
        return state

    # First attempt failed -> exactly one retry, identical configuration.
    state.intent_extraction_retried = True
    intent, failure_reason = _attempt()
    if failure_reason is None:
        state.query_intent = intent
        return state

    # Second failure: route to the error path and never set query_intent.
    state.error = f"intent_extraction_failed:{failure_reason}"
    return state


def detect_applicable_rules(state: GraphState) -> GraphState:
    """Node 3. Deterministic, no LLM. Reads state.query_intent and mechanically
    checks it against the four policy rules (net/gross/returns disclosure,
    average-excludes-zero-price, customer-grouping-excludes-null,
    product-ranking-excludes-nonproduct). Populates state.applicable_rules.
    Contains no interpretive judgment -- pure structural checks against
    query_intent's fields.

    Rule scope: all four rules fire. ``NET_VS_GROSS`` (rule 1) fires on a
    SUM over a net/gross amount metric or any non-default ``net_gross``
    variant; ``AVG_EXCLUDE_ZERO_PRICE`` (rule 2) on an AVG over a
    price-derived metric; ``CUSTOMER_EXCLUDE_NULL`` (rule 3) on a group_by
    that includes ``customer_id``; and ``PRODUCT_EXCLUDE_NONPRODUCT`` (rule 4)
    on a group_by that includes ``stock_code`` -- grouping by the product
    dimension, never merely filtering on it (each trigger is spelled out in
    the inline comment above its check below). When ``state.query_intent`` is
    ``None`` -- Node 2 (``extract_query_intent``) is still a stub -- the node
    is inert and returns ``state`` unchanged, mirroring ``compile_sql``'s
    None-intent contract.
    """

    intent = state.query_intent
    if intent is None:
        return state

    # Recomputed from scratch every call: this node is deterministic, so a
    # stale ``applicable_rules`` from an earlier pass must be replaced, never
    # appended to.
    applicable_rules: list[RuleName] = []

    # Rule NET_VS_GROSS (rule 1): revenue/quantity totals default to "net" (sum
    # the signed amounts as recorded), and the rule resolves which of its three
    # named filter variants the query applies. It fires when either:
    #   (a) the query is a SUM over an amount metric that carries the
    #       net/gross distinction -- metric "revenue" or "quantity"; or
    #   (b) query_intent.net_gross explicitly requests a non-default variant
    #       ("gross_of_cancellations" or "returns"), regardless of aggregation
    #       and metric. Condition (b) exists specifically to satisfy
    #       compile_sql's invalid_intent:rule_mismatch gate, which hard-fails
    #       when a non-default net_gross appears without this rule present --
    #       getting this trigger wrong would directly cause that downstream
    #       failure.
    sums_amount_metric = (
        intent.aggregation == "sum"
        and intent.metric in {"revenue", "quantity"}
    )
    if sums_amount_metric or intent.net_gross != "net":
        applicable_rules.append(RuleName.NET_VS_GROSS)

    # Rule AVG_EXCLUDE_ZERO_PRICE (rule 2): an AVG over a price-derived metric
    # is distorted by zero-price rows -- a row priced at 0 contributes 0 to
    # AVG(unit_price) and, since revenue is quantity * unit_price, also to
    # AVG(quantity * unit_price) -- so both price metrics fire the rule that
    # drops those rows and counts them in a companion query. Metric "quantity"
    # is deliberately NOT included (DESIGN_LOG.md section 13): a zero-price row
    # still carries a real, non-zero quantity (e.g. a free sample), so quantity
    # averages are not distorted by zero-price rows and must not fire this rule.
    averages_price_derived_metric = (
        intent.aggregation == "avg"
        and intent.metric in {"unit_price", "revenue"}
    )
    if averages_price_derived_metric:
        applicable_rules.append(RuleName.AVG_EXCLUDE_ZERO_PRICE)

    # Rule CUSTOMER_EXCLUDE_NULL (rule 3): a customer-grouped query must not
    # surface a spurious "no customer" bucket, so rows with a NULL customer_id
    # drop out of the main result and are counted by a companion query (see
    # DESIGN_LOG.md section 4.2, policy rule 3). It fires exactly when the
    # query groups by the customer dimension -- membership anywhere in
    # group_by, regardless of position or how many other columns group beside
    # it -- because that is the only intent shape a NULL-customer bucket can
    # arise in. It is independent of rules 1 and 2, so it composes freely with
    # both.
    if intent.group_by is not None and "customer_id" in intent.group_by:
        applicable_rules.append(RuleName.CUSTOMER_EXCLUDE_NULL)

    # Rule PRODUCT_EXCLUDE_NONPRODUCT (rule 4): a product-ranking query -- one
    # grouped by the product dimension -- aggregates across a mixed population
    # of product and non-product line items, because fee/adjustment/voucher
    # rows (postage, discounts, "Manual", gift vouchers, ...) share the same
    # transactions table as real products and would otherwise surface as their
    # own pseudo-product buckets in the ranked result (DESIGN_LOG.md section
    # 2.3/4.2, policy rule 4). It fires exactly when query_intent.group_by
    # includes the product dimension ``"stock_code"`` -- membership anywhere
    # in group_by, regardless of position or how many other columns group
    # beside it -- mirroring how rule 3 keys on ``"customer_id"`` in the same
    # list. A query that merely *filters* to a specific stock_code value (via
    # query_intent.filters) without grouping by it does NOT fire this rule: a
    # single stock code denotes one homogeneous population -- a given code is
    # either a product or a non-product line item, never both -- so no
    # aggregation across a mixed product/non-product population happens in
    # that case and the ambiguity this rule exists to resolve does not arise.
    # The distinction is deliberate and load-bearing: this rule keys on the
    # GROUP BY dimension, not on the presence of the column anywhere in the
    # intent. The rule is independent of rules 1-3, so it composes freely with
    # all of them.
    if intent.group_by is not None and "stock_code" in intent.group_by:
        applicable_rules.append(RuleName.PRODUCT_EXCLUDE_NONPRODUCT)

    state.applicable_rules = applicable_rules
    return state


# The single fact table every base-case query reads. Rule-specific queries may
# later join other whitelisted tables (e.g. country_timezones), but the plain
# base case never leaves this table.
_MAIN_TABLE = "transactions"

# Path to the read-only live database used by compile_sql's runtime
# introspection gates (group-by columns must exist in the live schema, and a
# scalar filter value must match real rows). Derivation mirrors the rest of
# the repository (e.g. scripts/run_load.py): project root / data / processed /
# sentrasql.db.
_MAIN_DB_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "processed"
    / "sentrasql.db"
)

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
    aggregation_class = _AGGREGATION_CLASSES.get(intent.aggregation)
    if aggregation_class is None:
        raise ValueError(f"Unsupported aggregation {intent.aggregation!r}.")

    select_expressions: list[exp.Expression] = []
    group_by_expressions: list[exp.Column] = []
    if intent.group_by:
        for column_name in intent.group_by:
            select_expressions.append(_column(column_name))
            group_by_expressions.append(exp.column(column_name))

    aggregate = aggregation_class(this=_metric_expression(intent.metric))
    select_expressions.append(exp.alias_(aggregate, intent.metric))

    query = exp.select(*select_expressions).from_(_MAIN_TABLE)
    if conditions:
        query = query.where(*conditions)
    if group_by_expressions:
        query = query.group_by(*group_by_expressions)
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
    """Node 4. Deterministic, no LLM. Builds state.sql_main and state.sql_companions from state.query_intent and state.applicable_rules, using shared filter parameters so main and companion queries cannot structurally drift apart. Rule NET_VS_GROSS produces no companion query, per policy — its disclosure is sourced directly from query_intent's resolved filter, not from a CompanionQuery.

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

    The exclusion-rule cases (rules 2-4) follow the same anti-drift structure:
    the base filter conditions are compiled once and deep-copied into each
    query, and each rule's two clauses (the companion predicate and the
    main-query negation) derive from one shared predicate object -- authored a
    single time -- that is copied before being reused (companion) or negated
    (main). No code path authors either clause independently, so the queries
    cannot drift apart.

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
    # sql_main stays None and sql_companions stays empty so nothing downstream
    # can mistake an unvalidated or partial plan for a real one. The graph
    # edge wiring (graph/build.py) routes on ``state.error is not None``.
    reason = _validate_intent(intent, state.applicable_rules)
    if reason is not None:
        state.error = reason
        state.sql_main = None
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
    state.sql_companions = companions
    return state


def validate_guardrails(state: GraphState) -> GraphState:
    """Node 5. Deterministic, AST-based. Parses state.sql_main and every entry in state.sql_companions before execution. Enforces SELECT-only, whitelisted tables/columns, no destructive keywords, bounded result size. Sets state.guardrail_status to 'passed' or 'failed'. On failure, must set state.error and the graph must route to an error path — never to execution."""

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

    # Companion validation runs only after the main query has passed. Every
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

    # Success path: the main query and every companion all passed the same
    # validate_sql gate, so the guardrail-enforced (row-limited) statements are
    # now the plan to execute. Overwrite state.sql_main with validate_sql's
    # enforced main SQL and propagate its truncation flag to state.main_truncated,
    # then do the same per companion -- each companion receives its own enforced
    # SQL and its own truncation flag from its own validate_sql result, never a
    # value copied across the whole plan.
    state.sql_main = enforced_main_sql
    state.main_truncated = main_truncated
    for companion, enforced_sql, truncated in enforced_companions:
        companion.sql = enforced_sql
        companion.truncated = truncated
    state.guardrail_status = "passed"
    return state


def execute_queries(state: GraphState) -> GraphState:
    """Node 6. Runs state.sql_main and every state.sql_companions entry against the database. Populates state.main_results and updates each CompanionQuery's status and excluded_count. If any companion query required by state.applicable_rules fails, this is a hard stop: state.error must be set and the graph must not proceed to disclosure assembly or answer assembly with partial results."""

    # TODO: implement
    return state


def assemble_disclosures(state: GraphState) -> GraphState:
    """Node 6.5. Deterministic, no LLM. Runs only when state.error is None. Converts all three disclosure sources into a unified list of Disclosure objects in state.disclosures: (1) each entry in state.query_intent.assumptions, (2) each successful CompanionQuery result for rules other than NET_VS_GROSS, (3) a direct-filter-based disclosure for rule NET_VS_GROSS sourced from query_intent's resolved filter, not from a companion query."""

    # TODO: implement
    return state


def assemble_answer(state: GraphState) -> GraphState:
    """Node 7. LLM call, constrained. Takes state.main_results and state.disclosures as structured input and composes state.final_answer. Only responsible for phrasing — every number or exclusion count in the output must originate from state.disclosures or state.main_results, never invented in prose."""

    # TODO: implement
    return state


def handle_error(state: GraphState) -> GraphState:
    """Error path. Deterministic, no LLM. Runs when state.error is set (guardrail validation failure or companion query execution failure). Composes a user-facing message from state.error into state.final_answer, explicitly stating that the system could not produce a reliable answer rather than guessing or returning a partial result. Must never be bypassed when state.error is set."""

    # TODO: implement
    return state
