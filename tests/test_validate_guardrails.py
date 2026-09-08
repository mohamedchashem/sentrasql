"""Tests for graph.nodes.validate_guardrails (Node 5).

Implemented contracts so far:

1. Error passthrough. When ``state.error`` is already set -- the graph is
   already routing toward its error path, e.g. one of ``compile_sql``'s four
   early-validation gates fired -- the node returns the state completely
   untouched: no SQL parsing, no ``guardrail_status`` change, no field mutated.
   This is safe because of compile_sql's gate invariant: every
   early-validation/rule-mismatch failure sets ``state.error`` while forcing
   ``state.sql_main`` back to ``None`` and ``state.sql_companions`` to ``{}``,
   so an errored state reaching this node never carries a real plan whose
   validation could be skipped.

2. Main-query guardrail validation. The node fetches the live schema once over
   a read-only connection and runs ``state.sql_main`` through
   ``db.guardrails.validate_sql``. A rejection sets ``state.error`` to the
   guardrail's exact reason string and ``state.guardrail_status`` to
   ``"failed"``, and must leave ``state.sql_companions`` completely untouched.
   A valid main query passes this stage without setting an error.

3. Companion-query guardrail validation. Runs only after the main query has
   passed. Every entry in ``state.sql_companions`` is run through the same
   ``db.guardrails.validate_sql`` gate against the same schema already fetched
   for the main query. The first companion rejection is a hard stop:
   ``state.error`` carries the guardrail's exact reason string verbatim,
   ``guardrail_status`` flips to ``"failed"``, the loop returns immediately (so
   later companions are never validated), and ``sql_companions`` keeps the
   exact mapping and entry objects ``compile_sql`` produced -- identity-checked
   below, including the companions validated before the failure, which must
   receive no enforced-SQL or truncation write-back. When every companion
   passes, this stage must not set an error.

4. Success path, no companions. When the main query passes and
   ``state.sql_companions`` is empty, the node stores ``validate_sql``'s
   enforced (row-limited) SQL back into ``state.sql_main``, propagates that
   result's truncation flag to ``state.main_truncated``, and flips
   ``guardrail_status`` to ``"passed"``. The main query under test has no
   LIMIT clause, so enforcement genuinely injects the ceiling and the stored
   SQL is observably different from the input -- never a no-op.

5. Success path, multi-companion. When the main query and every companion
   pass, each companion's ``sql`` is overwritten with its own enforced version
   and its ``truncated`` field is set from its own ``validate_sql`` result,
   exactly like the main query's fields. The companions are shaped so
   enforcement produces different outcomes per statement (one already carries
   a LIMIT at or below the ceiling and stays untouched, another has no LIMIT
   and gains an injected one), proving the write-back is handled per companion
   rather than by copying one value across the whole plan.
"""

import unittest
from unittest import mock

from graph import nodes as nodes_module
from graph.nodes import validate_guardrails
from graph.state import (
    CompanionQuery,
    CountryFilter,
    Filters,
    GraphState,
    QueryIntent,
    RuleName,
)


class ValidateGuardrailsErrorPassthroughTest(unittest.TestCase):
    """Node 5 error passthrough: an already-errored state returns unchanged."""

    def test_errored_state_is_returned_completely_unchanged(self):
        # A state shaped exactly like compile_sql's rule-mismatch gate failure:
        # a non-default net_gross variant with rule NET_VS_GROSS absent sets
        # state.error while sql_main stays None and sql_companions stays {}.
        error_reason = "invalid_intent:rule_mismatch"
        intent = QueryIntent(
            aggregation="sum",
            metric="revenue",
            filters=Filters(
                country=CountryFilter(present=True, value="United Kingdom")
            ),
            net_gross="gross_of_cancellations",
        )
        state = GraphState(
            raw_query="What is the gross-of-cancellations revenue in the UK?",
            detected_language="en",
            normalized_query="gross of cancellations revenue in United Kingdom",
            query_intent=intent,
            applicable_rules=[],
            sql_main=None,
            sql_companions={},
            guardrail_status="pending",
            main_truncated=False,
            main_results=None,
            error=error_reason,
            disclosures=[],
            final_answer=None,
        )

        result = validate_guardrails(state)

        # The passthrough must leave every field identical -- the node must not
        # clear the error, parse SQL, flip guardrail_status, or drop the intent.
        self.assertEqual(result.error, error_reason)
        self.assertEqual(result.raw_query, state.raw_query)
        self.assertEqual(result.detected_language, "en")
        self.assertEqual(result.normalized_query, state.normalized_query)
        self.assertEqual(result.query_intent, intent)
        self.assertEqual(result.applicable_rules, [])
        self.assertIsNone(result.sql_main)
        self.assertEqual(result.sql_companions, {})
        self.assertEqual(result.guardrail_status, "pending")
        self.assertEqual(result.main_truncated, False)
        self.assertIsNone(result.main_results)
        self.assertEqual(result.disclosures, [])
        self.assertIsNone(result.final_answer)
        self.assertEqual(result.model_dump(), state.model_dump())


