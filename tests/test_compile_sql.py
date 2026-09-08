"""Tests for graph.nodes.compile_sql.

compile_sql is implemented for the base case and all four policy rules:

* Base case -- ``state.applicable_rules`` is empty: it compiles a plain
  aggregate query from ``query_intent`` via the sqlglot expression API and
  sets ``sql_companions`` to ``{}``.
* Rule ``AVG_EXCLUDE_ZERO_PRICE`` (rule 2) alone: the main query keeps every
  base filter AND-composed with ``unit_price != 0``, and ``sql_companions``
  carries one CompanionQuery that counts exactly the excluded rows
  (``COUNT(*)`` over the same base filters AND-composed with
  ``unit_price = 0``).
* Rule ``CUSTOMER_EXCLUDE_NULL`` (rule 3) alone: the same pattern over the
  customer dimension -- the main query keeps every base filter AND-composed
  with ``customer_id IS NOT NULL``, and the one CompanionQuery counts the
  excluded rows (``COUNT(*)`` over the same base filters AND-composed with
  ``customer_id IS NULL``).
* Rule ``PRODUCT_EXCLUDE_NONPRODUCT`` (rule 4) alone: the same pattern with
  its direction inverted -- the main query keeps every base filter
  AND-composed with ``line_item_type = 'product'``, and the one CompanionQuery
  counts the excluded rows (``COUNT(*)`` over the same base filters
  AND-composed with ``line_item_type != 'product'``).
* Rules 2 and 3 together, 2 and 4 together, 3 and 4 together, and all three
  together: each fired rule appends its own negation onto the same main query
  (for 2+3: ``unit_price != 0 AND customer_id IS NOT NULL``; for 2+3+4 the
  same two AND-composed with ``line_item_type = 'product'``, still AND-composed
  over every base filter) and its own CompanionQuery into ``sql_companions``.
* Rule ``NET_VS_GROSS`` (rule 1) alone: structurally different from rules 2-4
  -- it produces no companion query at all and adds no condition by default.
  From ``query_intent.net_gross`` it selects which of its three mutually
  exclusive named filter variants applies to the main query's WHERE clause:
  ``net`` (the default) adds nothing, ``gross_of_cancellations`` AND-composes
  ``invoice_id NOT LIKE 'C%'``, and ``returns`` AND-composes ``quantity < 0``.
  ``sql_companions`` stays ``{}``.
* Rule ``NET_VS_GROSS`` alongside any exclusion rule(s): the chosen variant
  condition composes by AND on the same main query alongside every fired
  exclusion rule's negation (e.g. gross-of-cancellations revenue excluding
  zero-price rows), while ``sql_companions`` gains entries only from the fired
  exclusion rules -- never from rule 1.

In every rule case both clauses derive from one shared predicate object that
the compiler authors a single time, so no test here should need to guard
against the main and companion conditions drifting apart -- the compiler
structure prevents it.

The live-database tests run every compiled query through the SQL guardrail
(``db.guardrails.validate_sql``) exactly as the graph's validate_guardrails /
execute_queries nodes will, then execute the guardrail-approved SQL against the
real read-only database, confirming the statements are valid, executable SQL
whose results match independently-run reference queries.
"""

import unittest
from pathlib import Path

import pydantic
from sqlglot import exp, parse

from db.connect import connect_readonly
from db.guardrails import validate_sql
from graph.nodes import compile_sql
from graph.state import GraphState, QueryIntent, RuleName

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DB_PATH = _PROJECT_ROOT / "data" / "processed" / "sentrasql.db"


