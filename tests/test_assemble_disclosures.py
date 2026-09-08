"""Tests for graph.node_assemble_disclosures.assemble_disclosures (Node 6.5).

Implemented contracts:

1. Passthrough gate. When ``state.error`` is set, ``guardrail_status`` is not
   ``"passed"``, or ``state.main_results`` is ``None``, the node returns the
   state completely untouched -- no hard-fail check runs and no disclosure is
   built, even when the state would otherwise carry rule/companion
   inconsistencies.

2. Hard-fail on rule/companion inconsistency. Before any disclosure is built,
   every exclusion rule present in ``state.applicable_rules`` (rules 2-4) must
   have a corresponding ``state.sql_companions`` entry with status
   ``"success"``. A missing entry, or an entry that is present but not
   ``"success"``, sets ``state.error`` to
   ``disclosure_assembly_inconsistency:<rule_name>`` and returns immediately --
   ``state.disclosures`` is never partially populated.

3. Deterministic build order. Output always follows: fired exclusion rules in
   fixed rule order (AVG_EXCLUDE_ZERO_PRICE, CUSTOMER_EXCLUDE_NULL,
   PRODUCT_EXCLUDE_NONPRODUCT), the NET_VS_GROSS direct-filter disclosure,
   assumption disclosures in intent order, main-query truncation, then
   companion truncations in sorted rule order -- independent of
   ``applicable_rules`` order and ``sql_companions`` dict insertion order.

4. Zero-count companions still disclose: a companion that succeeded with
   ``excluded_count == 0`` yields a ``"rule"`` disclosure stating that 0 rows
   were excluded; it is never skipped.
"""

import unittest

from graph.node_assemble_disclosures import assemble_disclosures
from graph.state import (
    Assumption,
    CompanionQuery,
    Disclosure,
    GraphState,
    QueryIntent,
    RuleName,
)

_AVG = RuleName.AVG_EXCLUDE_ZERO_PRICE
_CUSTOMER = RuleName.CUSTOMER_EXCLUDE_NULL
_PRODUCT = RuleName.PRODUCT_EXCLUDE_NONPRODUCT
_NET = RuleName.NET_VS_GROSS


def _companion(
    rule: RuleName,
    *,
    status: str = "success",
    excluded_count: int | None = None,
    truncated: bool = False,
) -> CompanionQuery:
    """Build a CompanionQuery carrying the fields assemble_disclosures reads."""
    return CompanionQuery(
        rule=rule,
        sql="SELECT COUNT(*) AS excluded_count FROM transactions",
        status=status,
        excluded_count=excluded_count,
        truncated=truncated,
    )


class AssembleDisclosuresGatePassthroughTest(unittest.TestCase):
    """Node 6.5 gate: an errored/unvalidated/unexecuted state returns unchanged."""

    def _gate_failing_state(
        self,
        *,
        error: str | None = None,
        guardrail_status: str = "passed",
        main_results: list[dict] | None = [{"revenue": 100.0}],
    ) -> GraphState:
        # A state that WOULD build disclosures if it reached the build step
        # (companions all successful, truncation set), so the gate is the only
        # reason nothing is produced.
        return GraphState(
            raw_query="What is total revenue?",
            query_intent=QueryIntent(
                aggregation="sum",
                metric="revenue",
                net_gross="gross_of_cancellations",
            ),
            applicable_rules=[RuleName.NET_VS_GROSS, _AVG],
            sql_main=(
                "SELECT SUM(quantity * unit_price) AS revenue FROM transactions"
            ),
            sql_companions={_AVG: _companion(_AVG, excluded_count=3)},
            guardrail_status=guardrail_status,
            main_truncated=True,
            main_results=main_results,
            error=error,
            disclosures=[
                Disclosure(
                    source="assumption",
                    label="ranking_size",
                    detail="pre-existing disclosure marker",
                )
            ],
        )

    def test_error_set_returns_state_completely_untouched(self):
        state = self._gate_failing_state(error="intent_extraction_failed:SomeError")

        result = assemble_disclosures(state)

        self.assertEqual(result.error, "intent_extraction_failed:SomeError")
        self.assertEqual(result.disclosures, state.disclosures)
        self.assertEqual(result.model_dump(), state.model_dump())

    def test_guardrail_not_passed_returns_state_completely_untouched(self):
        state = self._gate_failing_state(guardrail_status="failed")

        result = assemble_disclosures(state)

        self.assertEqual(result.guardrail_status, "failed")
        self.assertEqual(result.disclosures, state.disclosures)
        self.assertEqual(result.model_dump(), state.model_dump())

    def test_main_results_none_returns_state_completely_untouched(self):
        # main_results is None -- the main query never executed -- even though
        # the plan passed guardrail validation and the companion succeeded.
        state = self._gate_failing_state(main_results=None)

        result = assemble_disclosures(state)

        self.assertIsNone(result.main_results)
        self.assertEqual(result.disclosures, state.disclosures)
        self.assertEqual(result.model_dump(), state.model_dump())


