"""Live eval case: average quantity per order line in 2011.

Deliberately exercises a path where NO policy rule fires: rule 2
(AVG_EXCLUDE_ZERO_PRICE) applies only to averages over the price dimension
(``unit_price``), not over ``quantity``. The compiled plan therefore carries no
applicable rules, no companion queries, and no disclosures, and the final
answer must not render a "Disclosures" section at all.

Verified against the live database before pinning expectations:

* ``SELECT AVG(quantity) FROM transactions WHERE invoice_timestamp >=
  '2011-01-01' AND invoice_timestamp <= '2011-12-31T23:59:59'`` returns
  ``9.679499988987423`` -- the expected main-result value (the 2011 date
  filter is the only constraint; no exclusion rule narrows the window).

Registration appends one ``EvalCase`` to ``eval.cases.CASES`` at import time.
"""

from __future__ import annotations

import math

from eval.cases import CASES, CheckResult, EvalCase
from eval.checks import scalar_metric, scalar_reference

_QUERY = "What was the average quantity per order line in 2011?"
_CATEGORY = "basic"

_REFERENCE_SQL = (
    "SELECT AVG(quantity) FROM transactions "
    "WHERE invoice_timestamp >= '2011-01-01' "
    "AND invoice_timestamp <= '2011-12-31T23:59:59'"
)


def _check(dumped_state: dict) -> list[CheckResult]:
    """Verify the dumped graph state for the 2011 average-quantity query."""
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

    # No rule may fire on a quantity average -- in particular rule 2
    # (AVG_EXCLUDE_ZERO_PRICE) is a unit_price rule and must stay off.
    actual_rules = dumped_state.get("applicable_rules", [])
    result(
        "applicable_rules == []",
        actual_rules == [],
        f"actual: {actual_rules}",
    )

    result(
        "sql_companions == {}",
        dumped_state.get("sql_companions", {}) == {},
        f"actual: {sorted(dumped_state.get('sql_companions', {}))}",
    )

    result(
        "disclosures == []",
        dumped_state.get("disclosures", []) == [],
        f"actual: {dumped_state.get('disclosures')}",
    )

    # Main result matches the independent average-over-2011 reference.
    try:
        actual_value = scalar_metric(dumped_state, "quantity")
        reference_value = scalar_reference(_REFERENCE_SQL)
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
        f"main_results={actual_value!r}, reference AVG(quantity) over 2011 "
        f"returned {reference_value!r}.",
    )

    result(
        "assumptions == []",
        dumped_state.get("query_intent", {}).get("assumptions", []) == [],
        f"actual: "
        f"{dumped_state.get('query_intent', {}).get('assumptions')}",
    )

    # With no disclosures the deterministic renderer emits no "Disclosures"
    # heading, so the final answer must not contain that heading string.
    final_answer = dumped_state.get("final_answer")
    result(
        'no "Disclosures" heading in final_answer',
        isinstance(final_answer, str) and "Disclosures" not in final_answer,
        f"final_answer: {final_answer[:200]!r}..."
        if isinstance(final_answer, str) and len(final_answer) > 200
        else f"final_answer: {final_answer!r}",
    )

    return checks


CASES.append(
    EvalCase(
        id="avg_quantity_2011",
        category=_CATEGORY,
        query=_QUERY,
        check=_check,
    )
)

