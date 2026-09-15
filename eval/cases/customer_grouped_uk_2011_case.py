"""Live eval case: per-customer spend in the United Kingdom during 2011.

Exercises two rules firing together on one grouped query -- NET_VS_GROSS
(rule 1, a SUM over the revenue amount metric resolves to the net basis) and
CUSTOMER_EXCLUDE_NULL (rule 3, a customer-grouped query must not surface a
"no customer" bucket). The main query groups by ``customer_id`` across UK 2011
and excludes NULL-customer rows; the CUSTOMER_EXCLUDE_NULL companion counts
exactly those excluded rows under the same UK-2011 base filters, and the
grouped wrapper's top-level total is verified against an independent reference.

Verified against the live database before being pinned (the database, not a
planning estimate, is the source of truth):

* ``SELECT COUNT(*) FROM transactions WHERE country = 'United Kingdom' AND
  invoice_timestamp >= '2011-01-01T00:00:00' AND invoice_timestamp <=
  '2011-12-31T23:59:59' AND customer_id IS NULL`` returns **118011** -- the
  companion's ``excluded_count`` and the count in the disclosure sentence.
* ``SELECT COUNT(DISTINCT customer_id) ... AND customer_id IS NOT NULL``
  (same UK-2011 window) returns **3835** distinct non-NULL customers, far
  above the guardrail's 500-row LLM-context ceiling -- so the grouped main
  result is genuinely cut to 500 rows, ``main_truncated`` is ``True``, and
  the truncation disclosure is present exactly as in the grouped-country case.
* ``SELECT SUM(quantity * unit_price) ... AND customer_id IS NOT NULL``
  (same UK-2011 window) returns ``6284073.654`` -- the wrapper's ``total``.

Because the main result set IS truncated (3835 groups clamped to 500 rows), a
per-row sum of the returned 500 rows can never equal the wrapper total, so this
case deliberately does NOT assert a row-sum-to-total relationship: the wrapper
total is the guardrail-derived ungrouped aggregate over the WHOLE filtered
population (its derived query drops the GROUP BY and the injected LIMIT), and
the only valid total comparison is against that full-population reference.

Registration appends one ``EvalCase`` to ``eval.cases.CASES`` at import time.
"""

from __future__ import annotations

import math

from db.guardrails import LIMIT_TIER_LLM_CONTEXT_ROWS
from eval.cases import CASES, CheckResult, EvalCase
from eval.checks import scalar_reference

_QUERY = "How much did each customer spend in the United Kingdom in 2011?"
_CATEGORY = "grouped"

# Rule 1 fires first (SUM over revenue), rule 3 second (customer group), in
# detect_applicable_rules' fixed evaluation order.
_EXPECTED_RULES = ["NET_VS_GROSS", "CUSTOMER_EXCLUDE_NULL"]

# Row-limit ceiling the guardrail enforces on every grouped main query; a
# customer-grouped UK-2011 result far exceeds it, so rows are clamped to this.
_ROW_LIMIT_CEILING = LIMIT_TIER_LLM_CONTEXT_ROWS

# Live-verified companion count: SELECT COUNT(*) FROM transactions WHERE
# country = 'United Kingdom' AND invoice_timestamp >= '2011-01-01T00:00:00'
# AND invoice_timestamp <= '2011-12-31T23:59:59' AND customer_id IS NULL
# -> 118011.
_EXPECTED_NULL_CUSTOMER_COUNT = 118011

# Live-verified wrapper total over the whole UK-2011 population with NULL
# customers excluded (the main query's CUSTOMER_EXCLUDE_NULL negation):
# SUM(quantity * unit_price) -> 6284073.654.
_EXPECTED_UK_2011_CUSTOMER_REVENUE = 6_284_073.654

# The UK-2011 filter window exactly as Node 2 resolved it live.
_REFERENCE_RANGE = (
    "invoice_timestamp >= '2011-01-01T00:00:00' "
    "AND invoice_timestamp <= '2011-12-31T23:59:59'"
)
_REFERENCE_UK_FILTER = "country = 'United Kingdom'"

_REFERENCE_NULL_COUNT_SQL = (
    "SELECT COUNT(*) FROM transactions "
    f"WHERE {_REFERENCE_UK_FILTER} AND {_REFERENCE_RANGE} "
    "AND customer_id IS NULL"
)
_REFERENCE_CUSTOMER_COUNT_SQL = (
    "SELECT COUNT(DISTINCT customer_id) FROM transactions "
    f"WHERE {_REFERENCE_UK_FILTER} AND {_REFERENCE_RANGE} "
    "AND customer_id IS NOT NULL"
)
_REFERENCE_TOTAL_SQL = (
    "SELECT SUM(quantity * unit_price) FROM transactions "
    f"WHERE {_REFERENCE_UK_FILTER} AND {_REFERENCE_RANGE} "
    "AND customer_id IS NOT NULL"
)