class AssembleDisclosuresHardFailTest(unittest.TestCase):
    """Node 6.5 hard-fail: a fired exclusion rule without a successful companion."""

    def test_missing_companion_entry_sets_inconsistency_error(self):
        # CUSTOMER_EXCLUDE_NULL is applicable but has no companion entry at all.
        # The check runs in fixed rule order, so AVG (present and successful)
        # passes before the missing CUSTOMER companion is caught.
        state = GraphState(
            raw_query=(
                "Average revenue excluding zero prices, by customer and product?"
            ),
            query_intent=QueryIntent(aggregation="avg", metric="revenue"),
            applicable_rules=[_AVG, _CUSTOMER, _PRODUCT],
            sql_main=(
                "SELECT AVG(quantity * unit_price) AS revenue FROM transactions"
            ),
            sql_companions={
                _AVG: _companion(_AVG, excluded_count=2),
                _PRODUCT: _companion(_PRODUCT, excluded_count=5),
            },
            guardrail_status="passed",
            main_results=[{"revenue": 41.0}],
            error=None,
        )

        result = assemble_disclosures(state)

        self.assertEqual(
            result.error,
            "disclosure_assembly_inconsistency:CUSTOMER_EXCLUDE_NULL",
        )
        # No disclosure is built and nothing else is mutated beyond state.error.
        self.assertEqual(result.disclosures, [])
        expected = state.model_dump()
        expected["error"] = (
            "disclosure_assembly_inconsistency:CUSTOMER_EXCLUDE_NULL"
        )
        self.assertEqual(result.model_dump(), expected)

    def test_non_success_companion_entry_sets_inconsistency_error(self):
        # The AVG companion exists but never succeeded (still "pending"), so no
        # trustworthy exclusion count exists to disclose.
        state = GraphState(
            raw_query="What is the average unit price?",
            query_intent=QueryIntent(aggregation="avg", metric="unit_price"),
            applicable_rules=[_AVG],
            sql_main="SELECT AVG(unit_price) AS unit_price FROM transactions",
            sql_companions={_AVG: _companion(_AVG, status="pending")},
            guardrail_status="passed",
            main_results=[{"unit_price": 4.5}],
            error=None,
        )

        result = assemble_disclosures(state)

        self.assertEqual(
            result.error,
            "disclosure_assembly_inconsistency:AVG_EXCLUDE_ZERO_PRICE",
        )
        self.assertEqual(result.disclosures, [])
        expected = state.model_dump()
        expected["error"] = (
            "disclosure_assembly_inconsistency:AVG_EXCLUDE_ZERO_PRICE"
        )
        self.assertEqual(result.model_dump(), expected)


