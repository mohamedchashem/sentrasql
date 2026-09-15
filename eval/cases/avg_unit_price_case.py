"""Live eval case: average unit price across all order lines.

Covers the AVG_EXCLUDE_ZERO_PRICE exclusion path: the average fires rule 2,
the compiled main query excludes zero-price rows (``unit_price <> 0``), and a
companion ``COUNT(*)`` of the excluded rows must produce a disclosure whose
sentence names the exact verified count.

Verified against the live database before the count is pinned:

* ``SELECT COUNT(*) FROM transactions WHERE unit_price = 0`` returns ``6199``
  -- the companion's ``excluded_count`` and the count embedded in the
  disclosure sentence.
* ``SELECT AVG(unit_price) FROM transactions WHERE unit_price <> 0`` returns
  ``4.676566691953578`` -- the expected main-result value.

Registration appends one ``EvalCase`` to ``eval.cases.CASES`` at import time.
"""

from __future__ import annotations

import math

from eval.cases import CASES, CheckResult, EvalCase
from eval.checks import scalar_metric, scalar_reference

_QUERY = "What is the average unit price across all order lines?"
_CATEGORY = "basic"

# Live-verified companion count: SELECT COUNT(*) FROM transactions WHERE
# unit_price = 0 -> 6199.
_EXPECTED_ZERO_PRICE_COUNT = 6199

_REFERENCE_AVG_SQL = (
    "SELECT AVG(unit_price) FROM transactions WHERE unit_price <> 0"
)
_REFERENCE_ZERO_COUNT_SQL = (
    "SELECT COUNT(*) FROM transactions WHERE unit_price = 0"
)

# The exact disclosure sentence the graph must produce, with the live-verified
# count (mirrors graph/node_assemble_disclosures.py's count sentence grammar).
_EXPECTED_DISCLOSURES = [
    {
        "source": "rule",
        "label": "AVG_EXCLUDE_ZERO_PRICE",
        "detail": (
            f"{_EXPECTED_ZERO_PRICE_COUNT} rows were excluded from the result "
            "set under rule AVG_EXCLUDE_ZERO_PRICE."
        ),
    }
]


def _check(dumped_state: dict) -> list[CheckResult]:
    """Verify the dumped graph state for the average-unit-price query."""
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

    actual_rules = dumped_state.get("applicable_rules", [])
    result(
        'applicable_rules == ["AVG_EXCLUDE_ZERO_PRICE"]',
        actual_rules == ["AVG_EXCLUDE_ZERO_PRICE"],
        f"actual: {actual_rules}",
    )

    # The AVG_EXCLUDE_ZERO_PRICE companion must have succeeded.
    companions = dumped_state.get("sql_companions", {})
    companion = companions.get("AVG_EXCLUDE_ZERO_PRICE")
    if not isinstance(companion, dict):
        result(
            "AVG_EXCLUDE_ZERO_PRICE companion present and succeeded",
            False,
            f"companion missing from sql_companions "
            f"(keys: {sorted(companions)}).",
        )
    else:
        result(
            "AVG_EXCLUDE_ZERO_PRICE companion present and succeeded",
            companion.get("status") == "success",
            f"companion status: {companion.get('status')!r}.",
        )

    # Excluded count: equal to the live reference AND the documented 6199.
    if isinstance(companion, dict):
        live_zero_count = scalar_reference(_REFERENCE_ZERO_COUNT_SQL)
        actual_excluded = companion.get("excluded_count")
        result(
            "companion excluded_count == live zero-price count",
            actual_excluded == live_zero_count,
            f"excluded_count={actual_excluded!r}, SELECT COUNT(*) FROM "
            f"transactions WHERE unit_price = 0 returned {live_zero_count}.",
        )
        result(
            "companion excluded_count == documented 6199",
            actual_excluded == _EXPECTED_ZERO_PRICE_COUNT,
            f"excluded_count={actual_excluded!r}, expected "
            f"{_EXPECTED_ZERO_PRICE_COUNT} (verified against the live "
            "database).",
        )

    # Disclosures: exactly the rule disclosure naming the verified count.
    actual_disclosures = dumped_state.get("disclosures", [])
    result(
        'disclosure == "6199 rows were excluded ..." exactly',
        actual_disclosures == _EXPECTED_DISCLOSURES,
        f"actual: {actual_disclosures}",
    )

    # Main result matches the independent average-over-nonzero-price reference.
    try:
        actual_value = scalar_metric(dumped_state, "unit_price")
        reference_value = scalar_reference(_REFERENCE_AVG_SQL)
    except Exception as exc:
        return checks + [
            CheckResult(
                name="main_results value matches reference",
                passed=False,
                detail=f"could not extract/compare value: "
                f"{type(exc).__name__}: {exc}",
            )
        ]
    result(
        "main_results value matches reference query",
        math.isclose(actual_value, reference_value, rel_tol=1e-9, abs_tol=1e-9),
        f"main_results={actual_value!r}, reference AVG(unit_price) WHERE "
        f"unit_price <> 0 returned {reference_value!r}.",
    )

    result(
        "assumptions == []",
        dumped_state.get("query_intent", {}).get("assumptions", []) == [],
        f"actual: "
        f"{dumped_state.get('query_intent', {}).get('assumptions')}",
    )

    return checks


CASES.append(
    EvalCase(
        id="avg_unit_price",
        category=_CATEGORY,
        query=_QUERY,
        check=_check,
    )
)

