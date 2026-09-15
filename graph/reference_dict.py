"""Deterministic reference-dictionary builder for Node 7's answer assembly.

This module owns one responsibility: turning ``state.main_results`` and
``state.query_intent`` (the two structured inputs the answer-assembly node
will consume) into the flat ``dict[str, str]`` of pre-formatted reference
values that answer templates substitute from. Every formatting decision --
currency vs. plain numbers, thousands separators, decimals, how dates and
countries are rendered, and which fixed display parameters exist -- is made
here, once, in code, so no downstream layer (the later LLM prompt/composition
logic) ever reformats a raw number or decides presentation policy.

Key scheme (what the returned dict contains):

* Scalar (ungrouped) queries: ``result.<metric>`` -- exactly one key holding
  the single aggregate value, e.g. ``result.revenue``. A scalar query whose
  result set is empty produces no ``result.*`` key at all: with no data there
  is no value to substitute, and the validation gate downstream guarantees no
  template can reference a key that was never produced.
* Grouped queries (``query_intent.group_by`` set): ``result.total`` plus one
  per-row key per projected column, addressed by row ordinal:
  ``result.rows.<ordinal>.<column>``, e.g. ``result.rows.0.country`` and
  ``result.rows.0.revenue``.
  Reasoning for the ordinal scheme: a per-row key must stay unambiguous for
  any grouping column's value. Keying by the group value itself (e.g.
  ``result.United Kingdom.revenue``) would collide or need escaping the
  moment a dimension value contains a dot, a space, or a quote (stock codes,
  product descriptions, and timestamps all can), and multi-column GROUP BY
  would require inventing a delimiter between several values. A neutral,
  positional address has no such problem, is regular for template authors
  ("the row at ordinal N"), and works identically whether the row set is
  sorted or not. ``result.total`` is the formatted value of the top-level
  aggregate record ``execute_queries`` computed for the grouped result
  (DESIGN_LOG.md section 20).
* Filter keys, included only when the corresponding ``*_present`` flag is
  true (never guessed from a populated value string): ``filters.country``,
  ``filters.date_range.start``, ``filters.date_range.end``. Dates keep the
  project's existing ISO-8601 format verbatim; countries keep their stored
  spelling verbatim.
* Display keys for fixed display parameters that would otherwise appear as a
  literal digit in answer text. The only such parameter today is the implicit
  top-N default applied when a ranking query names no size (the assumption
  trigger documented in ``graph/assumptions_prompt.py``): when
  ``query_intent.assumptions`` records a ``ranking_size`` assumption, the
  builder emits ``display.top_n`` so templates can write "top {display.top_n}"
  instead of hard-coding a digit. Any future fixed display parameter must be
  added here the same way -- the segment validator has no digit exceptions.

Formatting decisions, made exactly once in this module:

* Revenue-type metrics -- ``revenue`` and ``unit_price`` (money measures) --
  are rendered as GBP currency: ``£`` prefix, thousands separators, two
  decimals (e.g. ``£1,234,567.80``). The dataset is a UK retailer whose price
  column is pounds, so GBP is the single currency convention.
* Everything else (``quantity``, ``customer_id``) is rendered as a plain
  number: integer values with thousands separators and no decimal point
  (e.g. ``1,234``), non-integer values rounded to two decimals with trailing
  zeros trimmed (e.g. ``12.35``, never ``12.350``).
* A formatting request that cannot be honoured (a non-numeric metric value, a
  metric alias missing from a result record, a grouped wrapper without its
  total) is a caller/upstream inconsistency and raises ``ValueError`` -- the
  project's "present but wrong is worse than absent" principle applied to the
  substitution layer.

The module deliberately contains no validation logic (that is the sibling
``graph/segment_validator.py``) and no LLM prompt or invocation logic (a
separate follow-up task).
"""

from __future__ import annotations

from graph.state import QueryIntent

# Metrics whose values are money and are therefore formatted as GBP currency.
# ``unit_price`` is a price and ``revenue`` is a money total; both derive from
# the same pound-valued price column.
_CURRENCY_METRICS = frozenset({"revenue", "unit_price"})