def _read_schema() -> dict[str, set[str]]:
    """Load ``{table: {column}}`` from ``db/schema.sql`` for the guardrail."""
    ddl = (_PROJECT_ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
    schema: dict[str, set[str]] = {}
    for statement in parse(ddl, read="sqlite"):
        if isinstance(statement, exp.Create) and isinstance(
            statement.this, exp.Schema
        ):
            table = statement.this.this.name
            columns = {
                column_def.name
                for column_def in statement.this.find_all(exp.ColumnDef)
            }
            schema[table] = columns
    return schema


def _compile(
    aggregation: str = "sum",
    metric: str = "revenue",
    group_by: list[str] | None = None,
    filters: dict | None = None,
    rules: list[RuleName] | None = None,
    net_gross: str = "net",
) -> GraphState:
    """Build a GraphState and run compile_sql on it.

    The base-case tests pass no ``rules`` (empty, the default); the
    rule-specific tests pass any single rule or any combination of the four
    rules (``NET_VS_GROSS``, ``AVG_EXCLUDE_ZERO_PRICE``,
    ``CUSTOMER_EXCLUDE_NULL``, ``PRODUCT_EXCLUDE_NONPRODUCT``) -- for example
    ``[RuleName.AVG_EXCLUDE_ZERO_PRICE]`` or all four together. ``net_gross``
    sets which NET_VS_GROSS variant the intent asks for and defaults to the
    ``"net"`` variant; it only matters when ``rules`` includes
    ``NET_VS_GROSS``.
    """
    state = GraphState(
        raw_query="test query",
        query_intent=QueryIntent(
            aggregation=aggregation,
            metric=metric,
            group_by=group_by,
            filters=filters or {},
            net_gross=net_gross,
        ),
        applicable_rules=rules or [],
    )
    return compile_sql(state)


class CompileSqlBaseCaseTest(unittest.TestCase):
    def test_simple_total_revenue(self):
        state = _compile()
        self.assertEqual(
            state.sql_main,
            "SELECT SUM(quantity * unit_price) AS revenue FROM transactions",
        )
        self.assertEqual(state.sql_companions, {})

    def test_total_revenue_grouped_by_country(self):
        state = _compile(group_by=["country"])
        self.assertEqual(
            state.sql_main,
            "SELECT country, SUM(quantity * unit_price) AS revenue "
            "FROM transactions GROUP BY country",
        )
        self.assertEqual(state.sql_companions, {})

    def test_total_revenue_filtered_to_one_country(self):
        state = _compile(filters={"country": "United Kingdom"})
        self.assertEqual(
            state.sql_main,
            "SELECT SUM(quantity * unit_price) AS revenue FROM transactions "
            "WHERE country = 'United Kingdom'",
        )
        self.assertEqual(state.sql_companions, {})

    def test_plain_column_metric_average_price(self):
        state = _compile(aggregation="avg", metric="unit_price")
        self.assertEqual(
            state.sql_main,
            "SELECT AVG(unit_price) AS unit_price FROM transactions",
        )
        self.assertEqual(state.sql_companions, {})

    def test_all_four_rule_sets_compile_rather_than_raise(self):
        # Every RuleName member is implemented now: a NET_VS_GROSS-firing rule
        # set -- alone or alongside any exclusion rule -- must compile (not
        # raise NotImplementedError like the unimplemented-rules contract used
        # to demand). The dedicated rule test classes below assert the exact
        # compiled SQL for each of these sets.
        implemented_sets = [
            [RuleName.NET_VS_GROSS],
            [RuleName.NET_VS_GROSS, RuleName.AVG_EXCLUDE_ZERO_PRICE],
            [RuleName.NET_VS_GROSS, RuleName.CUSTOMER_EXCLUDE_NULL],
            [RuleName.NET_VS_GROSS, RuleName.PRODUCT_EXCLUDE_NONPRODUCT],
            [
                RuleName.NET_VS_GROSS,
                RuleName.AVG_EXCLUDE_ZERO_PRICE,
                RuleName.CUSTOMER_EXCLUDE_NULL,
                RuleName.PRODUCT_EXCLUDE_NONPRODUCT,
            ],
        ]
        for rules in implemented_sets:
            with self.subTest(rules=rules):
                state = _compile(rules=rules)
                self.assertIsNotNone(state.sql_main)


class CompileSqlAvgExcludeZeroPriceTest(unittest.TestCase):
    """compile_sql with AVG_EXCLUDE_ZERO_PRICE as the *only* applicable rule."""

    RULE = RuleName.AVG_EXCLUDE_ZERO_PRICE

    def _compile_avg(self, group_by=None, filters=None) -> GraphState:
        return _compile(
            aggregation="avg",
            metric="unit_price",
            group_by=group_by,
            filters=filters,
            rules=[self.RULE],
        )

    def test_average_price_without_filters_negates_and_sets_one_companion(self):
        state = self._compile_avg()
        self.assertEqual(
            state.sql_main,
            "SELECT AVG(unit_price) AS unit_price FROM transactions "
            "WHERE unit_price <> 0",
        )
        self.assertEqual(len(state.sql_companions), 1)
        companion = state.sql_companions[self.RULE]
        self.assertEqual(companion.rule, self.RULE)
        self.assertEqual(
            companion.sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE unit_price = 0",
        )
        self.assertEqual(companion.status, "pending")
        self.assertIsNone(companion.excluded_count)

    def test_average_price_keeps_country_filter_and_ands_the_negation(self):
        # Regression guard for the AND-composition contract: the rule exclusion
        # must be AND-ed onto the existing filters, never replace them. The
        # country filter has nothing to do with the zero-price rule, so its
        # presence in the same WHERE clause proves both survive together.
        state = self._compile_avg(filters={"country": "United Kingdom"})
        self.assertEqual(
            state.sql_main,
            "SELECT AVG(unit_price) AS unit_price FROM transactions "
            "WHERE country = 'United Kingdom' AND unit_price <> 0",
        )

        # AST-level check of the AND composition: the WHERE root is an AND over
        # the unrelated country equality and the zero-price inequality.
        where_condition = parse(
            state.sql_main, read="sqlite"
        )[0].find(exp.Where).this
        self.assertIsInstance(where_condition, exp.And)
        self.assertEqual(
            {
                where_condition.left.sql(dialect="sqlite"),
                where_condition.right.sql(dialect="sqlite"),
            },
            {"country = 'United Kingdom'", "unit_price <> 0"},
        )

        companion = state.sql_companions[self.RULE]
        self.assertEqual(
            companion.sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE country = 'United Kingdom' AND unit_price = 0",
        )
        companion_where = parse(companion.sql, read="sqlite")[0].find(
            exp.Where
        ).this
        self.assertIsInstance(companion_where, exp.And)
        self.assertEqual(
            {
                companion_where.left.sql(dialect="sqlite"),
                companion_where.right.sql(dialect="sqlite"),
            },
            {"country = 'United Kingdom'", "unit_price = 0"},
        )

    def test_grouped_average_price_keeps_group_by_and_global_companion(self):
        state = self._compile_avg(group_by=["country"])
        self.assertEqual(
            state.sql_main,
            "SELECT country, AVG(unit_price) AS unit_price FROM transactions "
            "WHERE unit_price <> 0 GROUP BY country",
        )
        # The companion stays a single scalar count of every excluded row --
        # it must not inherit the main query's GROUP BY, or its result would
        # stop being one countable total.
        self.assertEqual(
            state.sql_companions[self.RULE].sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE unit_price = 0",
        )

    def test_avg_rule_listed_twice_is_still_the_only_rule(self):
        # Dispatch keys on the *set* of applicable rules: a duplicated entry is
        # still "AVG_EXCLUDE_ZERO_PRICE as the only rule", not a combination.
        state = _compile(
            aggregation="avg",
            metric="unit_price",
            rules=[self.RULE, self.RULE],
        )
        self.assertEqual(len(state.sql_companions), 1)


class CompileSqlCustomerExcludeNullTest(unittest.TestCase):
    """compile_sql with CUSTOMER_EXCLUDE_NULL as the *only* applicable rule."""

    RULE = RuleName.CUSTOMER_EXCLUDE_NULL

    def _compile_customer(self, group_by=None, filters=None) -> GraphState:
        return _compile(
            aggregation="avg",
            metric="unit_price",
            group_by=group_by,
            filters=filters,
            rules=[self.RULE],
        )

    def test_average_price_without_filters_negates_and_sets_one_companion(self):
        state = self._compile_customer()
        self.assertEqual(
            state.sql_main,
            "SELECT AVG(unit_price) AS unit_price FROM transactions "
            "WHERE customer_id IS NOT NULL",
        )
        self.assertEqual(len(state.sql_companions), 1)
        companion = state.sql_companions[self.RULE]
        self.assertEqual(companion.rule, self.RULE)
        self.assertEqual(
            companion.sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE customer_id IS NULL",
        )
        self.assertEqual(companion.status, "pending")
        self.assertIsNone(companion.excluded_count)

    def test_average_price_keeps_country_filter_and_ands_the_is_not_null(self):
        # Regression guard for the AND-composition contract: the rule exclusion
        # must be AND-ed onto the existing filters, never replace them. The
        # country filter has nothing to do with the null-customer rule, so its
        # presence in the same WHERE clause proves both survive together.
        state = self._compile_customer(filters={"country": "United Kingdom"})
        self.assertEqual(
            state.sql_main,
            "SELECT AVG(unit_price) AS unit_price FROM transactions "
            "WHERE country = 'United Kingdom' AND customer_id IS NOT NULL",
        )

        # AST-level check of the AND composition: the WHERE root is an AND over
        # the unrelated country equality and the negation. sqlglot parses
        # ``customer_id IS NOT NULL`` as ``NOT (customer_id IS NULL)``, which
        # is how the parsed right-hand side serializes back below.
        where_condition = parse(
            state.sql_main, read="sqlite"
        )[0].find(exp.Where).this
        self.assertIsInstance(where_condition, exp.And)
        self.assertEqual(
            {
                where_condition.left.sql(dialect="sqlite"),
                where_condition.right.sql(dialect="sqlite"),
            },
            {"country = 'United Kingdom'", "NOT customer_id IS NULL"},
        )

        companion = state.sql_companions[self.RULE]
        self.assertEqual(
            companion.sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE country = 'United Kingdom' AND customer_id IS NULL",
        )
        companion_where = parse(companion.sql, read="sqlite")[0].find(
            exp.Where
        ).this
        self.assertIsInstance(companion_where, exp.And)
        self.assertEqual(
            {
                companion_where.left.sql(dialect="sqlite"),
                companion_where.right.sql(dialect="sqlite"),
            },
            {"country = 'United Kingdom'", "customer_id IS NULL"},
        )

    def test_grouped_average_price_keeps_group_by_and_global_companion(self):
        state = self._compile_customer(group_by=["country"])
        self.assertEqual(
            state.sql_main,
            "SELECT country, AVG(unit_price) AS unit_price FROM transactions "
            "WHERE customer_id IS NOT NULL GROUP BY country",
        )
        # The companion stays a single scalar count of every excluded row --
        # it must not inherit the main query's GROUP BY, or its result would
        # stop being one countable total.
        self.assertEqual(
            state.sql_companions[self.RULE].sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE customer_id IS NULL",
        )

    def test_customer_rule_listed_twice_is_still_the_only_rule(self):
        # Dispatch keys on the *set* of applicable rules: a duplicated entry is
        # still "CUSTOMER_EXCLUDE_NULL as the only rule", not a combination.
        state = _compile(
            aggregation="avg",
            metric="unit_price",
            rules=[self.RULE, self.RULE],
        )
        self.assertEqual(
            state.sql_main,
            "SELECT AVG(unit_price) AS unit_price FROM transactions "
            "WHERE customer_id IS NOT NULL",
        )
        self.assertEqual(len(state.sql_companions), 1)


class CompileSqlProductExcludeNonProductTest(unittest.TestCase):
    """compile_sql with PRODUCT_EXCLUDE_NONPRODUCT as the *only* applicable rule."""

    RULE = RuleName.PRODUCT_EXCLUDE_NONPRODUCT

    def _compile_product(self, group_by=None, filters=None) -> GraphState:
        return _compile(
            aggregation="avg",
            metric="unit_price",
            group_by=group_by,
            filters=filters,
            rules=[self.RULE],
        )

    def test_average_price_without_filters_keeps_products_and_sets_one_companion(self):
        state = self._compile_product()
        self.assertEqual(
            state.sql_main,
            "SELECT AVG(unit_price) AS unit_price FROM transactions "
            "WHERE line_item_type = 'product'",
        )
        self.assertEqual(len(state.sql_companions), 1)
        companion = state.sql_companions[self.RULE]
        self.assertEqual(companion.rule, self.RULE)
        self.assertEqual(
            companion.sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE line_item_type <> 'product'",
        )
        self.assertEqual(companion.status, "pending")
        self.assertIsNone(companion.excluded_count)

    def test_average_price_keeps_country_filter_and_ands_the_product_predicate(self):
        # Regression guard for the AND-composition contract: the rule exclusion
        # must be AND-ed onto the existing filters, never replace them. The
        # country filter has nothing to do with the non-product rule, so its
        # presence in the same WHERE clause proves both survive together.
        state = self._compile_product(filters={"country": "United Kingdom"})
        self.assertEqual(
            state.sql_main,
            "SELECT AVG(unit_price) AS unit_price FROM transactions "
            "WHERE country = 'United Kingdom' AND line_item_type = 'product'",
        )

        # AST-level check of the AND composition: the WHERE root is an AND over
        # the unrelated country equality and the rule's product predicate.
        where_condition = parse(
            state.sql_main, read="sqlite"
        )[0].find(exp.Where).this
        self.assertIsInstance(where_condition, exp.And)
        self.assertEqual(
            {
                where_condition.left.sql(dialect="sqlite"),
                where_condition.right.sql(dialect="sqlite"),
            },
            {"country = 'United Kingdom'", "line_item_type = 'product'"},
        )

        companion = state.sql_companions[self.RULE]
        self.assertEqual(
            companion.sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE country = 'United Kingdom' AND line_item_type <> 'product'",
        )
        companion_where = parse(companion.sql, read="sqlite")[0].find(
            exp.Where
        ).this
        self.assertIsInstance(companion_where, exp.And)
        self.assertEqual(
            {
                companion_where.left.sql(dialect="sqlite"),
                companion_where.right.sql(dialect="sqlite"),
            },
            {"country = 'United Kingdom'", "line_item_type <> 'product'"},
        )

    def test_grouped_average_price_keeps_group_by_and_global_companion(self):
        state = self._compile_product(group_by=["country"])
        self.assertEqual(
            state.sql_main,
            "SELECT country, AVG(unit_price) AS unit_price FROM transactions "
            "WHERE line_item_type = 'product' GROUP BY country",
        )
        # The companion stays a single scalar count of every excluded row --
        # it must not inherit the main query's GROUP BY, or its result would
        # stop being one countable total.
        self.assertEqual(
            state.sql_companions[self.RULE].sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE line_item_type <> 'product'",
        )

    def test_product_rule_listed_twice_is_still_the_only_rule(self):
        # Dispatch keys on the *set* of applicable rules: a duplicated entry is
        # still "PRODUCT_EXCLUDE_NONPRODUCT as the only rule", not a
        # combination.
        state = _compile(
            aggregation="avg",
            metric="unit_price",
            rules=[self.RULE, self.RULE],
        )
        self.assertEqual(
            state.sql_main,
            "SELECT AVG(unit_price) AS unit_price FROM transactions "
            "WHERE line_item_type = 'product'",
        )
        self.assertEqual(len(state.sql_companions), 1)


class CompileSqlAvgAndCustomerExcludeNullTest(unittest.TestCase):
    """compile_sql with rules 2 and 3 firing on the same query.

    Both rules must compose by AND onto one main query, and each fired rule
    must populate its own CompanionQuery -- this is the real, expected
    multi-rule case (e.g. an average price excluding both zero-price rows and
    unknown-customer rows).
    """

    RULES = [
        RuleName.AVG_EXCLUDE_ZERO_PRICE,
        RuleName.CUSTOMER_EXCLUDE_NULL,
    ]

    def _compile_both(self, filters=None) -> GraphState:
        return _compile(
            aggregation="avg",
            metric="unit_price",
            filters=filters,
            rules=list(self.RULES),
        )

    def test_both_negations_compose_onto_main_and_both_companions_present(self):
        state = self._compile_both()
        # Both negations must sit on the same main WHERE clause...
        self.assertEqual(
            state.sql_main,
            "SELECT AVG(unit_price) AS unit_price FROM transactions "
            "WHERE unit_price <> 0 AND customer_id IS NOT NULL",
        )
        # ...and both companions must be populated, one per fired rule.
        self.assertEqual(set(state.sql_companions), set(self.RULES))
        self.assertEqual(
            state.sql_companions[RuleName.AVG_EXCLUDE_ZERO_PRICE].sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE unit_price = 0",
        )
        self.assertEqual(
            state.sql_companions[RuleName.CUSTOMER_EXCLUDE_NULL].sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE customer_id IS NULL",
        )

    def test_both_negations_compose_onto_main_over_unrelated_filter(self):
        # The unrelated country filter must survive alongside *both* rule
        # negations: AND-composition onto the existing filters, never
        # replacement of them.
        state = self._compile_both(filters={"country": "United Kingdom"})
        self.assertEqual(
            state.sql_main,
            "SELECT AVG(unit_price) AS unit_price FROM transactions "
            "WHERE country = 'United Kingdom' AND unit_price <> 0 "
            "AND customer_id IS NOT NULL",
        )

        # AST-level check: flattening the AND tree yields exactly the country
        # filter plus both negations -- proving both exclusions are present on
        # the main query at once.
        where_condition = parse(
            state.sql_main, read="sqlite"
        )[0].find(exp.Where).this
        leaves = {
            leaf.sql(dialect="sqlite") for leaf in where_condition.flatten()
        }
        self.assertEqual(
            leaves,
            {
                "country = 'United Kingdom'",
                "unit_price <> 0",
                "NOT customer_id IS NULL",
            },
        )

        # Each companion keeps the rule's own predicate under the same base
        # filter; both are keyed under their own rule in the same dict.
        self.assertEqual(
            state.sql_companions[RuleName.AVG_EXCLUDE_ZERO_PRICE].sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE country = 'United Kingdom' AND unit_price = 0",
        )
        self.assertEqual(
            state.sql_companions[RuleName.CUSTOMER_EXCLUDE_NULL].sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE country = 'United Kingdom' AND customer_id IS NULL",
        )

    def test_applicable_rule_order_does_not_change_the_compiled_query(self):
        # The compiler iterates its own fixed rule order, so the compiled SQL
        # is deterministic regardless of the order the rules were detected in.
        state = _compile(
            aggregation="avg",
            metric="unit_price",
            filters={"country": "United Kingdom"},
            rules=[
                RuleName.CUSTOMER_EXCLUDE_NULL,
                RuleName.AVG_EXCLUDE_ZERO_PRICE,
            ],
        )
        self.assertEqual(
            state.sql_main,
            "SELECT AVG(unit_price) AS unit_price FROM transactions "
            "WHERE country = 'United Kingdom' AND unit_price <> 0 "
            "AND customer_id IS NOT NULL",
        )
        self.assertEqual(set(state.sql_companions), set(self.RULES))

    def test_both_rules_with_duplicate_entries_still_compile_once_each(self):
        # Dispatch keys on the *set* of applicable rules: duplicated entries
        # must not double-append a negation onto the main query or overwrite a
        # companion.
        state = _compile(
            aggregation="avg",
            metric="unit_price",
            rules=[
                RuleName.AVG_EXCLUDE_ZERO_PRICE,
                RuleName.CUSTOMER_EXCLUDE_NULL,
                RuleName.AVG_EXCLUDE_ZERO_PRICE,
            ],
        )
        self.assertEqual(
            state.sql_main,
            "SELECT AVG(unit_price) AS unit_price FROM transactions "
            "WHERE unit_price <> 0 AND customer_id IS NOT NULL",
        )
        self.assertEqual(set(state.sql_companions), set(self.RULES))


class CompileSqlThreeExclusionsTest(unittest.TestCase):
    """compile_sql with rules 2, 3, and 4 firing in the same query.

    PRODUCT_EXCLUDE_NONPRODUCT composes with rules 2 and 3 exactly like those
    two compose with each other, so every pair of the three exclusion rules
    and the full 2+3+4 triple are now valid rule sets. Each fired rule must
    AND its own negation onto the one main query and populate its own
    CompanionQuery; the full-combination main query carries all three
    negations at once.
    """

    RULES = [
        RuleName.AVG_EXCLUDE_ZERO_PRICE,
        RuleName.CUSTOMER_EXCLUDE_NULL,
        RuleName.PRODUCT_EXCLUDE_NONPRODUCT,
    ]

    def _compile_three(self, filters=None) -> GraphState:
        return _compile(
            aggregation="avg",
            metric="unit_price",
            filters=filters,
            rules=list(self.RULES),
        )

    def test_three_negations_compose_onto_main_and_three_companions_present(self):
        state = self._compile_three()
        self.assertEqual(
            state.sql_main,
            "SELECT AVG(unit_price) AS unit_price FROM transactions "
            "WHERE unit_price <> 0 AND customer_id IS NOT NULL "
            "AND line_item_type = 'product'",
        )
        self.assertEqual(set(state.sql_companions), set(self.RULES))
        self.assertEqual(
            state.sql_companions[RuleName.AVG_EXCLUDE_ZERO_PRICE].sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE unit_price = 0",
        )
        self.assertEqual(
            state.sql_companions[RuleName.CUSTOMER_EXCLUDE_NULL].sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE customer_id IS NULL",
        )
        self.assertEqual(
            state.sql_companions[RuleName.PRODUCT_EXCLUDE_NONPRODUCT].sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE line_item_type <> 'product'",
        )

    def test_all_three_negations_compose_onto_main_over_unrelated_filter(self):
        # The unrelated country filter must survive alongside *all three* rule
        # negations: AND-composition onto the existing filters, never
        # replacement of them.
        state = self._compile_three(filters={"country": "United Kingdom"})
        self.assertEqual(
            state.sql_main,
            "SELECT AVG(unit_price) AS unit_price FROM transactions "
            "WHERE country = 'United Kingdom' AND unit_price <> 0 "
            "AND customer_id IS NOT NULL AND line_item_type = 'product'",
        )

        # AST-level check: flattening the AND tree yields exactly the country
        # filter plus all three negations -- proving every exclusion is present
        # on the main query at once. sqlglot parses ``customer_id IS NOT
        # NULL`` as ``NOT (customer_id IS NULL)``, which is how that leaf
        # serializes back below.
        where_condition = parse(
            state.sql_main, read="sqlite"
        )[0].find(exp.Where).this
        leaves = {
            leaf.sql(dialect="sqlite") for leaf in where_condition.flatten()
        }
        self.assertEqual(
            leaves,
            {
                "country = 'United Kingdom'",
                "unit_price <> 0",
                "NOT customer_id IS NULL",
                "line_item_type = 'product'",
            },
        )

        # Each companion keeps the rule's own predicate under the same base
        # filter; all three are keyed under their own rule in the same dict.
        self.assertEqual(
            state.sql_companions[RuleName.AVG_EXCLUDE_ZERO_PRICE].sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE country = 'United Kingdom' AND unit_price = 0",
        )
        self.assertEqual(
            state.sql_companions[RuleName.CUSTOMER_EXCLUDE_NULL].sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE country = 'United Kingdom' AND customer_id IS NULL",
        )
        self.assertEqual(
            state.sql_companions[RuleName.PRODUCT_EXCLUDE_NONPRODUCT].sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE country = 'United Kingdom' AND line_item_type <> 'product'",
        )

    def test_rule_2_plus_rule_4_compose(self):
        state = _compile(
            aggregation="avg",
            metric="unit_price",
            filters={"country": "United Kingdom"},
            rules=[
                RuleName.AVG_EXCLUDE_ZERO_PRICE,
                RuleName.PRODUCT_EXCLUDE_NONPRODUCT,
            ],
        )
        self.assertEqual(
            state.sql_main,
            "SELECT AVG(unit_price) AS unit_price FROM transactions "
            "WHERE country = 'United Kingdom' AND unit_price <> 0 "
            "AND line_item_type = 'product'",
        )
        self.assertEqual(
            set(state.sql_companions),
            {
                RuleName.AVG_EXCLUDE_ZERO_PRICE,
                RuleName.PRODUCT_EXCLUDE_NONPRODUCT,
            },
        )

    def test_rule_3_plus_rule_4_compose(self):
        state = _compile(
            aggregation="avg",
            metric="unit_price",
            rules=[
                RuleName.CUSTOMER_EXCLUDE_NULL,
                RuleName.PRODUCT_EXCLUDE_NONPRODUCT,
            ],
        )
        self.assertEqual(
            state.sql_main,
            "SELECT AVG(unit_price) AS unit_price FROM transactions "
            "WHERE customer_id IS NOT NULL AND line_item_type = 'product'",
        )
        self.assertEqual(
            set(state.sql_companions),
            {
                RuleName.CUSTOMER_EXCLUDE_NULL,
                RuleName.PRODUCT_EXCLUDE_NONPRODUCT,
            },
        )

    def test_applicable_rule_order_does_not_change_the_compiled_query(self):
        # The compiler iterates its own fixed rule order, so the compiled SQL
        # is deterministic regardless of the order the rules were detected in.
        state = _compile(
            aggregation="avg",
            metric="unit_price",
            filters={"country": "United Kingdom"},
            rules=[
                RuleName.PRODUCT_EXCLUDE_NONPRODUCT,
                RuleName.CUSTOMER_EXCLUDE_NULL,
                RuleName.AVG_EXCLUDE_ZERO_PRICE,
            ],
        )
        self.assertEqual(
            state.sql_main,
            "SELECT AVG(unit_price) AS unit_price FROM transactions "
            "WHERE country = 'United Kingdom' AND unit_price <> 0 "
            "AND customer_id IS NOT NULL AND line_item_type = 'product'",
        )
        self.assertEqual(set(state.sql_companions), set(self.RULES))

    def test_three_rules_with_duplicate_entries_still_compile_once_each(self):
        # Dispatch keys on the *set* of applicable rules: duplicated entries
        # must not double-append a negation onto the main query or overwrite a
        # companion.
        state = _compile(
            aggregation="avg",
            metric="unit_price",
            rules=[
                RuleName.AVG_EXCLUDE_ZERO_PRICE,
                RuleName.CUSTOMER_EXCLUDE_NULL,
                RuleName.PRODUCT_EXCLUDE_NONPRODUCT,
                RuleName.AVG_EXCLUDE_ZERO_PRICE,
            ],
        )
        self.assertEqual(
            state.sql_main,
            "SELECT AVG(unit_price) AS unit_price FROM transactions "
            "WHERE unit_price <> 0 AND customer_id IS NOT NULL "
            "AND line_item_type = 'product'",
        )
        self.assertEqual(set(state.sql_companions), set(self.RULES))


class CompileSqlNetVsGrossTest(unittest.TestCase):
    """compile_sql with NET_VS_GROSS as an applicable rule.

    Rule NET_VS_GROSS is structurally different from the three exclusion rules:
    it produces no companion query at all and does not add a condition by
    default. When it fires it selects which of its three mutually exclusive
    named filter variants (``net`` / ``gross_of_cancellations`` / ``returns``)
    the main query's WHERE clause applies, from ``query_intent.net_gross``.
    Only the two non-default variants contribute a condition, and neither one
    ever produces a CompanionQuery.
    """

    RULE = RuleName.NET_VS_GROSS

    def _compile_net_vs_gross(
        self,
        net_gross: str,
        filters: dict | None = None,
        extra_rules: list[RuleName] | None = None,
    ) -> GraphState:
        rules = [self.RULE] + list(extra_rules or [])
        return _compile(
            aggregation="sum",
            metric="revenue",
            filters=filters,
            rules=rules,
            net_gross=net_gross,
        )

    def test_default_net_variant_adds_no_where_clause_and_no_companion(self):
        state = self._compile_net_vs_gross("net")
        self.assertEqual(
            state.sql_main,
            "SELECT SUM(quantity * unit_price) AS revenue FROM transactions",
        )
        self.assertEqual(state.sql_companions, {})
        # Genuinely no WHERE clause anywhere in the parsed SELECT, not even an
        # empty one -- the net default adds nothing.
        self.assertIsNone(parse(state.sql_main, read="sqlite")[0].find(exp.Where))

    def test_default_net_variant_keeps_only_the_users_own_filters(self):
        state = self._compile_net_vs_gross(
            "net", filters={"country": "United Kingdom"}
        )
        self.assertEqual(
            state.sql_main,
            "SELECT SUM(quantity * unit_price) AS revenue FROM transactions "
            "WHERE country = 'United Kingdom'",
        )
        self.assertEqual(state.sql_companions, {})
        # The only WHERE condition is the user's country equality -- the rule
        # must not have appended anything onto it.
        where_condition = parse(
            state.sql_main, read="sqlite"
        )[0].find(exp.Where).this
        self.assertNotIsInstance(where_condition, exp.And)

    def test_gross_of_cancellations_variant_ands_invoice_not_like(self):
        state = self._compile_net_vs_gross("gross_of_cancellations")
        self.assertEqual(
            state.sql_main,
            "SELECT SUM(quantity * unit_price) AS revenue FROM transactions "
            "WHERE invoice_id NOT LIKE 'C%'",
        )
        self.assertEqual(state.sql_companions, {})
        # AST-level check that the condition is a genuinely negated LIKE node,
        # not an unparsed ``NOT (...)`` wrapper.
        where_condition = parse(
            state.sql_main, read="sqlite"
        )[0].find(exp.Where).this
        self.assertIsInstance(where_condition, exp.Like)
        self.assertTrue(where_condition.args.get("negate"))

    def test_gross_of_cancellations_variant_keeps_user_filter_and_ands(self):
        state = self._compile_net_vs_gross(
            "gross_of_cancellations", filters={"country": "United Kingdom"}
        )
        self.assertEqual(
            state.sql_main,
            "SELECT SUM(quantity * unit_price) AS revenue FROM transactions "
            "WHERE country = 'United Kingdom' AND invoice_id NOT LIKE 'C%'",
        )
        self.assertEqual(state.sql_companions, {})
        where_condition = parse(
            state.sql_main, read="sqlite"
        )[0].find(exp.Where).this
        self.assertIsInstance(where_condition, exp.And)
        self.assertEqual(
            {
                where_condition.left.sql(dialect="sqlite"),
                where_condition.right.sql(dialect="sqlite"),
            },
            {"country = 'United Kingdom'", "invoice_id NOT LIKE 'C%'"},
        )

    def test_returns_variant_ands_quantity_lt_zero(self):
        state = self._compile_net_vs_gross("returns")
        self.assertEqual(
            state.sql_main,
            "SELECT SUM(quantity * unit_price) AS revenue FROM transactions "
            "WHERE quantity < 0",
        )
        self.assertEqual(state.sql_companions, {})
        where_condition = parse(
            state.sql_main, read="sqlite"
        )[0].find(exp.Where).this
        self.assertIsInstance(where_condition, exp.LT)

    def test_returns_variant_keeps_user_filter_and_ands(self):
        state = self._compile_net_vs_gross(
            "returns", filters={"country": "United Kingdom"}
        )
        self.assertEqual(
            state.sql_main,
            "SELECT SUM(quantity * unit_price) AS revenue FROM transactions "
            "WHERE country = 'United Kingdom' AND quantity < 0",
        )
        self.assertEqual(state.sql_companions, {})
        where_condition = parse(
            state.sql_main, read="sqlite"
        )[0].find(exp.Where).this
        self.assertIsInstance(where_condition, exp.And)
        self.assertEqual(
            {
                where_condition.left.sql(dialect="sqlite"),
                where_condition.right.sql(dialect="sqlite"),
            },
            {"country = 'United Kingdom'", "quantity < 0"},
        )

    def test_default_variant_adds_no_condition_versus_the_two_named_variants(self):
        # Compile all three variants from the *same* base filters. The default
        # net variant's WHERE must be exactly the user's own filter (no extra
        # ANDed condition), while each named variant adds exactly its own one
        # condition. This is the "genuinely no extra WHERE clause" contract.
        filters = {"country": "United Kingdom"}
        net_state = self._compile_net_vs_gross("net", filters=filters)
        gross_state = self._compile_net_vs_gross(
            "gross_of_cancellations", filters=filters
        )
        returns_state = self._compile_net_vs_gross("returns", filters=filters)

        net_where = parse(
            net_state.sql_main, read="sqlite"
        )[0].find(exp.Where).this
        gross_where = parse(
            gross_state.sql_main, read="sqlite"
        )[0].find(exp.Where).this
        returns_where = parse(
            returns_state.sql_main, read="sqlite"
        )[0].find(exp.Where).this

        self.assertEqual(
            net_where.sql(dialect="sqlite"), "country = 'United Kingdom'"
        )
        self.assertIsInstance(gross_where, exp.And)
        self.assertEqual(
            {
                gross_where.left.sql(dialect="sqlite"),
                gross_where.right.sql(dialect="sqlite"),
            },
            {"country = 'United Kingdom'", "invoice_id NOT LIKE 'C%'"},
        )
        self.assertIsInstance(returns_where, exp.And)
        self.assertEqual(
            {
                returns_where.left.sql(dialect="sqlite"),
                returns_where.right.sql(dialect="sqlite"),
            },
            {"country = 'United Kingdom'", "quantity < 0"},
        )

        # The net query must not contain either named variant's condition at
        # all, while each named variant's query does.
        self.assertNotIn("NOT LIKE", net_state.sql_main)
        self.assertNotIn("quantity < 0", net_state.sql_main)
        self.assertIn("invoice_id NOT LIKE 'C%'", gross_state.sql_main)
        self.assertIn("quantity < 0", returns_state.sql_main)

    def test_gross_variant_composes_with_zero_price_exclusion_rule(self):
        # The user-visible composition example: gross revenue, excluding
        # zero-price rows. The NET_VS_GROSS condition ANDs onto the same main
        # query as the rule-2 negation, and sql_companions gains an entry only
        # for the exclusion rule -- never for NET_VS_GROSS.
        state = self._compile_net_vs_gross(
            "gross_of_cancellations",
            filters={"country": "United Kingdom"},
            extra_rules=[RuleName.AVG_EXCLUDE_ZERO_PRICE],
        )
        self.assertEqual(
            state.sql_main,
            "SELECT SUM(quantity * unit_price) AS revenue FROM transactions "
            "WHERE country = 'United Kingdom' AND invoice_id NOT LIKE 'C%' "
            "AND unit_price <> 0",
        )
        self.assertEqual(
            set(state.sql_companions), {RuleName.AVG_EXCLUDE_ZERO_PRICE}
        )
        companion = state.sql_companions[RuleName.AVG_EXCLUDE_ZERO_PRICE]
        self.assertEqual(
            companion.sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE country = 'United Kingdom' AND unit_price = 0",
        )

    def test_returns_variant_composes_with_product_exclusion_rule(self):
        state = self._compile_net_vs_gross(
            "returns",
            filters={"country": "United Kingdom"},
            extra_rules=[RuleName.PRODUCT_EXCLUDE_NONPRODUCT],
        )
        self.assertEqual(
            state.sql_main,
            "SELECT SUM(quantity * unit_price) AS revenue FROM transactions "
            "WHERE country = 'United Kingdom' AND quantity < 0 "
            "AND line_item_type = 'product'",
        )
        self.assertEqual(
            set(state.sql_companions), {RuleName.PRODUCT_EXCLUDE_NONPRODUCT}
        )
        companion = state.sql_companions[RuleName.PRODUCT_EXCLUDE_NONPRODUCT]
        self.assertEqual(
            companion.sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE country = 'United Kingdom' AND line_item_type <> 'product'",
        )

    def test_net_variant_composes_with_customer_null_exclusion_rule(self):
        # Even the default net variant composes with an exclusion rule: it
        # contributes no condition of its own, yet the exclusion rule's
        # negation and companion appear exactly as if NET_VS_GROSS were absent.
        state = self._compile_net_vs_gross(
            "net",
            filters={"country": "United Kingdom"},
            extra_rules=[RuleName.CUSTOMER_EXCLUDE_NULL],
        )
        self.assertEqual(
            state.sql_main,
            "SELECT SUM(quantity * unit_price) AS revenue FROM transactions "
            "WHERE country = 'United Kingdom' AND customer_id IS NOT NULL",
        )
        self.assertEqual(
            set(state.sql_companions), {RuleName.CUSTOMER_EXCLUDE_NULL}
        )


@unittest.skipUnless(
    _DB_PATH.exists(), f"real database not present at {_DB_PATH}"
)
class CompileSqlAvgExcludeZeroPriceLiveDbTest(unittest.TestCase):
    """Run the compiled rule-2 queries through the guardrail on the real DB.

    Mirrors the production path (compile_sql -> validate_guardrails ->
    execute_queries): every compiled statement is first approved by
    ``db.guardrails.validate_sql`` against the live schema, and only the
    guardrail-approved SQL is executed -- on the read-only connection, exactly
    as ``execute_queries`` will. Expected values are computed by independent
    reference queries on the same connection, never assumed from the compiled
    SQL.
    """

    RULE = RuleName.AVG_EXCLUDE_ZERO_PRICE

    @classmethod
    def setUpClass(cls):
        cls.conn = connect_readonly(_DB_PATH)
        cls.schema = _read_schema()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def _compile(self, filters: dict | None) -> tuple[str, str]:
        """Compile the rule-2 avg query and return the validated main/companion SQL."""
        state = _compile(
            aggregation="avg",
            metric="unit_price",
            filters=filters,
            rules=[self.RULE],
        )
        main_passed, main_reason, main_sql, _ = validate_sql(
            state.sql_main, self.schema
        )
        self.assertTrue(
            main_passed, f"guardrail rejected main query: {main_reason}"
        )
        companion = state.sql_companions[self.RULE]
        comp_passed, comp_reason, comp_sql, _ = validate_sql(
            companion.sql, self.schema
        )
        self.assertTrue(
            comp_passed, f"guardrail rejected companion query: {comp_reason}"
        )
        return main_sql, comp_sql

    def _reference_avg(self, filters_sql: str) -> float:
        """AVG over the same filters plus the explicit zero-price exclusion."""
        where = f"{filters_sql} AND unit_price != 0" if filters_sql else (
            "unit_price != 0"
        )
        return self.conn.execute(
            f"SELECT AVG(unit_price) FROM transactions WHERE {where}"
        ).fetchone()[0]

    def _reference_count(self, filters_sql: str, price_sql: str) -> int:
        where = f"{filters_sql} AND {price_sql}" if filters_sql else price_sql
        return self.conn.execute(
            f"SELECT COUNT(*) FROM transactions WHERE {where}"
        ).fetchone()[0]

    def test_uk_country_filter_main_and_companion_execute_and_match(self):
        filters_sql = "country = 'United Kingdom'"
        main_sql, comp_sql = self._compile(filters={"country": "United Kingdom"})

        main_avg = self.conn.execute(main_sql).fetchone()[0]
        # The negation must have real effect: the UK data has thousands of
        # zero-price rows, so the excluded average differs from the raw one.
        avg_all = self.conn.execute(
            f"SELECT AVG(unit_price) FROM transactions WHERE {filters_sql}"
        ).fetchone()[0]
        avg_excluding_zero = self._reference_avg(filters_sql)
        self.assertNotAlmostEqual(main_avg, avg_all, places=6)
        self.assertAlmostEqual(main_avg, avg_excluding_zero, places=6)

        excluded = self.conn.execute(comp_sql).fetchone()[0]
        expected_excluded = self._reference_count(
            filters_sql, "unit_price = 0"
        )
        self.assertEqual(excluded, expected_excluded)
        self.assertGreater(excluded, 0)

    def test_unfiltered_average_and_companion_execute_and_match(self):
        main_sql, comp_sql = self._compile(filters=None)

        main_avg = self.conn.execute(main_sql).fetchone()[0]
        self.assertAlmostEqual(
            main_avg, self._reference_avg(""), places=6
        )

        excluded = self.conn.execute(comp_sql).fetchone()[0]
        expected_excluded = self._reference_count("", "unit_price = 0")
        self.assertEqual(excluded, expected_excluded)
        self.assertGreater(excluded, 0)

    def test_companion_keeps_unrelated_country_filter_against_real_data(self):
        # The companion must count excluded rows for the *same* country the
        # main query scoped, not every zero-price row in the table.
        filters_sql = "country = 'Germany'"
        _main_sql, comp_sql = self._compile(filters={"country": "Germany"})

        excluded = self.conn.execute(comp_sql).fetchone()[0]
        expected_excluded = self._reference_count(
            filters_sql, "unit_price = 0"
        )
        self.assertEqual(excluded, expected_excluded)
        total_zero = self._reference_count("", "unit_price = 0")
        self.assertLess(excluded, total_zero)
        self.assertGreater(excluded, 0)


@unittest.skipUnless(
    _DB_PATH.exists(), f"real database not present at {_DB_PATH}"
)
class CompileSqlCustomerExcludeNullLiveDbTest(unittest.TestCase):
    """Run the compiled rule-3 queries through the guardrail on the real DB.

    Mirrors the production path (compile_sql -> validate_guardrails ->
    execute_queries) exactly as the rule-2 live tests do: every compiled
    statement is first approved by ``db.guardrails.validate_sql`` against the
    live schema, and only the guardrail-approved SQL is executed -- on the
    read-only connection. Expected values come from independent reference
    queries on the same connection, never assumed from the compiled SQL.
    """

    RULE = RuleName.CUSTOMER_EXCLUDE_NULL

    @classmethod
    def setUpClass(cls):
        cls.conn = connect_readonly(_DB_PATH)
        cls.schema = _read_schema()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def _compile(self, filters: dict | None) -> tuple[str, str]:
        """Compile the rule-3 query and return validated main/companion SQL."""
        state = _compile(
            aggregation="avg",
            metric="unit_price",
            filters=filters,
            rules=[self.RULE],
        )
        main_passed, main_reason, main_sql, _ = validate_sql(
            state.sql_main, self.schema
        )
        self.assertTrue(
            main_passed, f"guardrail rejected main query: {main_reason}"
        )
        companion = state.sql_companions[self.RULE]
        comp_passed, comp_reason, comp_sql, _ = validate_sql(
            companion.sql, self.schema
        )
        self.assertTrue(
            comp_passed, f"guardrail rejected companion query: {comp_reason}"
        )
        return main_sql, comp_sql

    def _reference_avg(self, filters_sql: str) -> float:
        """AVG over the same filters plus the non-null-customer scope."""
        where = f"{filters_sql} AND customer_id IS NOT NULL" if filters_sql else (
            "customer_id IS NOT NULL"
        )
        return self.conn.execute(
            f"SELECT AVG(unit_price) FROM transactions WHERE {where}"
        ).fetchone()[0]

    def _reference_count(self, filters_sql: str) -> int:
        where = f"{filters_sql} AND customer_id IS NULL" if filters_sql else (
            "customer_id IS NULL"
        )
        return self.conn.execute(
            f"SELECT COUNT(*) FROM transactions WHERE {where}"
        ).fetchone()[0]

    def test_uk_country_filter_main_and_companion_execute_and_match(self):
        filters_sql = "country = 'United Kingdom'"
        main_sql, comp_sql = self._compile(filters={"country": "United Kingdom"})

        main_avg = self.conn.execute(main_sql).fetchone()[0]
        # The negation must have real effect: the UK data has hundreds of
        # thousands of null-customer rows, so the known-customer-only average
        # differs from the raw one.
        avg_all = self.conn.execute(
            f"SELECT AVG(unit_price) FROM transactions WHERE {filters_sql}"
        ).fetchone()[0]
        avg_known = self._reference_avg(filters_sql)
        self.assertNotAlmostEqual(main_avg, avg_all, places=6)
        self.assertAlmostEqual(main_avg, avg_known, places=6)

        excluded = self.conn.execute(comp_sql).fetchone()[0]
        expected_excluded = self._reference_count(filters_sql)
        self.assertEqual(excluded, expected_excluded)
        self.assertGreater(excluded, 0)

    def test_unfiltered_average_and_companion_execute_and_match(self):
        main_sql, comp_sql = self._compile(filters=None)

        main_avg = self.conn.execute(main_sql).fetchone()[0]
        self.assertAlmostEqual(main_avg, self._reference_avg(""), places=6)

        excluded = self.conn.execute(comp_sql).fetchone()[0]
        expected_excluded = self._reference_count("")
        self.assertEqual(excluded, expected_excluded)
        self.assertGreater(excluded, 0)

    def test_companion_keeps_unrelated_country_filter_against_real_data(self):
        # The companion must count excluded rows for the *same* country the
        # main query scoped, not every null-customer row in the table.
        filters_sql = "country = 'United Kingdom'"
        _main_sql, comp_sql = self._compile(filters={"country": "United Kingdom"})

        excluded = self.conn.execute(comp_sql).fetchone()[0]
        expected_excluded = self._reference_count(filters_sql)
        self.assertEqual(excluded, expected_excluded)
        total_null = self._reference_count("")
        self.assertLess(excluded, total_null)
        self.assertGreater(excluded, 0)


@unittest.skipUnless(
    _DB_PATH.exists(), f"real database not present at {_DB_PATH}"
)
class CompileSqlProductExcludeNonProductLiveDbTest(unittest.TestCase):
    """Run the compiled rule-4 queries through the guardrail on the real DB.

    Mirrors the production path (compile_sql -> validate_guardrails ->
    execute_queries) exactly as the rule-2 and rule-3 live tests do: every
    compiled statement is first approved by ``db.guardrails.validate_sql``
    against the live schema, and only the guardrail-approved SQL is executed
    -- on the read-only connection. Expected values come from independent
    reference queries on the same connection, never assumed from the compiled
    SQL.
    """

    RULE = RuleName.PRODUCT_EXCLUDE_NONPRODUCT

    @classmethod
    def setUpClass(cls):
        cls.conn = connect_readonly(_DB_PATH)
        cls.schema = _read_schema()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def _compile(self, filters: dict | None) -> tuple[str, str]:
        """Compile the rule-4 query and return validated main/companion SQL."""
        state = _compile(
            aggregation="avg",
            metric="unit_price",
            filters=filters,
            rules=[self.RULE],
        )
        main_passed, main_reason, main_sql, _ = validate_sql(
            state.sql_main, self.schema
        )
        self.assertTrue(
            main_passed, f"guardrail rejected main query: {main_reason}"
        )
        companion = state.sql_companions[self.RULE]
        comp_passed, comp_reason, comp_sql, _ = validate_sql(
            companion.sql, self.schema
        )
        self.assertTrue(
            comp_passed, f"guardrail rejected companion query: {comp_reason}"
        )
        return main_sql, comp_sql

    def _reference_avg(self, filters_sql: str) -> float:
        """AVG over the same filters plus the product-only scope."""
        where = (
            f"{filters_sql} AND line_item_type = 'product'"
            if filters_sql
            else "line_item_type = 'product'"
        )
        return self.conn.execute(
            f"SELECT AVG(unit_price) FROM transactions WHERE {where}"
        ).fetchone()[0]

    def _reference_count(self, filters_sql: str) -> int:
        where = (
            f"{filters_sql} AND line_item_type <> 'product'"
            if filters_sql
            else "line_item_type <> 'product'"
        )
        return self.conn.execute(
            f"SELECT COUNT(*) FROM transactions WHERE {where}"
        ).fetchone()[0]

    def test_uk_country_filter_main_and_companion_execute_and_match(self):
        filters_sql = "country = 'United Kingdom'"
        main_sql, comp_sql = self._compile(filters={"country": "United Kingdom"})

        main_avg = self.conn.execute(main_sql).fetchone()[0]
        # The negation must have real effect: the UK data contains fee and
        # adjustment rows whose unit-price profile differs from the product
        # population, so the product-only average differs from the raw one.
        avg_all = self.conn.execute(
            f"SELECT AVG(unit_price) FROM transactions WHERE {filters_sql}"
        ).fetchone()[0]
        avg_products = self._reference_avg(filters_sql)
        self.assertNotAlmostEqual(main_avg, avg_all, places=6)
        self.assertAlmostEqual(main_avg, avg_products, places=6)

        excluded = self.conn.execute(comp_sql).fetchone()[0]
        expected_excluded = self._reference_count(filters_sql)
        self.assertEqual(excluded, expected_excluded)
        self.assertGreater(excluded, 0)

    def test_unfiltered_average_and_companion_execute_and_match(self):
        main_sql, comp_sql = self._compile(filters=None)

        main_avg = self.conn.execute(main_sql).fetchone()[0]
        self.assertAlmostEqual(main_avg, self._reference_avg(""), places=6)

        excluded = self.conn.execute(comp_sql).fetchone()[0]
        expected_excluded = self._reference_count("")
        self.assertEqual(excluded, expected_excluded)
        self.assertGreater(excluded, 0)

    def test_companion_keeps_unrelated_country_filter_against_real_data(self):
        # The companion must count excluded rows for the *same* country the
        # main query scoped, not every non-product row in the table.
        filters_sql = "country = 'United Kingdom'"
        _main_sql, comp_sql = self._compile(filters={"country": "United Kingdom"})

        excluded = self.conn.execute(comp_sql).fetchone()[0]
        expected_excluded = self._reference_count(filters_sql)
        self.assertEqual(excluded, expected_excluded)
        total_nonproduct = self._reference_count("")
        self.assertLess(excluded, total_nonproduct)
        self.assertGreater(excluded, 0)


@unittest.skipUnless(
    _DB_PATH.exists(), f"real database not present at {_DB_PATH}"
)
class CompileSqlAvgAndCustomerExcludeNullLiveDbTest(unittest.TestCase):
    """Run the rules-2+3 compiled queries through the guardrail on the real DB.

    The combined case compiles *three* statements -- the main query carrying
    both negations, plus one companion per fired rule. All three are validated
    against the live schema and executed on the read-only connection, with
    expected values computed by independent reference queries.
    """

    RULES = [
        RuleName.AVG_EXCLUDE_ZERO_PRICE,
        RuleName.CUSTOMER_EXCLUDE_NULL,
    ]

    @classmethod
    def setUpClass(cls):
        cls.conn = connect_readonly(_DB_PATH)
        cls.schema = _read_schema()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def _compile(self, filters: dict | None) -> tuple[str, str, str]:
        """Compile the rules-2+3 query and validate all three statements.

        Returns the guardrail-approved SQL in fixed order: (main query,
        rule-2 companion, rule-3 companion).
        """
        state = _compile(
            aggregation="avg",
            metric="unit_price",
            filters=filters,
            rules=list(self.RULES),
        )
        sqls = [
            ("main query", state.sql_main),
            (
                "rule-2 companion",
                state.sql_companions[self.RULES[0]].sql,
            ),
            (
                "rule-3 companion",
                state.sql_companions[self.RULES[1]].sql,
            ),
        ]
        validated: list[str] = []
        for label, sql in sqls:
            passed, reason, enforced_sql, _ = validate_sql(sql, self.schema)
            self.assertTrue(
                passed, f"guardrail rejected {label}: {reason}"
            )
            validated.append(enforced_sql)
        return validated[0], validated[1], validated[2]

    def test_both_exclusions_apply_and_both_companions_match_reference(self):
        filters_sql = "country = 'United Kingdom'"
        main_sql, comp_zero_sql, comp_null_sql = self._compile(
            filters={"country": "United Kingdom"}
        )

        # The main average must reflect *both* exclusions at once: zero-price
        # rows and null-customer rows are both out of scope.
        main_avg = self.conn.execute(main_sql).fetchone()[0]
        expected_main = self.conn.execute(
            f"SELECT AVG(unit_price) FROM transactions WHERE {filters_sql} "
            "AND unit_price != 0 AND customer_id IS NOT NULL"
        ).fetchone()[0]
        self.assertAlmostEqual(main_avg, expected_main, places=6)

        # Averages computed under only one of the two exclusions must differ
        # from the both-exclusions result, proving the composition is not
        # silently dropping either rule.
        avg_only_zero_free = self.conn.execute(
            f"SELECT AVG(unit_price) FROM transactions WHERE {filters_sql} "
            "AND unit_price != 0"
        ).fetchone()[0]
        self.assertNotAlmostEqual(main_avg, avg_only_zero_free, places=6)
        avg_only_known_customers = self.conn.execute(
            f"SELECT AVG(unit_price) FROM transactions WHERE {filters_sql} "
            "AND customer_id IS NOT NULL"
        ).fetchone()[0]
        self.assertNotAlmostEqual(main_avg, avg_only_known_customers, places=6)

        # Rule-2 companion: the zero-price rows under the base filter.
        excluded_zero = self.conn.execute(comp_zero_sql).fetchone()[0]
        expected_zero = self.conn.execute(
            f"SELECT COUNT(*) FROM transactions WHERE {filters_sql} "
            "AND unit_price = 0"
        ).fetchone()[0]
        self.assertEqual(excluded_zero, expected_zero)
        self.assertGreater(excluded_zero, 0)

        # Rule-3 companion: the null-customer rows under the base filter.
        excluded_null = self.conn.execute(comp_null_sql).fetchone()[0]
        expected_null = self.conn.execute(
            f"SELECT COUNT(*) FROM transactions WHERE {filters_sql} "
            "AND customer_id IS NULL"
        ).fetchone()[0]
        self.assertEqual(excluded_null, expected_null)
        self.assertGreater(excluded_null, 0)


@unittest.skipUnless(
    _DB_PATH.exists(), f"real database not present at {_DB_PATH}"
)
class CompileSqlThreeExclusionsLiveDbTest(unittest.TestCase):
    """Run the rules-2+3+4 compiled queries through the guardrail on the real DB.

    The full combination compiles *four* statements -- the main query carrying
    all three negations, plus one companion per fired rule. All four are
    validated against the live schema and executed on the read-only
    connection, with expected values computed by independent reference
    queries.
    """

    RULES = [
        RuleName.AVG_EXCLUDE_ZERO_PRICE,
        RuleName.CUSTOMER_EXCLUDE_NULL,
        RuleName.PRODUCT_EXCLUDE_NONPRODUCT,
    ]

    @classmethod
    def setUpClass(cls):
        cls.conn = connect_readonly(_DB_PATH)
        cls.schema = _read_schema()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def _compile(self, filters: dict | None) -> tuple[str, str, str, str]:
        """Compile the 2+3+4 query and validate all four statements.

        Returns the guardrail-approved SQL in fixed order: (main query, rule-2
        companion, rule-3 companion, rule-4 companion).
        """
        state = _compile(
            aggregation="avg",
            metric="unit_price",
            filters=filters,
            rules=list(self.RULES),
        )
        sqls = [
            ("main query", state.sql_main),
            ("rule-2 companion", state.sql_companions[self.RULES[0]].sql),
            ("rule-3 companion", state.sql_companions[self.RULES[1]].sql),
            ("rule-4 companion", state.sql_companions[self.RULES[2]].sql),
        ]
        validated: list[str] = []
        for label, sql in sqls:
            passed, reason, enforced_sql, _ = validate_sql(sql, self.schema)
            self.assertTrue(
                passed, f"guardrail rejected {label}: {reason}"
            )
            validated.append(enforced_sql)
        return validated[0], validated[1], validated[2], validated[3]

    def test_all_three_exclusions_apply_and_all_three_companions_match_reference(
        self,
    ):
        filters_sql = "country = 'United Kingdom'"
        (
            main_sql,
            comp_zero_sql,
            comp_null_sql,
            comp_nonproduct_sql,
        ) = self._compile(filters={"country": "United Kingdom"})

        # The main average must reflect all three exclusions at once:
        # zero-price rows, null-customer rows, and non-product rows are all
        # out of scope.
        main_avg = self.conn.execute(main_sql).fetchone()[0]
        expected_main = self.conn.execute(
            f"SELECT AVG(unit_price) FROM transactions WHERE {filters_sql} "
            "AND unit_price != 0 AND customer_id IS NOT NULL "
            "AND line_item_type = 'product'"
        ).fetchone()[0]
        self.assertAlmostEqual(main_avg, expected_main, places=6)

        # Averages computed under only two of the three exclusions must differ
        # from the all-three result, proving the composition is not silently
        # dropping any of the three rules.
        avg_without_zero_exclusion = self.conn.execute(
            f"SELECT AVG(unit_price) FROM transactions WHERE {filters_sql} "
            "AND customer_id IS NOT NULL AND line_item_type = 'product'"
        ).fetchone()[0]
        self.assertNotAlmostEqual(
            main_avg, avg_without_zero_exclusion, places=6
        )
        avg_without_customer_exclusion = self.conn.execute(
            f"SELECT AVG(unit_price) FROM transactions WHERE {filters_sql} "
            "AND unit_price != 0 AND line_item_type = 'product'"
        ).fetchone()[0]
        self.assertNotAlmostEqual(
            main_avg, avg_without_customer_exclusion, places=6
        )
        avg_without_product_exclusion = self.conn.execute(
            f"SELECT AVG(unit_price) FROM transactions WHERE {filters_sql} "
            "AND unit_price != 0 AND customer_id IS NOT NULL"
        ).fetchone()[0]
        self.assertNotAlmostEqual(
            main_avg, avg_without_product_exclusion, places=6
        )

        # Rule-2 companion: the zero-price rows under the base filter.
        excluded_zero = self.conn.execute(comp_zero_sql).fetchone()[0]
        expected_zero = self.conn.execute(
            f"SELECT COUNT(*) FROM transactions WHERE {filters_sql} "
            "AND unit_price = 0"
        ).fetchone()[0]
        self.assertEqual(excluded_zero, expected_zero)
        self.assertGreater(excluded_zero, 0)

        # Rule-3 companion: the null-customer rows under the base filter.
        excluded_null = self.conn.execute(comp_null_sql).fetchone()[0]
        expected_null = self.conn.execute(
            f"SELECT COUNT(*) FROM transactions WHERE {filters_sql} "
            "AND customer_id IS NULL"
        ).fetchone()[0]
        self.assertEqual(excluded_null, expected_null)
        self.assertGreater(excluded_null, 0)

        # Rule-4 companion: the non-product rows under the base filter.
        excluded_nonproduct = self.conn.execute(comp_nonproduct_sql).fetchone()[0]
        expected_nonproduct = self.conn.execute(
            f"SELECT COUNT(*) FROM transactions WHERE {filters_sql} "
            "AND line_item_type <> 'product'"
        ).fetchone()[0]
        self.assertEqual(excluded_nonproduct, expected_nonproduct)
        self.assertGreater(excluded_nonproduct, 0)


@unittest.skipUnless(
    _DB_PATH.exists(), f"real database not present at {_DB_PATH}"
)
class CompileSqlNetVsGrossLiveDbTest(unittest.TestCase):
    """Run the compiled rule-1 NET_VS_GROSS queries through the guardrail.

    Mirrors the production path (compile_sql -> validate_guardrails ->
    execute_queries): every compiled statement is first approved by
    ``db.guardrails.validate_sql`` against the live schema, and only the
    guardrail-approved SQL is executed -- on the read-only connection, exactly
    as ``execute_queries`` will. NET_VS_GROSS produces no companion query, so
    the assertions are about which scope the single main query measured, with
    expected values from independent reference queries.
    """

    RULE = RuleName.NET_VS_GROSS

    @classmethod
    def setUpClass(cls):
        cls.conn = connect_readonly(_DB_PATH)
        cls.schema = _read_schema()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def _compile(self, net_gross: str, filters: dict | None) -> str:
        """Compile the rule-1 revenue query and validate the single main SQL."""
        state = _compile(
            aggregation="sum",
            metric="revenue",
            filters=filters,
            rules=[self.RULE],
            net_gross=net_gross,
        )
        # Rule 1 never produces a companion query -- there is nothing to count.
        self.assertEqual(state.sql_companions, {})
        passed, reason, sql, _ = validate_sql(state.sql_main, self.schema)
        self.assertTrue(passed, f"guardrail rejected main query: {reason}")
        return sql

    def _reference_revenue(self, filters_sql: str, scope_sql: str) -> float:
        """Independent ``SUM(quantity * unit_price)`` over the requested scope."""
        where = " AND ".join(
            [part for part in (filters_sql, scope_sql) if part]
        )
        if not where:
            return self.conn.execute(
                "SELECT SUM(quantity * unit_price) FROM transactions"
            ).fetchone()[0]
        return self.conn.execute(
            "SELECT SUM(quantity * unit_price) FROM transactions "
            f"WHERE {where}"
        ).fetchone()[0]

    def test_net_variant_matches_the_plain_base_revenue(self):
        main_sql = self._compile("net", filters={"country": "United Kingdom"})
        main = self.conn.execute(main_sql).fetchone()[0]
        expected = self._reference_revenue("country = 'United Kingdom'", "")
        self.assertAlmostEqual(main, expected, places=6)

    def test_gross_of_cancellations_executes_and_matches_reference(self):
        filters_sql = "country = 'United Kingdom'"
        main_sql = self._compile(
            "gross_of_cancellations", filters={"country": "United Kingdom"}
        )
        main = self.conn.execute(main_sql).fetchone()[0]
        expected = self._reference_revenue(
            filters_sql, "invoice_id NOT LIKE 'C%'"
        )
        self.assertAlmostEqual(main, expected, places=6)

        # The gross scope must have real effect: excluding the C-flagged
        # cancelled invoices removes their negative line amounts from the sum,
        # so the gross total differs from the net total over the same country.
        net = self.conn.execute(
            self._compile("net", filters={"country": "United Kingdom"})
        ).fetchone()[0]
        self.assertNotAlmostEqual(main, net, places=6)

    def test_returns_variant_executes_and_matches_reference(self):
        filters_sql = "country = 'United Kingdom'"
        main_sql = self._compile("returns", filters={"country": "United Kingdom"})
        main = self.conn.execute(main_sql).fetchone()[0]
        expected = self._reference_revenue(filters_sql, "quantity < 0")
        self.assertAlmostEqual(main, expected, places=6)
        # The returns scope selects only negative-quantity rows, so its revenue
        # sum is negative -- a genuinely different measurement from net/gross.
        self.assertLess(main, 0)


@unittest.skipUnless(
    _DB_PATH.exists(), f"real database not present at {_DB_PATH}"
)
class CompileSqlNetVsGrossAndZeroPriceLiveDbTest(unittest.TestCase):
    """Run the rule-1 + rule-2 combination through the guardrail on the real DB.

    Compiles the user-visible composition example (gross revenue excluding
    zero-price rows): one main query carrying both the gross-of-cancellations
    scope and the rule-2 negation, plus exactly one companion query -- for the
    exclusion rule only. Every statement is guardrail-approved and executed on
    the read-only connection; expected values come from independent reference
    queries.
    """

    RULES = [
        RuleName.NET_VS_GROSS,
        RuleName.AVG_EXCLUDE_ZERO_PRICE,
    ]
    COMPANION_RULE = RuleName.AVG_EXCLUDE_ZERO_PRICE

    @classmethod
    def setUpClass(cls):
        cls.conn = connect_readonly(_DB_PATH)
        cls.schema = _read_schema()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def _compile(self, filters: dict | None) -> tuple[str, str]:
        """Compile the combined query and validate main + companion SQL."""
        state = _compile(
            aggregation="sum",
            metric="revenue",
            filters=filters,
            rules=list(self.RULES),
            net_gross="gross_of_cancellations",
        )
        # sql_companions must gain an entry only from the fired exclusion rule;
        # NET_VS_GROSS itself never produces a companion query.
        self.assertEqual(
            set(state.sql_companions), {self.COMPANION_RULE}
        )
        sqls = [
            ("main query", state.sql_main),
            ("rule-2 companion", state.sql_companions[self.COMPANION_RULE].sql),
        ]
        validated: list[str] = []
        for label, sql in sqls:
            passed, reason, enforced_sql, _ = validate_sql(sql, self.schema)
            self.assertTrue(passed, f"guardrail rejected {label}: {reason}")
            validated.append(enforced_sql)
        return validated[0], validated[1]

    def test_gross_revenue_excluding_zero_price_rows_executes_and_matches(self):
        filters_sql = "country = 'United Kingdom'"
        main_sql, comp_sql = self._compile(
            filters={"country": "United Kingdom"}
        )

        # The main total must reflect *both* the gross scope and the zero-price
        # exclusion at once.
        main = self.conn.execute(main_sql).fetchone()[0]
        expected_main = self.conn.execute(
            "SELECT SUM(quantity * unit_price) FROM transactions "
            f"WHERE {filters_sql} AND invoice_id NOT LIKE 'C%' "
            "AND unit_price != 0"
        ).fetchone()[0]
        self.assertAlmostEqual(main, expected_main, places=6)

        # The rule-2 companion still counts the zero-price rows under the base
        # filter alone -- companions never inherit the NET_VS_GROSS scope.
        excluded = self.conn.execute(comp_sql).fetchone()[0]
        expected_excluded = self.conn.execute(
            f"SELECT COUNT(*) FROM transactions WHERE {filters_sql} "
            "AND unit_price = 0"
        ).fetchone()[0]
        self.assertEqual(excluded, expected_excluded)
        self.assertGreater(excluded, 0)

    def test_composition_effect_is_visible_on_a_rule_2_sensitive_metric(self):
        # On a revenue SUM the zero-price exclusion is invisible: zero-price
        # rows contribute exactly 0 to SUM(quantity * unit_price), so removing
        # them cannot change the total. Prove the AND-composition really
        # carries rule 2's negation by measuring AVG(unit_price) -- the metric
        # rule 2 is defined for: gross scope alone (which still includes the
        # zero-price UK rows on non-cancelled invoices) must differ from gross
        # scope AND-composed with the zero-price exclusion.
        filters = {"country": "United Kingdom"}
        state = _compile(
            aggregation="avg",
            metric="unit_price",
            filters=filters,
            rules=list(self.RULES),
            net_gross="gross_of_cancellations",
        )
        # sql_companions must gain an entry only from the fired exclusion rule;
        # NET_VS_GROSS itself never produces a companion query.
        self.assertEqual(set(state.sql_companions), {self.COMPANION_RULE})
        passed, reason, main_sql, _ = validate_sql(state.sql_main, self.schema)
        self.assertTrue(passed, f"guardrail rejected main query: {reason}")
        comp_passed, comp_reason, comp_sql, _ = validate_sql(
            state.sql_companions[self.COMPANION_RULE].sql, self.schema
        )
        self.assertTrue(
            comp_passed, f"guardrail rejected companion query: {comp_reason}"
        )

        combined_avg = self.conn.execute(main_sql).fetchone()[0]
        expected_combined = self.conn.execute(
            "SELECT AVG(unit_price) FROM transactions "
            "WHERE country = 'United Kingdom' AND invoice_id NOT LIKE 'C%' "
            "AND unit_price != 0"
        ).fetchone()[0]
        self.assertAlmostEqual(combined_avg, expected_combined, places=6)

        gross_only_avg = self.conn.execute(
            "SELECT AVG(unit_price) FROM transactions "
            "WHERE country = 'United Kingdom' AND invoice_id NOT LIKE 'C%'"
        ).fetchone()[0]
        self.assertNotAlmostEqual(combined_avg, gross_only_avg, places=6)

        # The rule-2 companion still counts the zero-price rows under the base
        # filter alone -- companions never inherit the NET_VS_GROSS scope.
        excluded = self.conn.execute(comp_sql).fetchone()[0]
        expected_excluded = self.conn.execute(
            f"SELECT COUNT(*) FROM transactions WHERE country = 'United Kingdom' "
            "AND unit_price = 0"
        ).fetchone()[0]
        self.assertEqual(excluded, expected_excluded)
        self.assertGreater(excluded, 0)


class CompileSqlEarlyValidationAggregationMetricTest(unittest.TestCase):
    """Gate 1: SUM/AVG are only defined for the numeric measure metrics.

    Rejection now happens in two layers. ``QueryIntent.metric`` is a
    ``Literal`` restricted to the canonical metric names (``revenue``,
    ``quantity``, ``unit_price``, ``customer_id``), so a text/dimension column
    such as ``country`` or ``description`` cannot even be expressed as a
    metric -- Pydantic raises a ``pydantic.ValidationError`` at construction,
    before ``compile_sql`` ever runs. Gate 1 in ``compile_sql`` is therefore
    left with exactly the case the schema cannot express away: SUM/AVG over a
    canonical metric that is nonetheless not a sensible measure -- a
    ``customer_id`` that is stored REAL yet is not sensible to sum or average
    -- rejected as ``invalid_intent:aggregation_metric_mismatch``.
    """

    REASON = "invalid_intent:aggregation_metric_mismatch"

    def _assert_rejected_by_compile_sql(self, aggregation: str, metric: str) -> None:
        state = _compile(aggregation=aggregation, metric=metric)
        self.assertEqual(state.error, self.REASON)
        self.assertIsNone(state.sql_main)
        self.assertEqual(state.sql_companions, {})

    def _assert_rejected_by_schema(self, aggregation: str, metric: str) -> None:
        # The schema -- not compile_sql's Gate 1 -- is the enforcement layer:
        # a non-canonical metric string fails validation the moment the intent
        # is built. Assert the single failing field is ``metric``.
        with self.assertRaises(pydantic.ValidationError) as ctx:
            QueryIntent(aggregation=aggregation, metric=metric)
        self.assertEqual(
            [error["loc"] for error in ctx.exception.errors()], [("metric",)]
        )

    def test_avg_over_text_dimension_metric_is_rejected(self):
        # AVG over a country name: "country" is not one of the canonical
        # metric names, so the schema rejects the intent at construction time.
        self._assert_rejected_by_schema("avg", "country")

    def test_sum_over_free_text_metric_is_rejected(self):
        # SUM over a description: "description" is not one of the canonical
        # metric names, so the schema rejects the intent at construction time.
        self._assert_rejected_by_schema("sum", "description")

    def test_avg_over_numeric_identifier_column_is_rejected(self):
        # customer_id is a valid canonical metric (it is in the Literal) and
        # is stored REAL, but an identifier is not a measure: averaging it is
        # exactly the "valid enum value yet not sensible to average" case that
        # is now Gate 1's remaining job.
        self._assert_rejected_by_compile_sql("avg", "customer_id")

    def test_sum_over_amount_metrics_still_compiles(self):
        # Positive control on the measure boundary: SUM(revenue) (the proven
        # base-case shape) and SUM(quantity) must keep compiling -- the gate
        # must not over-reject the queries already proven correct.
        for metric in ("revenue", "quantity"):
            with self.subTest(metric=metric):
                state = _compile(aggregation="sum", metric=metric)
                self.assertIsNone(state.error)
                self.assertIsNotNone(state.sql_main)
                self.assertEqual(state.sql_companions, {})


@unittest.skipUnless(
    _DB_PATH.exists(), f"real database not present at {_DB_PATH}"
)
class CompileSqlEarlyValidationGroupByTest(unittest.TestCase):
    """Gate 2: group-by fields must exist in the live schema and be dimensions.

    Existence is introspected from the real database at runtime (never from a
    duplicated hardcoded column list), and a numeric measure column such as
    ``quantity`` is not a legitimate grouping dimension. Both failures produce
    ``invalid_intent:invalid_group_by`` and no SQL. These tests open the live
    database exactly as the production path does, so they are skipped when the
    database is absent like the other live-DB classes.
    """

    REASON = "invalid_intent:invalid_group_by"

    def test_group_by_column_absent_from_live_schema_is_rejected(self):
        state = _compile(group_by=["customer_name"])
        self.assertEqual(state.error, self.REASON)
        self.assertIsNone(state.sql_main)
        self.assertEqual(state.sql_companions, {})

    def test_group_by_measure_column_is_rejected_as_not_a_dimension(self):
        # quantity exists in the live schema but is a numeric measure, not a
        # grouping dimension.
        state = _compile(group_by=["quantity"])
        self.assertEqual(state.error, self.REASON)
        self.assertIsNone(state.sql_main)
        self.assertEqual(state.sql_companions, {})


class CompileSqlEarlyValidationDateRangeTest(unittest.TestCase):
    """Gate 3a: a date-range filter with start after end is a hard failure.

    A dict-valued filter on a column is the date-range encoding
    (``{start, end}`` ISO-8601 endpoints). Reversed ranges fail with
    ``invalid_intent:invalid_date_range`` before any SQL is constructed --
    never silently swapped. This gate is pure (no live-database read), so it
    runs even where the database is absent.
    """

    REASON = "invalid_intent:invalid_date_range"

    def _assert_rejected(self, start: str, end: str) -> None:
        state = _compile(
            filters={"invoice_timestamp": {"start": start, "end": end}}
        )
        self.assertEqual(state.error, self.REASON)
        self.assertIsNone(state.sql_main)
        self.assertEqual(state.sql_companions, {})

    def test_reversed_date_only_range_is_rejected(self):
        self._assert_rejected("2010-01-01", "2009-01-01")

    def test_reversed_iso_datetime_range_is_rejected(self):
        # Ordering broken in the full ISO-8601 datetime spelling the schema's
        # invoice_timestamp column actually stores.
        self._assert_rejected(
            "2010-06-01T07:45:00", "2009-12-01T07:45:00"
        )

    def test_equal_endpoint_range_is_not_rejected_by_the_date_gate(self):
        # start == end is a degenerate but well-ordered range: the reversal
        # gate must let it through. Date-range SQL construction is a separate,
        # later task, so a well-ordered range advances past validation and then
        # hits the compiler's pre-existing unsupported-dict-literal ValueError
        # in _filter_conditions rather than the invalid_date_range gate. (This
        # test should be replaced by a compile-success assertion once date-range
        # SQL construction lands.)
        with self.assertRaises(ValueError):
            _compile(
                filters={
                    "invoice_timestamp": {
                        "start": "2010-01-01",
                        "end": "2010-01-01",
                    }
                }
            )


@unittest.skipUnless(
    _DB_PATH.exists(), f"real database not present at {_DB_PATH}"
)
class CompileSqlEarlyValidationNoMatchingDataTest(unittest.TestCase):
    """Gate 3b: a filter value with no matching real data is a hard failure.

    Deliberate v1 scope decision, not an oversight: there is no fuzzy
    correction or suggestion. A near-miss country name -- and, by extension,
    any genuinely zero-cohort scalar filter value, which the system cannot
    distinguish from a near-miss -- is refused outright with
    ``invalid_intent:no_matching_data``. These tests query the real database,
    so they are skipped when it is absent like the other live-DB classes.
    """

    REASON = "invalid_intent:no_matching_data"

    def test_near_miss_country_name_is_rejected_without_suggestion(self):
        state = _compile(filters={"country": "United Kingdm"})
        self.assertEqual(state.error, self.REASON)
        self.assertIsNone(state.sql_main)
        self.assertEqual(state.sql_companions, {})

    def test_value_with_no_rows_at_all_is_rejected(self):
        state = _compile(filters={"country": "Narnia"})
        self.assertEqual(state.error, self.REASON)
        self.assertIsNone(state.sql_main)
        self.assertEqual(state.sql_companions, {})

    def test_existing_filter_value_still_compiles(self):
        # Positive control: the proven country-filtered shapes keep compiling;
        # only a value with no matching rows is rejected.
        state = _compile(filters={"country": "United Kingdom"})
        self.assertIsNone(state.error)
        self.assertIsNotNone(state.sql_main)


class CompileSqlEarlyValidationRuleMismatchTest(unittest.TestCase):
    """Gate 4: applicable_rules and query_intent must agree structurally.

    The inert ``net_gross`` field only takes meaning when rule 1
    (NET_VS_GROSS) fires. A non-default variant with that rule absent means a
    bug upstream in rule detection, so ``compile_sql`` fails loudly with
    ``invalid_intent:rule_mismatch`` instead of silently ignoring the dead
    field. This gate is pure (no live-database read).
    """

    REASON = "invalid_intent:rule_mismatch"

    def test_gross_of_cancellations_without_net_vs_gross_is_rejected(self):
        state = _compile(net_gross="gross_of_cancellations")
        self.assertEqual(state.error, self.REASON)
        self.assertIsNone(state.sql_main)
        self.assertEqual(state.sql_companions, {})

    def test_returns_variant_without_net_vs_gross_is_rejected(self):
        state = _compile(net_gross="returns")
        self.assertEqual(state.error, self.REASON)
        self.assertIsNone(state.sql_main)
        self.assertEqual(state.sql_companions, {})

    def test_non_default_variant_with_net_vs_gross_present_still_compiles(self):
        # Positive control: the same non-default variant is valid the moment
        # the rule that gives it meaning is actually present (the proven
        # NET_VS_GROSS rule-1 shapes).
        state = _compile(
            net_gross="gross_of_cancellations",
            rules=[RuleName.NET_VS_GROSS],
        )
        self.assertIsNone(state.error)
        self.assertIsNotNone(state.sql_main)
        self.assertEqual(state.sql_companions, {})

    def test_default_net_variant_without_any_rule_is_still_valid(self):
        # Default net_gross is inert by design: every proven base-case query
        # (no rules at all) carries it and must keep compiling.
        state = _compile(net_gross="net")
        self.assertIsNone(state.error)
        self.assertIsNotNone(state.sql_main)


if __name__ == "__main__":
    unittest.main()
