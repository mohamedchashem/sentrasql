"""Tests for graph.node_validate_guardrails.validate_guardrails (Node 5).

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

   The grouped-query total (``state.sql_total``) is validated under this same
   contract: compile_sql stores it on its own GraphState field -- never inside
   ``sql_companions`` -- so the companion loop above does not see it
   automatically. ``validate_guardrails`` validates it explicitly, right after
   the main query and before the companions, and on the success path stores its
   enforced SQL back onto ``state.sql_total``. Its truncation flag is
   deliberately not stored: a scalar single-row aggregate can never actually be
   cut by row-limit enforcement.

4. Success path, no companions. When the main query passes and
   ``state.sql_companions`` is empty, the node stores ``validate_sql``'s
   enforced SQL back into ``state.sql_main``, propagates that result's
   truncation flag to ``state.main_truncated``, and flips ``guardrail_status``
   to ``"passed"``. The scalar main query under test carries ``compile_sql``'s
   own LIMIT 1 -- already at or below the guardrail's ceiling -- so
   enforcement leaves it completely unchanged and ``main_truncated`` is
   False: a structurally single-row aggregate must never be reported as
   limited.

5. Success path, multi-companion. When the main query and every companion
   pass, each companion's ``sql`` is overwritten with its own enforced version
   and its ``truncated`` field is set from its own ``validate_sql`` result,
   exactly like the main query's fields. The grouped main query is genuinely
   multi-row, so enforcement injects the ceiling into it (``main_truncated``
   True), while the ``COUNT(*)`` companions carry an at-or-below-ceiling bound
   -- ``compile_sql`` emits LIMIT 1 -- and pass through unchanged
   (``truncated`` False) -- proving the write-back is per statement rather
   than a single value copied across the whole plan.
"""

import unittest
from unittest import mock

