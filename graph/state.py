"""Typed data models for the analytics-query graph state.

This module defines the Pydantic shapes that flow through the system. It is
deliberately logic-free: no functions, no node code, and no graph-framework
imports -- data shapes only.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

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


class QueryIntent(BaseModel):
    """Structured, validated interpretation of the user's analytics question.

    Attributes:
        aggregation: Aggregate function to apply. Restricted to one of
            "sum", "avg", "count", "min", "max".
        metric: The business metric being aggregated (e.g. "quantity", "price").
        group_by: Optional columns to group results by. ``None`` (the default)
            means the query is not grouped.
        filters: Canonical filter constraints to apply, mapping filter/column
            names to their values. Empty by default (no filters).
        output_format: How the answer should be presented. Restricted to
            "chat" or "report"; defaults to "chat".
        assumptions: Assumptions made while parsing the query. The default
            empty list is a normal, expected, and fully valid result: it simply
            means the query needed no assumptions. An empty list here is NOT a
            missing value and should never be treated as an error.
    """

    aggregation: Literal["sum", "avg", "count", "min", "max"]
    metric: str
    group_by: list[str] | None = None
    filters: dict = Field(default_factory=dict)
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
    """

    rule: RuleName
    sql: str
    status: Literal["pending", "success", "failed"] = "pending"
    excluded_count: int | None = None


class Disclosure(BaseModel):
    """A statement surfaced to the user about how the answer was produced.

    Attributes:
        source: What generated this disclosure. Restricted to exactly three
            values: "rule" (an applicable rule fired), "assumption" (the query
            required an assumption), or "direct_filter" (the user's filters
            were applied directly).
        label: Short human-readable headline for the disclosure.
        detail: Longer explanation, e.g. the raw phrase and its resolution.
    """

    source: Literal["rule", "assumption", "direct_filter"]
    label: str
    detail: str


class GraphState(BaseModel):
    """Full working state passed between graph steps for one user query.

    Attributes:
        raw_query: The user's original question, verbatim.
        detected_language: Language detected for the query; defaults to "en".
        normalized_query: Normalized version of the query (spelling, canonical
            terms); empty string by default when no normalization was needed.
        query_intent: Parsed intent of the query, or ``None`` until parsing
            completes (or if parsing fails).
        applicable_rules: Ordered list of rules that should be applied to this
            query. Empty by default.
        sql_main: The primary SQL statement answering the query; ``None`` until
            it has been generated.
        sql_companions: Companion SQL queries keyed by the rule they serve.
            Empty by default when no companion queries are needed.
        guardrail_status: Whether the plan passed safety checks. Restricted to
            "pending", "passed", or "failed"; defaults to "pending".
        main_results: Rows returned by the main query as a list of records;
            ``None`` until execution produces them.
        error: Error message if a step failed; ``None`` when all is well.
        disclosures: Explanations (rule firings, assumptions, direct filters)
            to surface to the user. Empty by default.
        final_answer: The final user-facing answer text; ``None`` until the
            answer has been assembled.
    """

    raw_query: str
    detected_language: str = "en"
    normalized_query: str = ""
    query_intent: QueryIntent | None = None
    applicable_rules: list[RuleName] = Field(default_factory=list)
    sql_main: str | None = None
    sql_companions: dict[RuleName, CompanionQuery] = Field(default_factory=dict)
    guardrail_status: Literal["pending", "passed", "failed"] = "pending"
    main_results: list[dict] | None = None
    error: str | None = None
    disclosures: list[Disclosure] = Field(default_factory=list)
    final_answer: str | None = None