class ValidateGuardrailsMainQueryTest(unittest.TestCase):
    """Node 5 main-query guardrail validation against the live schema."""

    def test_valid_main_query_with_no_companions_passes_this_stage(self):
        # No LIMIT clause on purpose: the guardrail must inject one, so the
        # enforced SQL stored back is observably different from the input and
        # main_truncated is genuinely True -- not a no-op enforcement case.
        original_sql = (
            "SELECT SUM(quantity * unit_price) AS revenue FROM transactions"
        )
        enforced_sql = (
            "SELECT SUM(quantity * unit_price) AS revenue "
            "FROM transactions LIMIT 500"
        )
        state = GraphState(
            raw_query="What is total revenue?",
            normalized_query="total revenue",
            query_intent=QueryIntent(aggregation="sum", metric="revenue"),
            sql_main=original_sql,
            sql_companions={},
            guardrail_status="pending",
            main_truncated=False,
            error=None,
        )

        result = validate_guardrails(state)

        # Success path: the whole plan passed, so no error is set and
        # guardrail_status flips to "passed". state.sql_main is overwritten
        # with validate_sql's enforced (row-limited) main SQL -- the input had
        # no LIMIT, so the enforced version genuinely differs from it -- and
        # the result's truncation flag lands on state.main_truncated (True for
        # an injected limit). sql_companions stays empty.
        self.assertIsNone(result.error)
        self.assertEqual(result.guardrail_status, "passed")
        self.assertNotEqual(result.sql_main, original_sql)
        self.assertEqual(result.sql_main, enforced_sql)
        self.assertTrue(result.main_truncated)
        self.assertEqual(result.sql_companions, {})
        expected = GraphState(
            raw_query="What is total revenue?",
            normalized_query="total revenue",
            query_intent=QueryIntent(aggregation="sum", metric="revenue"),
            sql_main=enforced_sql,
            sql_companions={},
            guardrail_status="passed",
            main_truncated=True,
            error=None,
        )
        self.assertEqual(result, expected)

    def test_disallowed_table_sets_exact_reason_and_failed_untouched_companions(
        self,
    ):
        companion = CompanionQuery(
            rule=RuleName.AVG_EXCLUDE_ZERO_PRICE,
            sql=(
                "SELECT COUNT(*) AS excluded_count FROM transactions "
                "WHERE unit_price = 0"
            ),
        )
        state = GraphState(
            raw_query="What is revenue from the orders table?",
            normalized_query="revenue from secret orders",
            query_intent=QueryIntent(aggregation="sum", metric="revenue"),
            sql_main=(
                "SELECT SUM(quantity * unit_price) AS revenue FROM secret_orders"
            ),
            sql_companions={RuleName.AVG_EXCLUDE_ZERO_PRICE: companion},
            guardrail_status="pending",
            error=None,
        )
        # Pydantic may store its own validated copy of the mapping, so capture
        # the exact objects the node actually received before calling it -- the
        # identity asserts below then measure whether the node touched them.
        stored_companions = state.sql_companions
        stored_companion = state.sql_companions[
            RuleName.AVG_EXCLUDE_ZERO_PRICE
        ]

        result = validate_guardrails(state)

        # Exact reason string from db.guardrails.validate_sql, verbatim.
        self.assertEqual(result.error, "disallowed_table:secret_orders")
        self.assertEqual(result.guardrail_status, "failed")
        # The failure path must not touch sql_companions at all -- the exact
        # same mapping object with the exact same entries: no clearing, no
        # mutation, no reassignment to an equal-but-new dict.
        self.assertIs(result.sql_companions, stored_companions)
        self.assertEqual(result.sql_companions, stored_companions)
        self.assertIs(
            result.sql_companions[RuleName.AVG_EXCLUDE_ZERO_PRICE],
            stored_companion,
        )
        self.assertEqual(result.sql_main, state.sql_main)
        self.assertEqual(result.model_dump(), state.model_dump())



