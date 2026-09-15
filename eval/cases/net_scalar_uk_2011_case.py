"""Live eval case: total revenue in the United Kingdom during 2011.

Covers the simplest high-value path end to end: NET_VS_GROSS resolves the
amounts to the net basis, the country and year are direct filters, and the
scalar aggregate must equal an independent read-only reference query over the
real database.

Verified against the live database before being pinned (``data/processed/
sentrasql.db`` is the source of truth, not a planning estimate):

* ``SELECT SUM(quantity * unit_price) FROM transactions WHERE country =
  'United Kingdom' AND invoice_timestamp >= '2011-01-01' AND invoice_timestamp
  <= '2011-12-31T23:59:59'`` returns ``7511063.744`` -- the ≈ 7,511,063.74
  documented expectation, so both the live reference and the pinned constant
  are asserted.

Registration appends one ``EvalCase`` to ``eval.cases.CASES`` at import time.
"""

from __future__ import annotations

import math
from datetime import datetime

from eval.cases import CASES, CheckResult, EvalCase
from eval.checks import format_currency, scalar_metric, scalar_reference

_QUERY = "What was the total revenue in the United Kingdom during 2011?"
_CATEGORY = "basic"

# Verified against the live database: SELECT SUM(quantity * unit_price) FROM
# transactions WHERE country = 'United Kingdom' AND invoice_timestamp >=
# '2011-01-01' AND invoice_timestamp <= '2011-12-31T23:59:59'  -> 7511063.744.
_EXPECTED_UK_2011_REVENUE = 7_511_063.744

_REFERENCE_SQL = (
    "SELECT SUM(quantity * unit_price) FROM transactions "
    "WHERE country = 'United Kingdom' "
    "AND invoice_timestamp >= '2011-01-01' "
    "AND invoice_timestamp <= '2011-12-31T23:59:59'"
)

# The single disclosure the graph must produce for this query: NET_VS_GROSS
# resolved the amounts to the net basis (no exclusion companions fire).
_EXPECTED_DISCLOSURES = [
    {
        "source": "direct_filter",
        "label": "net_vs_gross",
        "detail": "The amounts were reported on a net basis.",
    }
]

# The resolved range must cover all of 2011: start no later than 2011-01-01
# 00:00:00 and end no earlier than 2011-12-31 23:59:59.
_YEAR_START = datetime(2011, 1, 1, 0, 0, 0)
_YEAR_END = datetime(2011, 12, 31, 23, 59, 59)


def _parse_boundary(value: str) -> datetime:
    """Parse an ISO-8601 boundary, tolerating date-only values."""
    return datetime.fromisoformat(value.replace(" ", "T"))


def _check(dumped_state: dict) -> list[CheckResult]:
    """Verify the dumped graph state for the UK 2011 revenue query."""
    checks: list[CheckResult] = []

    def result(name: str, passed: bool, detail: str) -> None:
        checks.append(CheckResult(name=name, passed=passed, detail=detail))

    # No graph error and the shape prerequisites are present.
    if dumped_state.get("error") is not None:
        return [
            CheckResult(
                name="graph completed without error",
                passed=False,
                detail=f"state.error = {dumped_state['error']!r}",
            )
        ]
    result(
        "graph completed without error",
        True,
        "state.error is None.",
    )

    # NET_VS_GROSS must be the only applicable rule (amounts -> net basis).
    actual_rules = dumped_state.get("applicable_rules", [])
    result(
        'applicable_rules == ["NET_VS_GROSS"]',
        actual_rules == ["NET_VS_GROSS"],
        f"actual: {actual_rules}",
    )

    # Direct country filter resolved to the United Kingdom.
    query_intent = dumped_state.get("query_intent") or {}
    filters = query_intent.get("filters", {})
    country = filters.get("country", {})
    country_ok = (
        country.get("present") is True
        and country.get("value") == "United Kingdom"
    )
    result(
        'country filter == "United Kingdom"',
        country_ok,
        f"actual: {country}",
    )

    # The resolved date range must contain the whole of 2011.
    date_range = filters.get("date_range", {})
    try:
        start = (
            _parse_boundary(date_range["start"])
            if date_range.get("start_present")
            else None
        )
        end = (
            _parse_boundary(date_range["end"])
            if date_range.get("end_present")
            else None
        )
    except (KeyError, TypeError, ValueError) as exc:
        result(
            "date range covers all of 2011",
            False,
            f"unparseable date range {date_range!r}: {exc}",
        )
        start = end = None
    if start is not None and end is not None:
        covers_2011 = start <= _YEAR_START and end >= _YEAR_END
        result(
            "date range covers all of 2011",
            covers_2011,
            f"start={date_range.get('start')!r} (must be <= {_YEAR_START}), "
            f"end={date_range.get('end')!r} (must be >= {_YEAR_END})",
        )

    # The scalar revenue must match the independent reference query AND the
    # documented ≈ 7,511,063.74 expectation pinned after a live check.
    try:
        actual_value = scalar_metric(dumped_state, "revenue")
        reference_value = scalar_reference(_REFERENCE_SQL)
    except Exception as exc:
        return checks + [
            CheckResult(
                name="main_results revenue matches reference",
                passed=False,
                detail=f"could not extract/compare value: "
                f"{type(exc).__name__}: {exc}",
            )
        ]
    matches_reference = math.isclose(
        actual_value, reference_value, rel_tol=1e-9, abs_tol=1e-6
    )
    result(
        "main_results revenue matches reference query",
        matches_reference,
        f"main_results={actual_value!r}, reference SELECT returned "
        f"{reference_value!r} (≈ £{reference_value:,.2f}).",
    )
    matches_pin = math.isclose(
        actual_value, _EXPECTED_UK_2011_REVENUE, rel_tol=1e-9, abs_tol=1e-6
    )
    result(
        "main_results revenue ≈ documented 7,511,063.74",
        matches_pin,
        f"actual={actual_value!r}, expected≈{_EXPECTED_UK_2011_REVENUE!r}.",
    )

    # Exactly one disclosure: the net-basis statement, and nothing else.
    actual_disclosures = dumped_state.get("disclosures", [])
    result(
        "disclosures == exactly the net-basis statement",
        actual_disclosures == _EXPECTED_DISCLOSURES,
        f"actual: {actual_disclosures}",
    )

    result(
        "assumptions == []",
        dumped_state.get("query_intent", {}).get("assumptions", []) == [],
        f"actual: "
        f"{dumped_state.get('query_intent', {}).get('assumptions')}",
    )

    result(
        "sql_companions == {}",
        dumped_state.get("sql_companions", {}) == {},
        f"actual: {sorted(dumped_state.get('sql_companions', {}))}",
    )

    # The answer's formatted currency value (from the live reference) must
    # appear verbatim in the final answer.
    final_answer = dumped_state.get("final_answer")
    formatted = format_currency(reference_value)
    if isinstance(final_answer, str):
        in_answer = formatted in final_answer
        snippet = final_answer[:200] + "..." if len(final_answer) > 200 else final_answer
        detail = f"looking for {formatted!r} in final_answer {snippet!r}."
    else:
        in_answer = False
        detail = f"final_answer is not a string: {final_answer!r}."
    result(
        "formatted currency value appears in final_answer",
        in_answer,
        detail,
    )

    return checks


CASES.append(
    EvalCase(
        id="net_scalar_uk_2011",
        category=_CATEGORY,
        query=_QUERY,
        check=_check,
    )
)

