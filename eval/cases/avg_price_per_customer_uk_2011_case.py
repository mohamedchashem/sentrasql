"""Live eval case: average unit price paid per customer in the UK during 2011.

The genuine two-rule composition case: a real model-generated intent (an AVG
over ``unit_price`` grouped by ``customer_id``, scoped to United Kingdom 2011)
fires BOTH AVG_EXCLUDE_ZERO_PRICE (rule 2 -- a price average is distorted by
zero-price rows) and CUSTOMER_EXCLUDE_NULL (rule 3 -- a customer-grouped query
must not surface a "no customer" bucket) on one query. The two exclusions
AND-compose onto the same main WHERE clause and each rule contributes its own
companion ``COUNT(*)`` with its own independently verified count. Proving these
two rules fire *together* through genuine intent -- not through deterministic
compiler logic already covered by unit tests -- is the primary thing this case
checks.

Verified against the live database before being pinned (the database, not a
planning estimate, is the source of truth; both counts are scoped to UK 2011
and are deliberately DIFFERENT numbers):

* ``SELECT COUNT(*) FROM transactions WHERE country = 'United Kingdom' AND
  invoice_timestamp >= '2011-01-01T00:00:00' AND invoice_timestamp <=
  '2011-12-31T23:59:59' AND unit_price = 0`` returns **2226** -- the
  AVG_EXCLUDE_ZERO_PRICE companion's ``excluded_count``. (The unscoped global
  zero-price count from the earlier avg_unit_price case is 6199 -- a different
  number, verified separately here.)
* ``SELECT COUNT(*) ... AND customer_id IS NULL`` (same UK-2011 window)
  returns **118011** -- the CUSTOMER_EXCLUDE_NULL companion's
  ``excluded_count``.
* ``SELECT COUNT(DISTINCT customer_id) ... AND customer_id IS NOT NULL AND
  unit_price <> 0`` returns **3834** distinct customers, above the guardrail's
  500-row ceiling, so the grouped main is truncated exactly as in the other
  grouped cases.
* ``SELECT AVG(unit_price) ... AND unit_price <> 0 AND customer_id IS NOT
  NULL`` (same UK-2011 window) returns ``3.26290171617964`` -- the wrapper's
  ``total`` (a full-population ungrouped average, unaffected by the 500-row
  clamp on the per-customer breakdown).

Registration appends one ``EvalCase`` to ``eval.cases.CASES`` at import time.
"""

from __future__ import annotations

import math

from db.guardrails import LIMIT_TIER_LLM_CONTEXT_ROWS
from eval.cases import CASES, CheckResult, EvalCase
from eval.checks import scalar_reference

_QUERY = (
    "What is the average unit price paid by each customer in the United "
    "Kingdom in 2011?"
)
_CATEGORY = "grouped"

# Rule 2 fires first (AVG over unit_price), rule 3 second (customer group), in
# detect_applicable_rules' fixed evaluation order -- the two-rule composition.
_EXPECTED_RULES = ["AVG_EXCLUDE_ZERO_PRICE", "CUSTOMER_EXCLUDE_NULL"]

# Row-limit ceiling the guardrail enforces on every grouped main query.
_ROW_LIMIT_CEILING = LIMIT_TIER_LLM_CONTEXT_ROWS

# Live-verified UK-2011-scoped companion counts (each verified independently --
# two different numbers, unlike the unscoped global counts in earlier cases).
_EXPECTED_ZERO_PRICE_UK2011_COUNT = 2226
_EXPECTED_NULL_CUSTOMER_UK2011_COUNT = 118011

# Live-verified wrapper total: AVG(unit_price) over every UK-2011 row that
# survives BOTH exclusions (unit_price <> 0 AND customer_id IS NOT NULL).
_EXPECTED_AVG_UK2011_EXCLUDED = 3.26290171617964

# The UK-2011 filter window exactly as Node 2 resolved it live.
_REFERENCE_RANGE = (
    "invoice_timestamp >= '2011-01-01T00:00:00' "
    "AND invoice_timestamp <= '2011-12-31T23:59:59'"
)
_REFERENCE_UK_FILTER = "country = 'United Kingdom'"