class AssembleDisclosuresBuildTest(unittest.TestCase):
    """Node 6.5 success path: correct disclosure count, order, and content."""

    def test_all_five_disclosure_categories_in_fixed_order(self):
        intent = QueryIntent(
            aggregation="sum",
            metric="revenue",
            net_gross="returns",
            assumptions=[
                Assumption(
                    field="filters.date_range",
                    raw_phrase="last month",
                    resolution=(
                        "resolved 'last month' to 2026-08-01 through 2026-08-31"
                    ),
                ),
                Assumption(
                    field="ranking_size",
                    raw_phrase="top products",
                    resolution="used the default ranking size of 5",
                ),
            ],
        )
        # applicable_rules is deliberately not in rule order, and the
        # sql_companions dict is deliberately not in AVG/CUSTOMER/PRODUCT
        # insertion order, to prove the output ordering is fixed rather than
        # inherited from the state.
        state = GraphState(
            raw_query=(
                "Returns revenue last month by top product, excluding "
                "zero-price rows and null customers?"
            ),
            query_intent=intent,
            applicable_rules=[_NET, _PRODUCT, _AVG, _CUSTOMER],
            sql_main=(
                "SELECT stock_code, SUM(quantity * unit_price) AS revenue "
                "FROM transactions WHERE quantity < 0 AND unit_price != 0 "
                "AND customer_id IS NOT NULL AND line_item_type = 'product' "
                "GROUP BY stock_code"
            ),
            sql_companions={
                _PRODUCT: _companion(
                    _PRODUCT, excluded_count=1, truncated=True
                ),
                _CUSTOMER: _companion(_CUSTOMER, excluded_count=7),
                _AVG: _companion(_AVG, excluded_count=3, truncated=True),
            },
            guardrail_status="passed",
            main_truncated=True,
            main_results=[{"stock_code": "A", "revenue": 12.5}],
            error=None,
        )

        result = assemble_disclosures(state)

        expected = [
            Disclosure(
                source="rule",
                label="AVG_EXCLUDE_ZERO_PRICE",
                detail=(
                    "3 rows were excluded from the result set under rule "
                    "AVG_EXCLUDE_ZERO_PRICE."
                ),
            ),
            Disclosure(
                source="rule",
                label="CUSTOMER_EXCLUDE_NULL",
                detail=(
                    "7 rows were excluded from the result set under rule "
                    "CUSTOMER_EXCLUDE_NULL."
                ),
            ),
            Disclosure(
                source="rule",
                label="PRODUCT_EXCLUDE_NONPRODUCT",
                detail=(
                    "1 row was excluded from the result set under rule "
                    "PRODUCT_EXCLUDE_NONPRODUCT."
                ),
            ),
            Disclosure(
                source="direct_filter",
                label="net_vs_gross",
                detail="The amounts were reported on a returns basis.",
            ),
            Disclosure(
                source="assumption",
                label="filters.date_range",
                detail=(
                    'The phrase "last month" was interpreted as: resolved '
                    "'last month' to 2026-08-01 through 2026-08-31."
                ),
            ),
            Disclosure(
                source="assumption",
                label="ranking_size",
                detail=(
                    'The phrase "top products" was interpreted as: used the '
                    "default ranking size of 5."
                ),
            ),
            Disclosure(
                source="truncation",
                label="main_query",
                detail=(
                    "The main result set was limited by the guardrail's "
                    "row-limit enforcement."
                ),
            ),
            Disclosure(
                source="truncation",
                label="AVG_EXCLUDE_ZERO_PRICE",
                detail=(
                    "The companion result set for rule AVG_EXCLUDE_ZERO_PRICE "
                    "was limited by the guardrail's row-limit enforcement."
                ),
            ),
            Disclosure(
                source="truncation",
                label="PRODUCT_EXCLUDE_NONPRODUCT",
                detail=(
                    "The companion result set for rule PRODUCT_EXCLUDE_NONPRODUCT "
                    "was limited by the guardrail's row-limit enforcement."
                ),
            ),
        ]

        self.assertIsNone(result.error)
        self.assertEqual(result.disclosures, expected)
        self.assertEqual(len(result.disclosures), 9)
        self.assertEqual(
            [disclosure.source for disclosure in result.disclosures],
            [
                "rule",
                "rule",
                "rule",
                "direct_filter",
                "assumption",
                "assumption",
                "truncation",
                "truncation",
                "truncation",
            ],
        )
        self.assertEqual(
            [disclosure.label for disclosure in result.disclosures],
            [
                "AVG_EXCLUDE_ZERO_PRICE",
                "CUSTOMER_EXCLUDE_NULL",
                "PRODUCT_EXCLUDE_NONPRODUCT",
                "net_vs_gross",
                "filters.date_range",
                "ranking_size",
                "main_query",
                "AVG_EXCLUDE_ZERO_PRICE",
                "PRODUCT_EXCLUDE_NONPRODUCT",
            ],
        )

    def test_zero_count_companion_still_produces_a_disclosure(self):
        # The AVG companion succeeded with excluded_count == 0: nothing was
        # excluded, but the mechanism still discloses that fact -- a zero-count
        # companion is never skipped.
        state = GraphState(
            raw_query="Average revenue by customer, excluding zero prices?",
            query_intent=QueryIntent(aggregation="avg", metric="revenue"),
            applicable_rules=[_AVG, _CUSTOMER],
            sql_main=(
                "SELECT AVG(quantity * unit_price) AS revenue FROM transactions "
                "WHERE unit_price != 0 AND customer_id IS NOT NULL"
            ),
            sql_companions={
                _AVG: _companion(_AVG, excluded_count=0),
                _CUSTOMER: _companion(_CUSTOMER, excluded_count=4),
            },
            guardrail_status="passed",
            main_results=[{"revenue": 20.0}],
            error=None,
        )

        result = assemble_disclosures(state)

        expected = [
            Disclosure(
                source="rule",
                label="AVG_EXCLUDE_ZERO_PRICE",
                detail=(
                    "0 rows were excluded from the result set under rule "
                    "AVG_EXCLUDE_ZERO_PRICE."
                ),
            ),
            Disclosure(
                source="rule",
                label="CUSTOMER_EXCLUDE_NULL",
                detail=(
                    "4 rows were excluded from the result set under rule "
                    "CUSTOMER_EXCLUDE_NULL."
                ),
            ),
        ]
        self.assertIsNone(result.error)
        self.assertEqual(result.disclosures, expected)


if __name__ == "__main__":
    unittest.main()



