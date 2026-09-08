"""Typed data models for the analytics-query graph state.

This module defines the Pydantic shapes that flow through the system. It is
deliberately logic-free: no functions, no node code, and no graph-framework
imports -- data shapes only.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class RuleName(str, Enum):
    """Name of a business/analytics rule that may apply to a query.

    Because it subclasses ``str``, each member serializes to its own name
    string, which keeps rule identity stable across JSON boundaries.

    Members:
        NET_VS_GROSS: Query amounts may be net or gross; the rule resolves which.
        AVG_EXCLUDE_ZERO_PRICE: Averages should ignore rows whose price is zero.
        CUSTOMER_EXCLUDE_NULL: Rows with no customer should be excluded from results.
        PRODUCT_EXCLUDE_NONPRODUCT: Non-product stock codes (postage, fees, etc.)
            should be excluded from product-level answers.
    """

    NET_VS_GROSS = "NET_VS_GROSS"
    AVG_EXCLUDE_ZERO_PRICE = "AVG_EXCLUDE_ZERO_PRICE"
    CUSTOMER_EXCLUDE_NULL = "CUSTOMER_EXCLUDE_NULL"
    PRODUCT_EXCLUDE_NONPRODUCT = "PRODUCT_EXCLUDE_NONPRODUCT"


class Assumption(BaseModel):
    """A single assumption recorded while interpreting an ambiguous query.

    Attributes:
        field: Which part of the query was ambiguous (e.g. "metric", "group_by",
            "filters").
        raw_phrase: What the user actually said that needed interpretation.
        resolution: What that phrase was resolved to for execution.
    """

    field: str
    raw_phrase: str
    resolution: str


class DateRangeFilter(BaseModel):
    """Optional start/end boundary pair for a date/time-range filter.

    A date-range filter is two independent, optional boundaries rather than one
    required start-and-end object, so half-open ranges such as "since March"
    (start present, end absent) or "before December" (end present, start
    absent) are representable. Presence is carried per boundary by an explicit
    ``*_present`` bool -- never by a sentinel value: an empty ``start`` string
    does NOT mean "no start". When a ``*_present`` flag is ``False`` the
    paired value field stays at its default empty string and must not be read.

    Attributes:
        start_present: Whether a start boundary was requested. Defaults to
            ``False`` (range open on the start side).
        start: The start boundary as an ISO-8601 string. Meaningful only when
            ``start_present`` is ``True``; otherwise it is the default empty
            string.
        end_present: Whether an end boundary was requested. Defaults to
            ``False`` (range open on the end side).
        end: The end boundary as an ISO-8601 string. Meaningful only when
            ``end_present`` is ``True``; otherwise it is the default empty
            string.
    """

    start_present: bool = False
    start: str = ""
    end_present: bool = False
    end: str = ""


class CountryFilter(BaseModel):
    """Optional equality constraint on the ``country`` dimension.

    A scalar filter uses one explicit ``present`` flag paired with the value,
    instead of the per-boundary start/end split a range filter needs. As with
    every ``*_present`` flag in this schema, ``present`` is the source of truth
    -- an empty ``value`` string does NOT mean "no country filter". When
    ``present`` is ``False`` the value stays at its default empty string and
    must not be read.

    Attributes:
        present: Whether a country constraint was requested. Defaults to
            ``False`` (no country filter).
        value: The exact country name to filter on. Meaningful only when
            ``present`` is ``True``; otherwise it is the default empty string.
    """

    present: bool = False
    value: str = ""


class Filters(BaseModel):
    """Typed filter constraints carried by a query intent.

    This replaces ``QueryIntent.filters``'s former loose-dict shape with
    explicit per-kind sub-objects (DESIGN_LOG.md section 16): each filter kind
    this system supports today is its own typed field, and absence is always
    encoded by that kind's own present flag(s) at their ``False`` default --
    never by ``None``, an omitted key, or a sentinel value such as an empty
    string. Adding a new filter kind is a deliberate schema extension (a new
    typed field), not a free-form dict entry.

    Attributes:
        date_range: Optional date/time-range constraint. Defaults to a fully
            open range (both ``*_present`` flags ``False``), i.e. no date
            filter.
        country: Optional country-equality constraint. Defaults to
            ``present=False``, i.e. no country filter.
    """

    date_range: DateRangeFilter = Field(default_factory=DateRangeFilter)
    country: CountryFilter = Field(default_factory=CountryFilter)


class QueryIntent(BaseModel):
    """Structured, validated interpretation of the user's analytics question.

    Attributes:
        aggregation: Aggregate function to apply. Restricted to one of
            "sum", "avg", "count", "min", "max".
        metric: The canonical business metric being aggregated. Restricted to
            exactly one of "revenue", "quantity", "unit_price", or
            "customer_id" -- these are the canonical metric names every
            downstream node depends on for exact string matching.
        distinct: Whether the aggregation should be applied to distinct values
            only. Defaults to ``False`` (aggregate over every row). For
            example, ``aggregation="count"`` with ``metric="customer_id"`` and
            ``distinct=True`` represents "count of unique customers", as
            opposed to a plain row count.
        group_by: Optional columns to group results by. ``None`` (the default)
            means the query is not grouped.
        filters: Canonical filter constraints to apply, always present as a
            typed ``Filters`` object. Each filter kind is an explicit typed
            field carrying its own ``*_present`` flag(s) (see ``Filters``);
            the default all-``False`` object means no filters. Never a loose
            dict, ``None``, or a sentinel-value encoding.
        net_gross: Which named revenue/quantity filter variant the query asks
            for, resolved by the NET_VS_GROSS rule. Exactly one of
            "net" (the default; sum signed amounts as recorded, no extra
            filter), "gross_of_cancellations" (AND ``invoice_id NOT LIKE
            'C%'``), or "returns" (AND ``quantity < 0``). Only meaningful when
            the NET_VS_GROSS rule fires; defaults to "net".
        output_format: How the answer should be presented. Restricted to
            "chat" or "report"; defaults to "chat".
        assumptions: Assumptions made while parsing the query. The default
            empty list is a normal, expected, and fully valid result: it simply
            means the query needed no assumptions. An empty list here is NOT a
            missing value and should never be treated as an error.
    """

    aggregation: Literal["sum", "avg", "count", "min", "max"]
    metric: Literal["revenue", "quantity", "unit_price", "customer_id"]
    distinct: bool = False
    group_by: list[str] | None = None
    filters: Filters = Field(default_factory=Filters)
    net_gross: Literal["net", "gross_of_cancellations", "returns"] = "net"
    output_format: Literal["chat", "report"] = "chat"
    assumptions: list[Assumption] = Field(default_factory=list)


class CompanionQuery(BaseModel):
    """A supplementary SQL query generated to satisfy one applicable rule.

    Attributes:
        rule: The rule this companion query was generated for.
        sql: The companion SQL statement itself.
        status: Execution lifecycle state. Restricted to "pending", "success",
            or "failed"; defaults to "pending".
        excluded_count: Number of rows the companion query excluded. Trustworthy
            only when ``status`` is "success" -- before the query runs this is
            ``None``, and after a failure it must not be used as a real count.
        truncated: Whether this companion query's row-limit was
            enforced/clamped by the guardrail. Same meaning as
            ``GraphState.main_truncated``, but per companion query. Defaults to
            ``False``.
    """

    rule: RuleName
    sql: str
    status: Literal["pending", "success", "failed"] = "pending"
    excluded_count: int | None = None
    truncated: bool = False


class Disclosure(BaseModel):
    """A statement surfaced to the user about how the answer was produced.

    Attributes:
        source: What generated this disclosure. Restricted to exactly four
            values: "rule" (an applicable rule fired), "assumption" (the query
            required an assumption), "direct_filter" (the user's filters were
            applied directly), or "truncation" (the result set was cut short by
            row-limit enforcement, distinct from an exclusion-rule disclosure).
        label: Short human-readable headline for the disclosure.
        detail: Longer explanation, e.g. the raw phrase and its resolution.
    """

    source: Literal["rule", "assumption", "direct_filter", "truncation"]
    label: str
    detail: str


class TextSegment(BaseModel):
    """Literal-prose segment of an answer template.

    Attributes:
        type: Segment-kind discriminator; always ``"text"``.
        content: Literal prose. After every ``ref`` segment in the template has
            been substituted from the reference dictionary, ``content`` appears
            in the final answer verbatim. Must never contain a digit: every
            computed number is routed through a ``ref`` segment rather than
            hard-coded into prose (enforced later by
            ``graph.segment_validator.validate_segments``).
    """

    type: Literal["text"] = "text"
    content: str


class RefSegment(BaseModel):
    """Substitution-hole segment of an answer template.

    Attributes:
        type: Segment-kind discriminator; always ``"ref"``.
        key: A key into the flat ``dict[str, str]`` reference dictionary built
            by ``graph.reference_dict.build_reference_dict`` (e.g.
            ``result.total``, ``result.rows.0.country``, ``filters.country``,
            ``display.top_n``). Rendering replaces the segment with that key's
            pre-formatted value.
    """

    type: Literal["ref"] = "ref"
    key: str


# One answer-template item: a discriminated union on ``"type"``, so a segment
# is always exactly one of the two shapes above and Pydantic rejects a segment
# that carries the wrong or missing field for its declared kind.
AnswerSegment = Annotated[
    TextSegment | RefSegment, Field(discriminator="type")
]


class AnswerSegments(BaseModel):
    """An answer template: an ordered list of typed ``text``/``ref`` segments.

    This is the structured-output schema ``assemble_answer`` (Node 7) is
    constrained to emit through its bound model
    (``graph.llm.get_answer_model``). Each item is either a ``TextSegment``
    (literal prose) or a ``RefSegment`` (a substitution hole into the reference
    dictionary) -- the exact per-item shape the downstream deterministic gates
    consume: a model-dumped valid instance is a list of
    ``{"type": "text", "content": str}`` and ``{"type": "ref", "key": str}``
    dicts, which is the segment contract
    ``graph.segment_validator.validate_segments`` checks.

    Defined as a single-field object rather than a bare list type because
    DeepSeek's strict structured-output endpoint requires an object-valued
    schema root; the array lives in ``segments``.

    Attributes:
        segments: The ordered answer-template segments.
    """

    segments: list[AnswerSegment]


class GraphState(BaseModel):
    """Full working state passed between graph steps for one user query.

    Attributes:
        raw_query: The user's original question, verbatim.
        detected_language: Language detected for the query; defaults to "en".
        normalized_query: Normalized version of the query (spelling, canonical
            terms); empty string by default when no normalization was needed.
        query_intent: Parsed intent of the query, or ``None`` until parsing
            completes (or if parsing fails).
        intent_extraction_retried: Whether ``extract_query_intent`` (Node 2)
            needed its one retry attempt. Defaults to ``False``; set to
            ``True`` only when a retry actually occurs (DESIGN_LOG.md section
            16), kept visible/inspectable rather than silently smoothed over.
        applicable_rules: Ordered list of rules that should be applied to this
            query. Empty by default.
        sql_main: The primary SQL statement answering the query; ``None`` until
            it has been generated.
        sql_total: The scalar (ungrouped) top-level aggregate SQL for a grouped
            query: the same base filters and rule exclusions as ``sql_main``
            with the group-by columns and GROUP BY clause removed. Compiled
            only when ``query_intent.group_by`` is non-empty; ``None`` for
            scalar queries, whose single result row already is the top-level
            aggregate (DESIGN_LOG.md section 20). Held separately from
            ``sql_companions`` because it serves no disclosure rule: code that
            iterates ``sql_companions`` looking specifically for the four
            rule-keyed entries must never see it.
        sql_companions: Companion SQL queries keyed by the rule they serve.
            Empty by default when no companion queries are needed.
        guardrail_status: Whether the plan passed safety checks. Restricted to
            "pending", "passed", or "failed"; defaults to "pending".
        main_truncated: Whether the main query's row-limit was
            enforced/clamped by the guardrail. Defaults to ``False``.
        main_results: Rows returned by the main query. A scalar (ungrouped)
            query stores a list of one record -- or the empty list when no rows
            matched. A grouped query that produced rows stores a single
            wrapper dict with two keys: ``"rows"`` (the row-level breakdown:
            one record per group) and ``"total"`` (the ungrouped top-level
            aggregate record for the same filters, produced per
            DESIGN_LOG.md section 20). A grouped query that matched no rows
            stores the empty list, like a scalar.
            ``None`` until execution produces the result.
        error: Error message if a step failed; ``None`` when all is well.
        error_reasons: Machine-readable reasons accumulated while processing
            this query, in the order they were produced. Empty by default.
            ``error`` carries the single terminal reason the graph routes on;
            ``error_reasons`` is a list so earlier reasons are not lost when a
            retry or a later failure replaces ``error``. Each entry uses the
            same exact machine-readable ``<prefix>:<detail>`` form nodes use on
            ``error`` (e.g. ``intent_extraction_failed:<reason>``).
        disclosures: Explanations (rule firings, assumptions, direct filters)
            to surface to the user. Empty by default.
        answer_generation_retried: Whether ``assemble_answer`` (Node 7) needed
            its one retry attempt. Defaults to ``False``; set to ``True`` only
            when a retry actually occurs (same convention as
            ``intent_extraction_retried`` for Node 2), kept visible/inspectable
            rather than silently smoothed over.
        final_answer: The final user-facing answer text; ``None`` until the
            answer has been assembled.
    """

    raw_query: str
    detected_language: str = "en"
    normalized_query: str = ""
    query_intent: QueryIntent | None = None
    intent_extraction_retried: bool = False
    applicable_rules: list[RuleName] = Field(default_factory=list)
    sql_main: str | None = None
    sql_total: str | None = None
    sql_companions: dict[RuleName, CompanionQuery] = Field(default_factory=dict)
    guardrail_status: Literal["pending", "passed", "failed"] = "pending"
    main_truncated: bool = False
    main_results: list[dict] | dict | None = None
    error: str | None = None
    error_reasons: list[str] = Field(default_factory=list)
    disclosures: list[Disclosure] = Field(default_factory=list)
    answer_generation_retried: bool = False
    final_answer: str | None = None