# Fixed display parameter for ranking queries that named no explicit size:
# the implicit top-N default of 5, matching the ranking_size assumption
# trigger in ``graph/assumptions_prompt.py`` and the "default ranking size of
# 5" resolution phrasing used across the codebase. Kept here, once, so the
# answer templates reference display.top_n instead of a hard-coded digit.
_DEFAULT_RANKING_SIZE = 5

# Wrapper keys of ``execute_queries``' grouped main_results shape
# (DESIGN_LOG.md section 20 / graph/state.py's main_results docstring).
_GROUPED_ROWS_KEY = "rows"
_GROUPED_TOTAL_KEY = "total"

# Canonical prefix of a metric result value key produced for scalar queries.
_SCALAR_RESULT_PREFIX = "result."
# Canonical prefix of the grouped top-level aggregate key.
_TOTAL_KEY = "result.total"
# Canonical prefix of a per-row result value key (row ordinal follows).
_ROW_PREFIX = "result.rows."

def build_reference_dict(
    main_results: list[dict] | dict | None,
    query_intent: QueryIntent,
) -> dict[str, str]:
    """Build the flat substitution dictionary for one query's answer.

    Args:
        main_results: ``state.main_results`` -- the executed result of the
            main query in the shape ``execute_queries`` stores (see module
            docstring and ``graph/state.py``).
        query_intent: ``state.query_intent``. Must be a real ``QueryIntent``;
            ``None`` is a caller error because the scalar-vs-grouped shape of
            the dictionary is a property of the intent.

    Returns:
        The flat ``dict[str, str]`` of pre-formatted reference keys (results,
        then filters, then display parameters). Every value is ready for
        substitution into an answer template.

    Raises:
        ValueError: On an inconsistent input -- a scalar result with more than
            one row, a grouped result in the pre-DESIGN_LOG-§20 list shape, a
            grouped wrapper missing its total, a missing metric alias, or a
            non-numeric value passed to a numeric formatter.
    """
    if query_intent is None:
        raise ValueError(
            "build_reference_dict requires a real query_intent; None means "
            "the intent was never extracted, so no substitution dictionary "
            "can be built."
        )

    refs: dict[str, str] = {}
    if query_intent.group_by:
        _add_grouped_result_refs(refs, main_results, query_intent)
    else:
        _add_scalar_result_refs(refs, main_results, query_intent)
    _add_filter_refs(refs, query_intent)
    _add_display_refs(refs, query_intent)
    return refs


def _add_scalar_result_refs(
    refs: dict[str, str],
    main_results: list[dict] | dict | None,
    query_intent: QueryIntent,
) -> None:
    """Populate ``result.<metric>`` for a scalar (ungrouped) query."""
    if not main_results:
        # None (never executed) and [] (executed, no rows matched) both carry
        # no value to substitute. No result key is produced.
        return
    if not isinstance(main_results, list):
        raise ValueError(
            "Scalar main_results must be a list of records, got "
            f"{type(main_results).__name__}: a grouped wrapper under a "
            "non-grouped query_intent is an upstream inconsistency."
        )
    if len(main_results) != 1:
        raise ValueError(
            "Scalar main_results must hold exactly one record "
            f"({query_intent.aggregation} over {query_intent.metric}), got "
            f"{len(main_results)} rows: the intent says ungrouped but the "
            "query returned multiple rows."
        )
    row = main_results[0]
    _require_column(row, query_intent.metric, "scalar")
    refs[f"{_SCALAR_RESULT_PREFIX}{query_intent.metric}"] = _format_metric_value(
        row[query_intent.metric], query_intent.metric
    )