_REFERENCE_ZERO_COUNT_SQL = (
    "SELECT COUNT(*) FROM transactions "
    f"WHERE {_REFERENCE_UK_FILTER} AND {_REFERENCE_RANGE} "
    "AND unit_price = 0"
)
_REFERENCE_NULL_COUNT_SQL = (
    "SELECT COUNT(*) FROM transactions "
    f"WHERE {_REFERENCE_UK_FILTER} AND {_REFERENCE_RANGE} "
    "AND customer_id IS NULL"
)
_REFERENCE_CUSTOMER_COUNT_SQL = (
    "SELECT COUNT(DISTINCT customer_id) FROM transactions "
    f"WHERE {_REFERENCE_UK_FILTER} AND {_REFERENCE_RANGE} "
    "AND customer_id IS NOT NULL AND unit_price <> 0"
)
_REFERENCE_AVG_SQL = (
    "SELECT AVG(unit_price) FROM transactions "
    f"WHERE {_REFERENCE_UK_FILTER} AND {_REFERENCE_RANGE} "
    "AND unit_price <> 0 AND customer_id IS NOT NULL"
)

# The exact disclosure set assemble_disclosures must produce, in its fixed
# output order: the two rule disclosures in fixed exclusion-rule order (rule 2
# AVG_EXCLUDE_ZERO_PRICE first, then rule 3 CUSTOMER_EXCLUDE_NULL -- each with
# its own verified count), then the main-query truncation disclosure. No
# NET_VS_GROSS fires here, so there is no direct-filter disclosure.
_EXPECTED_DISCLOSURES = [
    {
        "source": "rule",
        "label": "AVG_EXCLUDE_ZERO_PRICE",
        "detail": (
            f"{_EXPECTED_ZERO_PRICE_UK2011_COUNT} rows were excluded from "
            "the result set under rule AVG_EXCLUDE_ZERO_PRICE."
        ),
    },
    {
        "source": "rule",
        "label": "CUSTOMER_EXCLUDE_NULL",
        "detail": (
            f"{_EXPECTED_NULL_CUSTOMER_UK2011_COUNT} rows were excluded "
            "from the result set under rule CUSTOMER_EXCLUDE_NULL."
        ),
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
    """Verify the dumped graph state for the UK-2011 per-customer average."""
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

    # PRIMARY assertion: both rules must fire together, through real intent,
    # in detect_applicable_rules' fixed order (rule 2 AVG first, rule 3
    # customer second). This is the whole point of the case.
    actual_rules = dumped_state.get("applicable_rules", [])
    result(
        "both rules fire together (applicable_rules == [AVG_EXCLUDE_ZERO_"
        "PRICE, CUSTOMER_EXCLUDE_NULL])",
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

    # Truncation: the live distinct-customer count (with both exclusions)
    # must exceed the row-limit ceiling so the clamp genuinely binds.
    live_customer_count = scalar_reference(_REFERENCE_CUSTOMER_COUNT_SQL)
    result(
        "live distinct-customer count exceeds the 500-row ceiling",
        live_customer_count > _ROW_LIMIT_CEILING,
        f"reference COUNT(DISTINCT customer_id) over UK 2011 (nonzero price, "
        f"non-NULL customer) returned {live_customer_count} (ceiling: "
        f"{_ROW_LIMIT_CEILING}).",
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

    # Every returned row is one real (non-NULL) customer carrying its average.
    missing_keys = [
        (i, row)
        for i, row in enumerate(rows)
        if not isinstance(row, dict)
        or "customer_id" not in row
        or "unit_price" not in row
    ]
    result(
        "every grouped row carries customer_id and unit_price",
        not missing_keys,
        f"{len(rows) - len(missing_keys)}/{len(rows)} rows carry both "
        "projected columns"
        + (f"; first bad: {missing_keys[0]!r}" if missing_keys else "."),
    )

    companions = dumped_state.get("sql_companions", {})

    # AVG_EXCLUDE_ZERO_PRICE companion: present, succeeded, and its count
    # matches the live UK-2011-scoped zero-price reference (2226) -- NOT the
    # unscoped global 6199 pinned in the earlier avg_unit_price case.
    avg_companion = companions.get("AVG_EXCLUDE_ZERO_PRICE")
    if not isinstance(avg_companion, dict):
        result(
            "AVG_EXCLUDE_ZERO_PRICE companion present and succeeded",
            False,
            f"companion missing from sql_companions "
            f"(keys: {sorted(companions)}).",
        )
    else:
        result(
            "AVG_EXCLUDE_ZERO_PRICE companion present and succeeded",
            avg_companion.get("status") == "success",
            f"companion status: {avg_companion.get('status')!r}.",
        )
        live_zero_count = scalar_reference(_REFERENCE_ZERO_COUNT_SQL)
        actual_excluded = avg_companion.get("excluded_count")
        result(
            "avg companion excluded_count == live UK-2011 zero-price count",
            actual_excluded == live_zero_count,
            f"excluded_count={actual_excluded!r}, reference COUNT(*) of "
            f"zero-price UK-2011 rows returned {live_zero_count} "
            "(the unscoped global zero-price count is a different number).",
        )
        result(
            "avg companion excluded_count == documented 2226",
            actual_excluded == _EXPECTED_ZERO_PRICE_UK2011_COUNT,
            f"excluded_count={actual_excluded!r}, expected "
            f"{_EXPECTED_ZERO_PRICE_UK2011_COUNT} (verified against the live "
            "database).",
        )

    # CUSTOMER_EXCLUDE_NULL companion: present, succeeded, and its count
    # matches the live UK-2011-scoped NULL-customer reference (118011).
    customer_companion = companions.get("CUSTOMER_EXCLUDE_NULL")
    if not isinstance(customer_companion, dict):
        result(
            "CUSTOMER_EXCLUDE_NULL companion present and succeeded",
            False,
            f"companion missing from sql_companions "
            f"(keys: {sorted(companions)}).",
        )
    else:
        result(
            "CUSTOMER_EXCLUDE_NULL companion present and succeeded",
            customer_companion.get("status") == "success",
            f"companion status: {customer_companion.get('status')!r}.",
        )
        live_null_count = scalar_reference(_REFERENCE_NULL_COUNT_SQL)
        actual_excluded = customer_companion.get("excluded_count")
        result(
            "customer companion excluded_count == live UK-2011 "
            "NULL-customer count",
            actual_excluded == live_null_count,
            f"excluded_count={actual_excluded!r}, reference COUNT(*) of "
            f"NULL-customer UK-2011 rows returned {live_null_count}.",
        )
        result(
            "customer companion excluded_count == documented 118011",
            actual_excluded == _EXPECTED_NULL_CUSTOMER_UK2011_COUNT,
            f"excluded_count={actual_excluded!r}, expected "
            f"{_EXPECTED_NULL_CUSTOMER_UK2011_COUNT} (verified against the "
            "live database).",
        )

    # Exactly the two fired exclusion rules may have companions.
    result(
        "sql_companions keys == both fired exclusion rules",
        set(companions)
        == {"AVG_EXCLUDE_ZERO_PRICE", "CUSTOMER_EXCLUDE_NULL"},
        f"actual: {sorted(companions)}",
    )

    # Wrapper total is the full-population ungrouped average (its derived
    # query drops the GROUP BY and the injected row limit), so it must equal
    # the independent reference over every UK-2011 row surviving BOTH
    # exclusions -- never a per-row sum (an average of per-customer averages
    # is not the population average, and the rows are truncated anyway).
    try:
        actual_avg = total["unit_price"]
    except KeyError as exc:
        return checks + [
            CheckResult(
                name="wrapper total matches reference query",
                passed=False,
                detail=f"wrapper total has no 'unit_price' column: {exc} "
                f"(keys: {sorted(total)}).",
            )
        ]
    reference_avg = scalar_reference(_REFERENCE_AVG_SQL)
    result(
        "wrapper total matches reference query",
        math.isclose(actual_avg, reference_avg, rel_tol=1e-9, abs_tol=1e-12),
        f"total={actual_avg!r}, reference SELECT returned "
        f"{reference_avg!r}.",
    )
    result(
        "wrapper total ≈ documented 3.26290171617964",
        math.isclose(
            actual_avg,
            _EXPECTED_AVG_UK2011_EXCLUDED,
            rel_tol=1e-9,
            abs_tol=1e-12,
        ),
        f"actual={actual_avg!r}, expected≈{_EXPECTED_AVG_UK2011_EXCLUDED!r}.",
    )

    # Disclosures must be exactly the two rule disclosures + the main-query
    # truncation disclosure, each sentence embedding its own verified count.
    actual_disclosures = dumped_state.get("disclosures", [])
    result(
        "disclosures == both rule disclosures + main-query truncation",
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
    # verbatim, so both count sentences and the truncation sentence must
    # appear in the final answer.
    final_answer = dumped_state.get("final_answer")
    if isinstance(final_answer, str):
        missing = [
            d["detail"]
            for d in _EXPECTED_DISCLOSURES
            if d["detail"] not in final_answer
        ]
        result(
            "both disclosure count sentences appear in final_answer",
            not missing,
            f"{len(_EXPECTED_DISCLOSURES) - len(missing)}/"
            f"{len(_EXPECTED_DISCLOSURES)} sentences found"
            + (f"; missing: {missing!r}" if missing else "."),
        )
    else:
        result(
            "both disclosure count sentences appear in final_answer",
            False,
            f"final_answer is not a string: {final_answer!r}.",
        )

    return checks


CASES.append(
    EvalCase(
        id="avg_price_per_customer_uk_2011",
        category=_CATEGORY,
        query=_QUERY,
        check=_check,
    )
)
