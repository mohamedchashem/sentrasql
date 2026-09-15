"""Live eval case: total revenue by country in 2011 (grouped result).

Covers the grouped-result wrapper path: NET_VS_GROSS resolves the amounts to
the net basis, the main query groups by country across 2011, and the wrapper's
row breakdown plus top-level total must both be independently verified against
the real database. Because the answer's deterministic row table renders each
group on its own ``<country> | £<value>`` line, every formatted row line must
appear verbatim in ``final_answer``.

Verified against the live database before being pinned -- the database, not the
planning estimate, is the source of truth:

* A 2011 revenue group-by produces **37** country rows, NOT 43. 43 is the
  all-time country count; six countries (Bermuda, Korea, Lithuania, Nigeria,
  Thailand, West Indies) have data only outside 2011. The wrapper length and
  the live grouped-count reference both assert 37.
* ``SELECT SUM(quantity * unit_price) FROM transactions WHERE
  invoice_timestamp >= '2011-01-01' AND invoice_timestamp <=
  '2011-12-31T23:59:59'`` returns ``8998808.914`` -- the wrapper's ``total``.

Registration appends one ``EvalCase`` to ``eval.cases.CASES`` at import time.
"""

from __future__ import annotations

import math

from eval.cases import CASES, CheckResult, EvalCase
from eval.checks import format_currency, scalar_reference

_QUERY = "What was the total revenue by country in 2011?"
_CATEGORY = "grouped"

# Live-verified group count for the 2011 revenue breakdown (37 of the
# database's 43 all-time countries have 2011 rows).
_EXPECTED_2011_COUNTRY_COUNT = 37

_REFERENCE_TOTAL_SQL = (
    "SELECT SUM(quantity * unit_price) FROM transactions "
    "WHERE invoice_timestamp >= '2011-01-01' "
    "AND invoice_timestamp <= '2011-12-31T23:59:59'"
)
_REFERENCE_GROUP_COUNT_SQL = (
    "SELECT COUNT(*) FROM (SELECT country FROM transactions "
    "WHERE invoice_timestamp >= '2011-01-01' "
    "AND invoice_timestamp <= '2011-12-31T23:59:59' GROUP BY country)"
)

# Disclosures the graph must produce for this query, in assemble_disclosures'
# fixed order:
# 1. direct_filter net-vs-gross -- NET_VS_GROSS resolved amounts to net basis.
# 2. truncation main_query -- the guardrail's row-limit enforcement
#    deterministically injects its ceiling into every multi-row grouped main
#    query and sets main_truncated, which assemble_disclosures discloses.
# No exclusion rule fires, so there must be no source=="rule" disclosure at all
# (asserted separately below).
_EXPECTED_DISCLOSURES = [
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


def _row_line(row: dict) -> str:
    """Render one grouped data row exactly as the graph's table renderer does.

    Mirrors ``graph/node_assemble_answer.py``'s ``_render_table``: cells are
    the intent's group-by columns followed by the metric, joined by `` | ``,
    using the same pre-formatted currency spelling the reference dictionary
    holds (``graph/reference_dict.py``).
    """
    return f"{row['country']} | {format_currency(row['revenue'])}"


def _check(dumped_state: dict) -> list[CheckResult]:
    """Verify the dumped graph state for the 2011 country-revenue query."""
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
        'applicable_rules == ["NET_VS_GROSS"]',
        actual_rules == ["NET_VS_GROSS"],
        f"actual: {actual_rules}",
    )

    # The grouped wrapper: a dict with a "rows" list and a "total" record.
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

    # Row count: must equal the live grouped-count reference AND the pinned 37.
    live_group_count = scalar_reference(_REFERENCE_GROUP_COUNT_SQL)
    row_count_matches_live = len(rows) == live_group_count
    result(
        "grouped rows length == live 2011 country count",
        row_count_matches_live,
        f"rows={len(rows)}, reference GROUP BY country returned "
        f"{live_group_count} groups.",
    )
    result(
        "grouped rows length == documented 37",
        len(rows) == _EXPECTED_2011_COUNTRY_COUNT,
        f"rows={len(rows)}, expected={_EXPECTED_2011_COUNTRY_COUNT} "
        f"(verified against the live database).",
    )

    # Total wrapper record matches the independent top-level reference.
    try:
        actual_total = total["revenue"]
    except KeyError as exc:
        return checks + [
            CheckResult(
                name="total matches reference",
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

    # Every row value sums to the wrapper total (floating-point tolerance).
    row_values = [row["revenue"] for row in rows]
    result(
        "sum of row values ≈ wrapper total",
        math.isclose(
            sum(row_values), actual_total, rel_tol=1e-9, abs_tol=1e-6
        ),
        f"sum({len(rows)} rows)={sum(row_values)!r}, total={actual_total!r}.",
    )

    # Every formatted row line appears verbatim in the final answer (the
    # deterministic table section renders them exactly this way).
    final_answer = dumped_state.get("final_answer")
    if isinstance(final_answer, str):
        missing = [
            _row_line(row) for row in rows if _row_line(row) not in final_answer
        ]
        result(
            "every formatted row line appears in final_answer",
            not missing,
            f"{len(rows) - len(missing)}/{len(rows)} row lines found"
            + (f"; missing e.g. {missing[:3]!r}" if missing else "."),
        )
    else:
        result(
            "every formatted row line appears in final_answer",
            False,
            f"final_answer is not a string: {final_answer!r}.",
        )

    # Disclosures: net-basis + main-query row-limit enforcement (the graph's
    # deterministic disclosure set for a grouped query) -- and, per the case
    # contract, NO exclusion (source == "rule") disclosures at all.
    actual_disclosures = dumped_state.get("disclosures", [])
    result(
        "disclosures == net-basis + main-query row-limit enforcement",
        actual_disclosures == _EXPECTED_DISCLOSURES,
        f"actual: {actual_disclosures}",
    )
    exclusion_disclosures = [
        d for d in actual_disclosures if d.get("source") == "rule"
    ]
    result(
        "no exclusion disclosures present",
        exclusion_disclosures == [],
        f"exclusion disclosures found: {exclusion_disclosures}.",
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

    return checks


CASES.append(
    EvalCase(
        id="grouped_country_2011",
        category=_CATEGORY,
        query=_QUERY,
        check=_check,
    )
)