from graph import node_validate_guardrails as nodes_module
from graph.node_compile_sql import compile_sql
from graph.node_validate_guardrails import validate_guardrails
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
        # The scalar (ungrouped) main query now carries compile_sql's own
        # LIMIT 1 -- structurally guaranteed to return exactly one row, so its
        # row bound is already at or below the guardrail's ceiling. The
        # enforced SQL stored back is therefore identical to the input and
        # main_truncated is genuinely False -- a single-row aggregate must not
        # be reported as limited/truncated.
        original_sql = (
            "SELECT SUM(quantity * unit_price) AS revenue FROM transactions "
            "LIMIT 1"
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
        # with validate_sql's enforced main SQL -- which equals the input,
        # because the LIMIT 1 it already carries is at or below the ceiling --
        # and the result's truncation flag lands on state.main_truncated
        # (False for a bound the guardrail left untouched). sql_companions
        # stays empty.
        self.assertIsNone(result.error)
        self.assertEqual(result.guardrail_status, "passed")
        self.assertEqual(result.sql_main, original_sql)
        self.assertFalse(result.main_truncated)
        self.assertEqual(result.sql_companions, {})
        expected = GraphState(
            raw_query="What is total revenue?",
            normalized_query="total revenue",
            query_intent=QueryIntent(aggregation="sum", metric="revenue"),
            sql_main=original_sql,
            sql_companions={},
            guardrail_status="passed",
            main_truncated=False,
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
        # The grouped main query is genuinely multi-row: it has no LIMIT, so
        # the guardrail injects its ceiling (truncated True). The two COUNT(*)
        # companions are both single-row aggregates that already carry an
        # at-or-below-ceiling bound -- one compile_sql's own LIMIT 1, the other
        # a hand-authored LIMIT 100 -- so row-limit enforcement leaves each
        # exactly as written with truncated False. Only a per-companion
        # write-back can preserve each companion's own sql and its own
        # truncation flag; copying the main query's values (or one companion's
        # values) across the whole plan would fail these assertions.
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
            "WHERE customer_id IS NULL LIMIT 1"
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
        # validate_sql result, in place. Both companions' bounds (LIMIT 100 and
        # compile_sql's LIMIT 1) are at or below the ceiling, so each sql is
        # left exactly as written and each truncated stays False -- unlike the
        # grouped main query's True. Neither field is copied from the main
        # query or from the other companion.
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
        self.assertEqual(stored_customer.sql, customer_sql)
        self.assertFalse(stored_customer.truncated)
        self.assertNotEqual(result.main_truncated, stored_avg.truncated)
        self.assertNotEqual(result.main_truncated, stored_customer.truncated)

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
                    sql=customer_sql,
                    truncated=False,
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

        # Wrap graph.node_validate_guardrails.validate_sql with a counting
        # delegate so the
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


class ValidateGuardrailsGroupedTotalTest(unittest.TestCase):
    """Node 5 grouped-total (``state.sql_total``) validation coverage.

    compile_sql stores the grouped-query total on its own GraphState field --
    not as a ``sql_companions`` entry -- so the existing companion-validation
    loop (which iterates ``state.sql_companions.values()``) does not see it
    automatically. ``validate_guardrails`` must therefore validate
    ``state.sql_total`` explicitly, through the very same
    ``db.guardrails.validate_sql`` gate as ``sql_main`` and the companions,
    before any companion is validated. These tests prove that coverage against
    the live schema the node fetches itself: a valid grouped total passes
    through unchanged (its compile-time LIMIT 1 is at or below the ceiling)
    while the grouped main query is genuinely row-limited; a failing total
    rejects the whole plan (hard stop, nothing written back, companions never
    reached); and a scalar query with no total skips the stage entirely.
    """

    def test_grouped_query_total_is_validated_and_enforced_alongside_main(self):
        # Real compile_sql output for a grouped query: sql_main grouped, plus
        # sql_total -- the scalar ungrouped aggregate over the same conditions.
        state = compile_sql(
            GraphState(
                raw_query="What is total revenue by country?",
                normalized_query="total revenue by country",
                query_intent=QueryIntent(
                    aggregation="sum",
                    metric="revenue",
                    group_by=["country"],
                ),
                applicable_rules=[],
                guardrail_status="pending",
                error=None,
            )
        )
        self.assertIsNone(state.error)
        self.assertIsNotNone(state.sql_total)
        self.assertEqual(state.sql_companions, {})
        # The node mutates state in place, so capture the pre-validation
        # statements before calling it.
        original_total_sql = state.sql_total
        original_main_sql = state.sql_main

        result = validate_guardrails(state)

        # The whole plan -- main AND total -- passed the guardrail. The grouped
        # main is genuinely multi-row, so row-limit enforcement injects the
        # ceiling into it (sql rewritten, main_truncated True). The total is a
        # scalar aggregate that already carries compile_sql's LIMIT 1 -- at or
        # below the ceiling -- so enforcement leaves it exactly as written
        # (its own truncation flag is deliberately not stored, and validate_sql
        # reports False for it). The total is stored back onto state.sql_total
        # (its own field), never into sql_companions.
        self.assertIsNone(result.error)
        self.assertEqual(result.guardrail_status, "passed")
        self.assertEqual(result.sql_total, original_total_sql)
        self.assertEqual(result.sql_main, original_main_sql + " LIMIT 500")
        self.assertTrue(result.main_truncated)
        self.assertEqual(result.sql_companions, {})

    def test_scalar_query_with_no_total_passes_and_keeps_sql_total_none(self):
        state = compile_sql(
            GraphState(
                raw_query="What is total revenue?",
                normalized_query="total revenue",
                query_intent=QueryIntent(
                    aggregation="sum",
                    metric="revenue",
                ),
                applicable_rules=[],
                guardrail_status="pending",
                error=None,
            )
        )
        self.assertIsNone(state.sql_total)
        # The scalar main is a single-row aggregate carrying compile_sql's own
        # LIMIT 1, so validate_guardrails must pass it through unchanged with
        # no truncation reported -- not inject the ceiling.
        self.assertTrue(state.sql_main.endswith("LIMIT 1"))
        original_main_sql = state.sql_main

        result = validate_guardrails(state)
        self.assertIsNone(result.error)
        self.assertEqual(result.guardrail_status, "passed")
        self.assertIsNone(result.sql_total)
        self.assertEqual(result.sql_main, original_main_sql)
        self.assertFalse(result.main_truncated)

    def test_failing_total_rejects_plan_before_companions_and_writes_nothing(
        self,
    ):
        # A grouped main query whose total statement fails validation (a
        # disallowed table) must fail the node with the guardrail's exact
        # reason. The total is validated before any companion, and nothing is
        # written back on the failure path.
        main_sql = (
            "SELECT country, AVG(unit_price) AS unit_price FROM transactions "
            "WHERE unit_price != 0 GROUP BY country"
        )
        failing_total_sql = (
            "SELECT AVG(unit_price) AS unit_price FROM secret_orders "
            "WHERE unit_price != 0"
        )
        companion = CompanionQuery(
            rule=RuleName.CUSTOMER_EXCLUDE_NULL,
            sql=(
                "SELECT COUNT(*) AS excluded_count FROM transactions "
                "WHERE customer_id IS NULL"
            ),
        )
        state = GraphState(
            raw_query="What is average unit price by country?",
            normalized_query="average unit price by country",
            query_intent=QueryIntent(
                aggregation="avg",
                metric="unit_price",
                group_by=["country"],
            ),
            sql_main=main_sql,
            sql_total=failing_total_sql,
            sql_companions={RuleName.CUSTOMER_EXCLUDE_NULL: companion},
            guardrail_status="pending",
            error=None,
        )
        stored_companions = state.sql_companions

        # A recording delegate proves the total is the second statement
        # validated (right after the main query) and that the companion after
        # it is never reached.
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

        self.assertEqual(result.error, "disallowed_table:secret_orders")
        self.assertEqual(result.guardrail_status, "failed")
        self.assertEqual(validated_sqls, [main_sql, failing_total_sql])
        self.assertNotIn(companion.sql, validated_sqls)

        # No write-back on the failure path: sql_main and sql_total stay
        # exactly as received, and sql_companions is the very same mapping with
        # the very same entry objects.
        self.assertEqual(result.sql_main, main_sql)
        self.assertEqual(result.sql_total, failing_total_sql)
        self.assertIs(result.sql_companions, stored_companions)
        self.assertEqual(result.sql_companions, state.sql_companions)
        expected = state.model_dump()
        expected["error"] = "disallowed_table:secret_orders"
        expected["guardrail_status"] = "failed"
        self.assertEqual(result.model_dump(), expected)


if __name__ == "__main__":
    unittest.main()