# The exact disclosure set assemble_disclosures must produce, in its fixed
# output order: the CUSTOMER_EXCLUDE_NULL rule disclosure (count embedded),
# the NET_VS_GROSS net-basis direct-filter disclosure, then the main-query
# truncation disclosure (the grouped main was clamped to the row-limit
# ceiling). NET_VS_GROSS never produces a companion, so there is exactly one
# rule disclosure here.
_EXPECTED_DISCLOSURES = [
    {
        "source": "rule",
        "label": "CUSTOMER_EXCLUDE_NULL",
        "detail": (
            f"{_EXPECTED_NULL_CUSTOMER_COUNT} rows were excluded from the "
            "result set under rule CUSTOMER_EXCLUDE_NULL."
        ),
    },
    {
        "source": "direct_filter",
        "label": "net_vs_gross",
        "detail": "The amounts were reported on a net basis.",
    },
    {
        "source": "truncation",
        "label": "main_query",
        "detail": (
            "The main result set was limited by the guardrail's row-limit "
            "enforcement."
        ),
    },
]


def _check(dumped_state: dict) -> list[CheckResult]:
    """Verify the dumped graph state for the UK-2011 per-customer spend query."""
    checks: list[CheckResult] = []

    def result(name: str, passed: bool, detail: str) -> None:
        checks.append(CheckResult(name=name, passed=passed, detail=detail))

    if dumped_state.get("error") is not None:
        return [
            CheckResult(
                name="graph completed without error",
                passed=False,
                detail=f"state.error = {dumped_state['error']!r}",
            )
        ]
    result("graph completed without error", True, "state.error is None.")

    # The primary composition under test: NET_VS_GROSS (rule 1) and
    # CUSTOMER_EXCLUDE_NULL (rule 3) must both fire, in that exact order.
    actual_rules = dumped_state.get("applicable_rules", [])
    result(
        "applicable_rules == [NET_VS_GROSS, CUSTOMER_EXCLUDE_NULL]",
        actual_rules == _EXPECTED_RULES,
        f"actual: {actual_rules}",
    )

    # Grouped wrapper prerequisites.
    main_results = dumped_state.get("main_results")
    wrapper_ok = (
        isinstance(main_results, dict)
        and isinstance(main_results.get("rows"), list)
        and isinstance(main_results.get("total"), dict)
    )
    if not wrapper_ok:
        return checks + [
            CheckResult(
                name="main_results is grouped wrapper",
                passed=False,
                detail=f"actual main_results type/shape: "
                f"{type(main_results).__name__}: {str(main_results)[:200]}",
            )
        ]
    rows = main_results["rows"]
    total = main_results["total"]

    # The live distinct-customer count must exceed the guardrail's row-limit
    # ceiling (verified live, never assumed) so truncation genuinely binds.
    live_customer_count = scalar_reference(_REFERENCE_CUSTOMER_COUNT_SQL)
    result(
        "live distinct-customer count exceeds the 500-row ceiling",
        live_customer_count > _ROW_LIMIT_CEILING,
        f"reference COUNT(DISTINCT customer_id) over UK 2011 returned "
        f"{live_customer_count} (ceiling: {_ROW_LIMIT_CEILING}).",
    )
    result(
        "main_truncated is True",
        dumped_state.get("main_truncated") is True,
        f"actual main_truncated: {dumped_state.get('main_truncated')!r}.",
    )
    result(
        "grouped rows length == row-limit ceiling (500)",
        len(rows) == _ROW_LIMIT_CEILING,
        f"rows={len(rows)}, ceiling={_ROW_LIMIT_CEILING} (live count "
        f"{live_customer_count} > ceiling, so the result was actually cut).",
    )

    # Every returned row is one real (non-NULL) customer carrying its revenue.
    missing_keys = [
        (i, row)
        for i, row in enumerate(rows)
        if not isinstance(row, dict)
        or "customer_id" not in row
        or "revenue" not in row
    ]
    result(
        "every grouped row carries customer_id and revenue",
        not missing_keys,
        f"{len(rows) - len(missing_keys)}/{len(rows)} rows carry both "
        "projected columns"
        + (f"; first bad: {missing_keys[0]!r}" if missing_keys else "."),
    )

    # The CUSTOMER_EXCLUDE_NULL companion must have succeeded.
    companions = dumped_state.get("sql_companions", {})
    companion = companions.get("CUSTOMER_EXCLUDE_NULL")
    if not isinstance(companion, dict):
        result(
            "CUSTOMER_EXCLUDE_NULL companion present and succeeded",
            False,
            f"companion missing from sql_companions "
            f"(keys: {sorted(companions)}).",
        )
    else:
        result(
            "CUSTOMER_EXCLUDE_NULL companion present and succeeded",
            companion.get("status") == "success",
            f"companion status: {companion.get('status')!r}.",
        )

    # Excluded count: equal to the live reference AND the documented 118011.
    if isinstance(companion, dict):
        live_null_count = scalar_reference(_REFERENCE_NULL_COUNT_SQL)
        actual_excluded = companion.get("excluded_count")
        result(
            "companion excluded_count == live NULL-customer count (UK 2011)",
            actual_excluded == live_null_count,
            f"excluded_count={actual_excluded!r}, reference COUNT(*) of "
            f"NULL-customer UK-2011 rows returned {live_null_count}.",
        )
        result(
            "companion excluded_count == documented 118011",
            actual_excluded == _EXPECTED_NULL_CUSTOMER_COUNT,
            f"excluded_count={actual_excluded!r}, expected "
            f"{_EXPECTED_NULL_CUSTOMER_COUNT} (verified against the live "
            "database).",
        )

    # NET_VS_GROSS never produces a companion, so CUSTOMER_EXCLUDE_NULL must
    # be the only sql_companions entry.
    result(
        "sql_companions keys == {CUSTOMER_EXCLUDE_NULL}",
        set(companions) == {"CUSTOMER_EXCLUDE_NULL"},
        f"actual: {sorted(companions)}",
    )

    # Wrapper total is the full-population aggregate (its derived query drops
    # the GROUP BY and the injected row limit), so it must equal the
    # independent reference over every non-NULL-customer UK-2011 row -- NOT a
    # sum of the 500 truncated rows, which would be incomplete.
    try:
        actual_total = total["revenue"]
    except KeyError as exc:
        return checks + [
            CheckResult(
                name="wrapper total matches reference query",
                passed=False,
                detail=f"wrapper total has no 'revenue' column: {exc} "
                f"(keys: {sorted(total)}).",
            )
        ]
    reference_total = scalar_reference(_REFERENCE_TOTAL_SQL)
    result(
        "wrapper total matches reference query",
        math.isclose(actual_total, reference_total, rel_tol=1e-9, abs_tol=1e-6),
        f"total={actual_total!r}, reference SELECT returned "
        f"{reference_total!r} (≈ £{reference_total:,.2f}).",
    )
    result(
        "wrapper total ≈ documented 6,284,073.65",
        math.isclose(
            actual_total,
            _EXPECTED_UK_2011_CUSTOMER_REVENUE,
            rel_tol=1e-9,
            abs_tol=1e-6,
        ),
        f"actual={actual_total!r}, expected≈"
        f"{_EXPECTED_UK_2011_CUSTOMER_REVENUE!r}.",
    )

    # Disclosures must be exactly the rule + net-basis + truncation set.
    actual_disclosures = dumped_state.get("disclosures", [])
    result(
        "disclosures == rule + net-basis + main-query truncation",
        actual_disclosures == _EXPECTED_DISCLOSURES,
        f"actual: {actual_disclosures}",
    )

    result(
        "assumptions == []",
        dumped_state.get("query_intent", {}).get("assumptions", []) == [],
        f"actual: "
        f"{dumped_state.get('query_intent', {}).get('assumptions')}",
    )

    # The deterministic Disclosures section renders every disclosure detail
    # verbatim, so each expected sentence must appear in the final answer.
    final_answer = dumped_state.get("final_answer")
    if isinstance(final_answer, str):
        missing = [
            d["detail"]
            for d in _EXPECTED_DISCLOSURES
            if d["detail"] not in final_answer
        ]
        result(
            "every expected disclosure sentence appears in final_answer",
            not missing,
            f"{len(_EXPECTED_DISCLOSURES) - len(missing)}/"
            f"{len(_EXPECTED_DISCLOSURES)} sentences found"
            + (f"; missing: {missing!r}" if missing else "."),
        )
    else:
        result(
            "every expected disclosure sentence appears in final_answer",
            False,
            f"final_answer is not a string: {final_answer!r}.",
        )

    return checks


CASES.append(
    EvalCase(
        id="customer_grouped_uk_2011",
        category=_CATEGORY,
        query=_QUERY,
        check=_check,
    )
)