class ValidateGuardrailsCompanionQueryTest(unittest.TestCase):
    """Node 5 companion-query guardrail validation (runs after the main query)."""

    def test_all_companions_pass_and_stage_does_not_fail(self):
        # The main query has no LIMIT (the guardrail injects one -> truncated),
        # and the two companions are deliberately shaped so enforcement produces
        # *different* outcomes per statement: the AVG companion already carries
        # LIMIT 100 (at or below the 500-row ceiling -> returned unchanged,
        # truncated False), while the CUSTOMER companion has no LIMIT (one is
        # injected -> enforced SQL rewritten, truncated True). Only a per-
        # companion write-back can produce both outcomes; copying one value
        # across the whole plan would fail these assertions.
        main_sql = (
            "SELECT customer_id, AVG(unit_price) AS avg_unit_price "
            "FROM transactions WHERE unit_price != 0 "
            "AND customer_id IS NOT NULL GROUP BY customer_id"
        )
        enforced_main_sql = (
            "SELECT customer_id, AVG(unit_price) AS avg_unit_price "
            "FROM transactions WHERE unit_price <> 0 "
            "AND NOT customer_id IS NULL GROUP BY customer_id LIMIT 500"
        )
        avg_sql = (
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE unit_price = 0 LIMIT 100"
        )
        customer_sql = (
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE customer_id IS NULL"
        )
        state = GraphState(
            raw_query="What is average unit price by customer?",
            normalized_query="average unit price by customer",
            query_intent=QueryIntent(
                aggregation="avg",
                metric="unit_price",
                group_by=["customer_id"],
            ),
            sql_main=main_sql,
            sql_companions={
                RuleName.AVG_EXCLUDE_ZERO_PRICE: CompanionQuery(
                    rule=RuleName.AVG_EXCLUDE_ZERO_PRICE,
                    sql=avg_sql,
                ),
                RuleName.CUSTOMER_EXCLUDE_NULL: CompanionQuery(
                    rule=RuleName.CUSTOMER_EXCLUDE_NULL,
                    sql=customer_sql,
                ),
            },
            guardrail_status="pending",
            main_truncated=False,
            error=None,
        )
        # Capture the exact entry objects the node received so the asserts below
        # measure the node updating them on the success path.
        stored_companions = state.sql_companions
        stored_avg = state.sql_companions[RuleName.AVG_EXCLUDE_ZERO_PRICE]
        stored_customer = state.sql_companions[
            RuleName.CUSTOMER_EXCLUDE_NULL
        ]

        result = validate_guardrails(state)

        # The whole plan passed: no error, guardrail_status "passed", and the
        # main query's enforced (row-limited) SQL and truncation flag stored
        # back onto the state.
        self.assertIsNone(result.error)
        self.assertEqual(result.guardrail_status, "passed")
        self.assertEqual(result.sql_main, enforced_main_sql)
        self.assertTrue(result.main_truncated)

        # Every companion's sql/truncated is updated independently from its own
        # validate_sql result, in place. The AVG companion's LIMIT 100 is at or
        # below the ceiling, so its sql is left exactly as written and
        # truncated stays False; the CUSTOMER companion had no LIMIT, so
        # LIMIT 500 is injected into its sql and truncated flips True. Neither
        # field is copied from the main query or from the other companion.
        self.assertIs(result.sql_companions, stored_companions)
        self.assertIs(
            result.sql_companions[RuleName.AVG_EXCLUDE_ZERO_PRICE],
            stored_avg,
        )
        self.assertIs(
            result.sql_companions[RuleName.CUSTOMER_EXCLUDE_NULL],
            stored_customer,
        )
        self.assertEqual(stored_avg.sql, avg_sql)
        self.assertFalse(stored_avg.truncated)
        self.assertNotEqual(stored_avg.truncated, stored_customer.truncated)
        self.assertEqual(stored_customer.sql, customer_sql + " LIMIT 500")
        self.assertTrue(stored_customer.truncated)

        expected = GraphState(
            raw_query="What is average unit price by customer?",
            normalized_query="average unit price by customer",
            query_intent=QueryIntent(
                aggregation="avg",
                metric="unit_price",
                group_by=["customer_id"],
            ),
            sql_main=enforced_main_sql,
            sql_companions={
                RuleName.AVG_EXCLUDE_ZERO_PRICE: CompanionQuery(
                    rule=RuleName.AVG_EXCLUDE_ZERO_PRICE,
                    sql=avg_sql,
                    truncated=False,
                ),
                RuleName.CUSTOMER_EXCLUDE_NULL: CompanionQuery(
                    rule=RuleName.CUSTOMER_EXCLUDE_NULL,
                    sql=customer_sql + " LIMIT 500",
                    truncated=True,
                ),
            },
            guardrail_status="passed",
            main_truncated=True,
            error=None,
        )
        self.assertEqual(result, expected)


    def test_first_failing_companion_propagates_reason_and_stops_untouched(
        self,
    ):
        # Iteration order is state.sql_companions' insertion order: the valid
        # customer companion validates first, then the disallowed-table
        # companion fails, and the product companion after it must never be
        # reached.
        valid_companion = CompanionQuery(
            rule=RuleName.CUSTOMER_EXCLUDE_NULL,
            sql=(
                "SELECT COUNT(*) AS excluded_count FROM transactions "
                "WHERE customer_id IS NULL"
            ),
        )
        failing_companion = CompanionQuery(
            rule=RuleName.AVG_EXCLUDE_ZERO_PRICE,
            sql=(
                "SELECT COUNT(*) AS excluded_count FROM secret_orders "
                "WHERE unit_price = 0"
            ),
        )
        later_companion = CompanionQuery(
            rule=RuleName.PRODUCT_EXCLUDE_NONPRODUCT,
            sql=(
                "SELECT COUNT(*) AS excluded_count FROM transactions "
                "WHERE line_item_type != 'product'"
            ),
        )
        state = GraphState(
            raw_query="What is average unit price by customer?",
            normalized_query="average unit price by customer",
            query_intent=QueryIntent(
                aggregation="avg",
                metric="unit_price",
                group_by=["customer_id"],
            ),
            sql_main=(
                "SELECT customer_id, AVG(unit_price) AS avg_unit_price "
                "FROM transactions WHERE unit_price != 0 "
                "AND customer_id IS NOT NULL GROUP BY customer_id"
            ),
            sql_companions={
                RuleName.CUSTOMER_EXCLUDE_NULL: valid_companion,
                RuleName.AVG_EXCLUDE_ZERO_PRICE: failing_companion,
                RuleName.PRODUCT_EXCLUDE_NONPRODUCT: later_companion,
            },
            guardrail_status="pending",
            error=None,
        )
        # Capture the exact objects the node actually received before calling
        # it -- the identity asserts below then measure whether the node
        # touched them.
        stored_companions = state.sql_companions
        stored_valid = state.sql_companions[RuleName.CUSTOMER_EXCLUDE_NULL]
        stored_failing = state.sql_companions[RuleName.AVG_EXCLUDE_ZERO_PRICE]
        stored_later = state.sql_companions[
            RuleName.PRODUCT_EXCLUDE_NONPRODUCT
        ]

        # Wrap graph.nodes.validate_sql with a counting delegate so the
        # early-stop contract is observable: after the main query and the valid
        # companion pass, the failing companion must end the run -- the later
        # companion's SQL must never be handed to validate_sql.
        real_validate_sql = nodes_module.validate_sql
        validated_sqls: list[str] = []

        def counting_validate_sql(sql, schema, *args, **kwargs):
            validated_sqls.append(sql)
            return real_validate_sql(sql, schema, *args, **kwargs)

        with mock.patch.object(
            nodes_module,
            "validate_sql",
            side_effect=counting_validate_sql,
        ):
            result = validate_guardrails(state)

        # Exact reason string from db.guardrails.validate_sql, verbatim.
        self.assertEqual(result.error, "disallowed_table:secret_orders")
        self.assertEqual(result.guardrail_status, "failed")

        # Early stop: the main query and the first two companions were the only
        # statements handed to validate_sql -- the later companion was never
        # validated.
        self.assertEqual(
            validated_sqls,
            [state.sql_main, valid_companion.sql, failing_companion.sql],
        )
        self.assertNotIn(later_companion.sql, validated_sqls)

        # No write-back on the failure path: sql_companions is the very same
        # mapping object with the very same entry objects -- including the
        # companion validated before the failure, which receives no enforced
        # SQL and no truncation flag -- and sql_main is unmodified too (the
        # enforced main SQL is only stored on the success path, after every
        # statement has passed).
        self.assertIs(result.sql_companions, stored_companions)
        self.assertEqual(result.sql_companions, stored_companions)
        self.assertIs(
            result.sql_companions[RuleName.CUSTOMER_EXCLUDE_NULL],
            stored_valid,
        )
        self.assertIs(
            result.sql_companions[RuleName.AVG_EXCLUDE_ZERO_PRICE],
            stored_failing,
        )
        self.assertIs(
            result.sql_companions[RuleName.PRODUCT_EXCLUDE_NONPRODUCT],
            stored_later,
        )
        self.assertEqual(result.sql_main, state.sql_main)
        self.assertEqual(result.main_truncated, False)
        # The failure path may change exactly two fields and nothing else.
        expected = state.model_dump()
        expected["error"] = "disallowed_table:secret_orders"
        expected["guardrail_status"] = "failed"
        self.assertEqual(result.model_dump(), expected)


if __name__ == "__main__":
    unittest.main()