def _add_grouped_result_refs(
    refs: dict[str, str],
    main_results: list[dict] | dict | None,
    query_intent: QueryIntent,
) -> None:
    """Populate ``result.total`` and ``result.rows.<ordinal>.<column>``."""
    if not main_results:
        # Zero-row grouped outcome (execute_queries stores the empty list): no
        # breakdown exists, so no total and no per-row keys are produced.
        return
    if isinstance(main_results, list):
        raise ValueError(
            "Grouped main_results with rows must be the execute_queries "
            f"wrapper dict (\"{_GROUPED_ROWS_KEY}\" / \"{_GROUPED_TOTAL_KEY}\"), "
            "got a bare list: this state predates the DESIGN_LOG.md section "
            "20 grouped-total output shape."
        )
    rows = main_results.get(_GROUPED_ROWS_KEY)
    total = main_results.get(_GROUPED_TOTAL_KEY)
    if not isinstance(rows, list) or not isinstance(total, dict):
        raise ValueError(
            f"Grouped main_results wrapper must carry a \"{_GROUPED_ROWS_KEY}\" "
            f"list and a \"{_GROUPED_TOTAL_KEY}\" record dict, got rows="
            f"{type(rows).__name__}, total={type(total).__name__}."
        )
    if not rows:
        # Empty breakdown: nothing to summarize; consistent with the empty
        # list execute_queries stores for a zero-row grouped query.
        return

    _require_column(total, query_intent.metric, "grouped total")
    refs[_TOTAL_KEY] = _format_metric_value(
        total[query_intent.metric], query_intent.metric
    )

    for ordinal, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(
                "Every grouped row must be a column-name-keyed record dict, "
                f"got {type(row).__name__} at ordinal {ordinal}."
            )
        for column, value in row.items():
            if column == query_intent.metric:
                formatted = _format_metric_value(value, query_intent.metric)
            else:
                formatted = _format_dimension_value(value)
            refs[f"{_ROW_PREFIX}{ordinal}.{column}"] = formatted


def _add_filter_refs(
    refs: dict[str, str], query_intent: QueryIntent
) -> None:
    """Populate the filter keys whose ``*_present`` flag is true."""
    filters = query_intent.filters
    if filters.country.present:
        refs["filters.country"] = _format_dimension_value(filters.country.value)
    if filters.date_range.start_present:
        # Dates keep the project's existing ISO-8601 format exactly as the
        # boundary was resolved; no reformatting is invented here.
        refs["filters.date_range.start"] = filters.date_range.start
    if filters.date_range.end_present:
        refs["filters.date_range.end"] = filters.date_range.end


def _add_display_refs(
    refs: dict[str, str], query_intent: QueryIntent
) -> None:
    """Populate fixed display-parameter keys (none hard-code a digit)."""
    if any(
        assumption.field == "ranking_size"
        for assumption in query_intent.assumptions
    ):
        refs["display.top_n"] = str(_DEFAULT_RANKING_SIZE)


def _require_column(row: dict, column: str, where: str) -> None:
    """Fail loudly when a result record lacks the metric column."""
    if column not in row:
        raise ValueError(
            f"Result record for {where} has no {column!r} column (keys: "
            f"{sorted(row)}): compile_sql aliases the aggregate to the metric "
            "name, so this is an upstream inconsistency."
        )


def _format_metric_value(value: object, metric: str) -> str:
    """Format one metric value using the single formatting decision table."""
    if metric in _CURRENCY_METRICS:
        return _format_currency(value)
    return _format_plain_number(value)


def _format_currency(value: object) -> str:
    """Render a money value as GBP: ``£``, separators, two decimals."""
    number = _coerce_number(value, "currency")
    return f"£{number:,.2f}"


def _format_plain_number(value: object) -> str:
    """Render a non-currency value: integers plain, others to 2 decimals."""
    number = _coerce_number(value, "plain-number")
    if isinstance(number, float) and number.is_integer():
        number = int(number)
    if isinstance(number, int):
        return f"{number:,}"
    # Non-integral (e.g. AVG over quantity): two decimals, trailing zeros
    # trimmed so 5.40 reads as 5.4 but 12.35 stays exact.
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def _coerce_number(value: object, kind: str) -> int | float:
    """Return ``value`` as a number or raise a descriptive ValueError."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"Cannot format {value!r} of type {type(value).__name__} as a "
            f"{kind} value: result records carry raw numeric cells, so a "
            "non-number here is an upstream inconsistency."
        )
    return value


def _format_dimension_value(value: object) -> str:
    """Render a non-metric (grouping/filter) value as plain text."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        # A non-numeric, non-string dimension (none exist today) still needs a
        # substitution-safe rendering rather than a crash.
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)

