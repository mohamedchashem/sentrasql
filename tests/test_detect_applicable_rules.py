"""Tests for graph.node_detect_applicable_rules.detect_applicable_rules (Node 3).

detect_applicable_rules is deterministic and LLM-free: it reads
``state.query_intent``, mechanically checks it against the policy rule set, and
populates ``state.applicable_rules`` with the rules that fire.

Rule scope: all four policy rules fire. ``NET_VS_GROSS`` (rule 1),
``AVG_EXCLUDE_ZERO_PRICE`` (rule 2), ``CUSTOMER_EXCLUDE_NULL`` (rule 3), and
``PRODUCT_EXCLUDE_NONPRODUCT`` (rule 4).

``NET_VS_GROSS`` fires when either:

* (a) ``aggregation == "sum"`` and ``metric`` is ``"revenue"`` or
  ``"quantity"`` -- a SUM total over an amount that carries the net/gross
  distinction (which defaults to "net", so the rule must resolve which variant
  applies); or
* (b) ``query_intent.net_gross != "net"`` -- an explicit non-default variant
  request (``gross_of_cancellations`` or ``returns``) -- regardless of
  aggregation/metric.

``AVG_EXCLUDE_ZERO_PRICE`` fires when ``aggregation == "avg"`` and ``metric``
is a price-derived metric -- ``"unit_price"`` or ``"revenue"`` -- because a
zero-price row contributes 0 to either average and distorts it (DESIGN_LOG.md
section 13). ``metric == "quantity"`` is the deliberate exclusion: a zero-price
row still has a real, non-zero quantity, so quantity averages aren't distorted
and must NOT fire this rule.

``CUSTOMER_EXCLUDE_NULL`` fires when ``query_intent.group_by`` contains
``"customer_id"`` -- membership anywhere in the list, no matter how many other
columns group beside it -- because a customer-grouped query would otherwise
surface a spurious "no customer" bucket for NULL ``customer_id`` rows
(DESIGN_LOG.md section 4.2, policy rule 3). A group_by naming only other
columns (or no group_by at all) never fires it.

``PRODUCT_EXCLUDE_NONPRODUCT`` fires when ``query_intent.group_by`` contains
``"stock_code"`` -- the same membership-anywhere rule as ``CUSTOMER_EXCLUDE_NULL``
-- because grouping by the product dimension (a product-ranking query)
aggregates across a mixed population of product and non-product line items
(fees, adjustments, vouchers share the transactions table), so the non-product
rows must not surface as pseudo-products in the ranked result (DESIGN_LOG.md
section 4.2, policy rule 4). It does NOT fire when the intent only *filters*
on ``stock_code`` (via ``query_intent.filters``) without grouping by it: a
single filtered stock code denotes one homogeneous product-or-non-product
population, so the mixed-population ambiguity this rule resolves never arises.
A group_by naming only other columns (or no group_by at all) never fires it.

Condition (b) is load-bearing downstream: ``compile_sql``'s
``invalid_intent:rule_mismatch`` gate hard-fails any intent carrying a
non-default ``net_gross`` while rule 1 is absent. Because condition (b) is
independent of aggregation/metric, rule 1 and rule 2 can fire on the same
intent (e.g. ``avg`` over ``unit_price`` with ``net_gross="returns"``), and
``CUSTOMER_EXCLUDE_NULL`` and ``PRODUCT_EXCLUDE_NONPRODUCT`` compose freely
with both and with each other -- adding ``group_by=["customer_id",
"stock_code"]`` to that avg/unit_price + ``"returns"`` intent fires all four
rules at once. The tests below prove these rules compose rather than being
mutually exclusive, including that fully representable four-rule combination.
The integration tests at the bottom run the exact Node 3 -> Node 4 sequence
(``detect_applicable_rules`` then ``compile_sql``) to prove a firing intent
flows through without ever hitting that gate.
"""

import unittest

from graph.node_compile_sql import compile_sql
from graph.node_detect_applicable_rules import detect_applicable_rules
from graph.state import GraphState, QueryIntent, RuleName

_NET_VS_GROSS = RuleName.NET_VS_GROSS
_AVG_EXCLUDE_ZERO_PRICE = RuleName.AVG_EXCLUDE_ZERO_PRICE
_CUSTOMER_EXCLUDE_NULL = RuleName.CUSTOMER_EXCLUDE_NULL
_PRODUCT_EXCLUDE_NONPRODUCT = RuleName.PRODUCT_EXCLUDE_NONPRODUCT
_RULE_MISMATCH_REASON = "invalid_intent:rule_mismatch"


def _build_state(
    aggregation: str = "sum",
    metric: str = "revenue",
    group_by: list[str] | None = None,
    filters: dict | None = None,
    net_gross: str = "net",
) -> GraphState:
    """Build a GraphState whose query_intent carries the requested fields."""
    return GraphState(
        raw_query="test query",
        query_intent=QueryIntent(
            aggregation=aggregation,
            metric=metric,
            group_by=group_by,
            filters=filters or {},
            net_gross=net_gross,
        ),
    )


def _detect(**intent_kwargs) -> GraphState:
    """Run Node 3 alone on a fresh intent; return the resulting state."""
    return detect_applicable_rules(_build_state(**intent_kwargs))


class DetectApplicableRulesNetVsGrossTest(unittest.TestCase):
    """Node 3 fires NET_VS_GROSS exactly per its two structural conditions."""

    def test_sum_over_revenue_fires_net_vs_gross(self):
        state = _detect(aggregation="sum", metric="revenue")
        self.assertEqual(state.applicable_rules, [_NET_VS_GROSS])

    def test_sum_over_quantity_fires_net_vs_gross(self):
        state = _detect(aggregation="sum", metric="quantity")
        self.assertEqual(state.applicable_rules, [_NET_VS_GROSS])

    def test_non_default_net_gross_fires_despite_non_matching_aggregation_metric(
        self,
    ):
        # avg over quantity with the default net_gross fires no rule (see the
        # negative tests below), but the moment net_gross is non-default rule 1
        # fires regardless -- condition (b) is independent of aggregation and
        # metric. Metric "quantity" is used (not "unit_price"/"revenue") so
        # rule 2 stays out too, isolating condition (b) as the only trigger
        # under test.
        for variant in ("gross_of_cancellations", "returns"):
            with self.subTest(variant=variant):
                state = _detect(
                    aggregation="avg",
                    metric="quantity",
                    net_gross=variant,
                )
                self.assertEqual(state.applicable_rules, [_NET_VS_GROSS])

    def test_default_net_gross_with_non_matching_aggregation_metric_does_not_fire(
        self,
    ):
        # avg over quantity with the default "net" matches neither of rule 1's
        # conditions -- the aggregation is not "sum", and net_gross is the
        # default "net" -- and quantity is also not a rule-2 price-derived
        # metric, so no rule may fire.
        state = _detect(aggregation="avg", metric="quantity")
        self.assertEqual(state.applicable_rules, [])

    def test_sum_over_unit_price_with_default_net_gross_does_not_fire(self):
        # Condition (a) names exactly "revenue" and "quantity": a SUM over the
        # other numeric measure metric must not fire rule 1 on the metric alone
        # -- and rule 2 only fires on AVG, so this intent stays rule-free.
        state = _detect(aggregation="sum", metric="unit_price")
        self.assertEqual(state.applicable_rules, [])

    def test_replaces_stale_applicable_rules_instead_of_appending(self):
        # The node is deterministic and must recompute applicable_rules from
        # scratch, replacing -- never growing -- whatever a previous pass left.
        # A no-rule intent (avg over quantity, the rule-2 exclusion) must wipe
        # both a stale rule 1 and a stale rule 2...
        non_firing_state = _build_state(aggregation="avg", metric="quantity")
        non_firing_state.applicable_rules = [
            _NET_VS_GROSS,
            _AVG_EXCLUDE_ZERO_PRICE,
        ]  # stale
        non_firing_state = detect_applicable_rules(non_firing_state)
        self.assertEqual(non_firing_state.applicable_rules, [])

        # ...and a rule-1-only intent (a returns SUM over revenue) must drop a
        # stale rule 2 that no longer applies, leaving exactly rule 1.
        firing_state = _build_state(net_gross="returns")
        firing_state.applicable_rules = [_AVG_EXCLUDE_ZERO_PRICE]  # stale
        firing_state = detect_applicable_rules(firing_state)
        self.assertEqual(firing_state.applicable_rules, [_NET_VS_GROSS])

    def test_none_query_intent_leaves_state_unchanged(self):
        # Node 2 (extract_query_intent) is still a stub, so a None intent must
        # be survivable: the node is inert and returns state exactly as-is.
        state = GraphState(raw_query="test query", query_intent=None)
        result = detect_applicable_rules(state)
        self.assertIs(result, state)
        self.assertIsNone(result.query_intent)
        self.assertEqual(result.applicable_rules, [])


class DetectApplicableRulesAvgExcludeZeroPriceTest(unittest.TestCase):
    """Node 3 fires AVG_EXCLUDE_ZERO_PRICE exactly per its structural trigger.

    An AVG over a price-derived metric (``unit_price`` or ``revenue``) is
    distorted by zero-price rows, so the rule fires; ``quantity`` is the
    deliberate exclusion and stays rule-free.
    """

    def test_avg_over_unit_price_fires_avg_exclude_zero_price(self):
        state = _detect(aggregation="avg", metric="unit_price")
        self.assertEqual(state.applicable_rules, [_AVG_EXCLUDE_ZERO_PRICE])

    def test_avg_over_revenue_fires_avg_exclude_zero_price(self):
        # Revenue is quantity * unit_price, so a zero-price row also produces
        # zero revenue for that transaction: a revenue average is distorted by
        # zero-price rows exactly like a price average (DESIGN_LOG.md section
        # 13's documented extension).
        state = _detect(aggregation="avg", metric="revenue")
        self.assertEqual(state.applicable_rules, [_AVG_EXCLUDE_ZERO_PRICE])

    def test_avg_over_quantity_does_not_fire_avg_exclude_zero_price(self):
        # The deliberate exclusion: a zero-price row still has a real,
        # non-zero quantity (e.g. a free sample), so quantity averages are not
        # distorted by zero-price rows and this rule must not fire. It also
        # fires no other rule, so the intent is rule-free.
        state = _detect(aggregation="avg", metric="quantity")
        self.assertEqual(state.applicable_rules, [])

    def test_net_vs_gross_and_avg_exclude_zero_price_fire_together(self):
        # Rule 1's condition (b) is independent of aggregation and metric, so
        # an avg over a price-derived metric with a non-default net_gross fires
        # BOTH rules at once: the two are independently evaluated, not mutually
        # exclusive. Rule 1 is checked first and listed first, rule 2 second.
        for metric in ("unit_price", "revenue"):
            with self.subTest(metric=metric):
                state = _detect(
                    aggregation="avg",
                    metric=metric,
                    net_gross="returns",
                )
                self.assertEqual(
                    state.applicable_rules,
                    [_NET_VS_GROSS, _AVG_EXCLUDE_ZERO_PRICE],
                )


class DetectApplicableRulesCustomerExcludeNullTest(unittest.TestCase):
    """Node 3 fires CUSTOMER_EXCLUDE_NULL exactly per its structural trigger.

    Any group_by that includes the customer dimension -- alone or alongside
    other columns -- fires the rule; a group_by over only other columns (or no
    group_by at all) never does.
    """

    def test_group_by_customer_id_fires_customer_exclude_null(self):
        # avg over quantity + group_by customer_id fires rule 3 alone: quantity
        # is not a rule-2 price-derived metric and avg is not a rule-1 SUM
        # total, so the null-customer exclusion is the only applicable rule.
        state = _detect(
            aggregation="avg", metric="quantity", group_by=["customer_id"]
        )
        self.assertEqual(state.applicable_rules, [_CUSTOMER_EXCLUDE_NULL])

    def test_group_by_other_columns_does_not_fire_customer_exclude_null(self):
        # Grouping by a different dimension (country) cannot create a "no
        # customer" bucket, so rule 3 must not fire. avg over quantity also
        # keeps rules 1 and 2 out, leaving the intent rule-free.
        state = _detect(
            aggregation="avg", metric="quantity", group_by=["country"]
        )
        self.assertEqual(state.applicable_rules, [])

    def test_no_group_by_does_not_fire_customer_exclude_null(self):
        # With no group_by at all there is no customer grouping whose NULL
        # bucket needs excluding, so rule 3 must not fire.
        state = _detect(aggregation="avg", metric="quantity")
        self.assertEqual(state.applicable_rules, [])

    def test_multi_field_group_by_including_customer_id_still_fires(self):
        # group_by=["country", "customer_id"] fires rule 3 too: membership
        # anywhere in the list -- not a lone single-column group_by -- is what
        # the trigger keys on.
        state = _detect(
            aggregation="avg",
            metric="quantity",
            group_by=["country", "customer_id"],
        )
        self.assertEqual(state.applicable_rules, [_CUSTOMER_EXCLUDE_NULL])

    def test_customer_exclude_null_fires_alongside_net_vs_gross(self):
        # Rule 3 composes with rule 1: grouping the revenue SUM by customer and
        # asking for the "returns" variant fires both (rule 1 via conditions
        # (a) and (b), rule 3 via the customer group).
        state = _detect(
            aggregation="sum",
            metric="revenue",
            group_by=["customer_id"],
            net_gross="returns",
        )
        self.assertEqual(
            state.applicable_rules,
            [_NET_VS_GROSS, _CUSTOMER_EXCLUDE_NULL],
        )

    def test_customer_exclude_null_fires_alongside_avg_exclude_zero_price(self):
        # Rule 3 composes with rule 2: avg over unit_price grouped by customer
        # fires both (rule 2 for the price average, rule 3 for the customer
        # group). Rule 2 is checked first and listed first.
        state = _detect(
            aggregation="avg",
            metric="unit_price",
            group_by=["customer_id"],
        )
        self.assertEqual(
            state.applicable_rules,
            [_AVG_EXCLUDE_ZERO_PRICE, _CUSTOMER_EXCLUDE_NULL],
        )

    def test_customer_exclude_null_fires_alongside_rules_one_and_two(self):
        # All three implemented rules compose: adding a non-default net_gross
        # to the previous avg/unit_price + customer-group intent fires rule 1
        # as well. Order follows rule numbering (1, 2, 3).
        state = _detect(
            aggregation="avg",
            metric="unit_price",
            group_by=["customer_id"],
            net_gross="returns",
        )
        self.assertEqual(
            state.applicable_rules,
            [_NET_VS_GROSS, _AVG_EXCLUDE_ZERO_PRICE, _CUSTOMER_EXCLUDE_NULL],
        )


class DetectApplicableRulesProductExcludeNonproductTest(unittest.TestCase):
    """Node 3 fires PRODUCT_EXCLUDE_NONPRODUCT exactly per its structural trigger.

    Any group_by that includes the product dimension (``stock_code``) -- alone
    or alongside other columns -- fires the rule; a group_by over only other
    columns never does. A query that only filters on ``stock_code`` without
    grouping by it does NOT fire the rule: a single filtered code is one
    homogeneous product-or-non-product population, so no mixed
    product/non-product aggregation is happening for the rule to resolve.
    """

    def test_group_by_stock_code_fires_product_exclude_nonproduct(self):
        # avg over quantity + group_by stock_code fires rule 4 alone: quantity
        # is not a rule-2 price-derived metric and avg is not a rule-1 SUM
        # total, and no customer group fires rule 3, so the non-product
        # exclusion is the only applicable rule.
        state = _detect(
            aggregation="avg", metric="quantity", group_by=["stock_code"]
        )
        self.assertEqual(
            state.applicable_rules, [_PRODUCT_EXCLUDE_NONPRODUCT]
        )

    def test_filter_on_stock_code_without_grouping_does_not_fire(self):
        # A query filtered to a specific stock_code value but NOT grouped by
        # the product dimension does not aggregate across a mixed
        # product/non-product population -- a single stock code is either a
        # product or a non-product line item, never both -- so the ambiguity
        # rule 4 resolves never arises and it must not fire. avg over quantity
        # also keeps rules 1 and 2 out, leaving the intent rule-free.
        state = _detect(
            aggregation="avg",
            metric="quantity",
            filters={"stock_code": "POST"},
        )
        self.assertEqual(state.applicable_rules, [])

    def test_group_by_unrelated_field_does_not_fire_product_exclude_nonproduct(
        self,
    ):
        # Grouping by a different dimension (country) cannot create the mixed
        # product/non-product ranking that rule 4 excludes, so it must not
        # fire. avg over quantity also keeps rules 1 and 2 out, leaving the
        # intent rule-free.
        state = _detect(
            aggregation="avg", metric="quantity", group_by=["country"]
        )
        self.assertEqual(state.applicable_rules, [])

    def test_no_group_by_does_not_fire_product_exclude_nonproduct(self):
        # With no group_by at all there is no product ranking whose
        # non-product rows need excluding, so rule 4 must not fire.
        state = _detect(aggregation="avg", metric="quantity")
        self.assertEqual(state.applicable_rules, [])

    def test_multi_field_group_by_including_stock_code_still_fires(self):
        # group_by=["customer_id", "stock_code"] fires rules 3 AND 4:
        # membership anywhere in the list -- not a lone single-column group_by
        # -- is what each dimension trigger keys on. avg over quantity keeps
        # rules 1 and 2 out, isolating the two group-dimension rules.
        state = _detect(
            aggregation="avg",
            metric="quantity",
            group_by=["customer_id", "stock_code"],
        )
        self.assertEqual(
            state.applicable_rules,
            [_CUSTOMER_EXCLUDE_NULL, _PRODUCT_EXCLUDE_NONPRODUCT],
        )

    def test_product_exclude_nonproduct_fires_alongside_net_vs_gross(self):
        # Rule 4 composes with rule 1: a SUM of revenue grouped by product
        # fires both (rule 1 via its amount-metric condition (a), rule 4 via
        # the stock_code group).
        state = _detect(
            aggregation="sum",
            metric="revenue",
            group_by=["stock_code"],
        )
        self.assertEqual(
            state.applicable_rules,
            [_NET_VS_GROSS, _PRODUCT_EXCLUDE_NONPRODUCT],
        )

    def test_product_exclude_nonproduct_fires_alongside_avg_exclude_zero_price(
        self,
    ):
        # Rule 4 composes with rule 2: avg over a price-derived metric grouped
        # by stock_code fires both (rule 2 for the price average, rule 4 for
        # the product group). Rule 2 is checked first and listed first.
        state = _detect(
            aggregation="avg",
            metric="unit_price",
            group_by=["stock_code"],
        )
        self.assertEqual(
            state.applicable_rules,
            [_AVG_EXCLUDE_ZERO_PRICE, _PRODUCT_EXCLUDE_NONPRODUCT],
        )

    def test_all_four_rules_fire_together(self):
        # All four rules are now representable on one intent: adding a
        # non-default net_gross and a customer group to the previous
        # avg/unit_price + stock_code-group intent fires rule 1 and rule 3 as
        # well. Order follows rule numbering (1, 2, 3, 4).
        state = _detect(
            aggregation="avg",
            metric="unit_price",
            group_by=["customer_id", "stock_code"],
            net_gross="returns",
        )
        self.assertEqual(
            state.applicable_rules,
            [
                _NET_VS_GROSS,
                _AVG_EXCLUDE_ZERO_PRICE,
                _CUSTOMER_EXCLUDE_NULL,
                _PRODUCT_EXCLUDE_NONPRODUCT,
            ],
        )


class DetectApplicableRulesCompileSqlIntegrationTest(unittest.TestCase):
    """Node 3 -> Node 4: a firing intent must never hit the rule_mismatch gate."""

    def _detect_then_compile(self, **intent_kwargs) -> GraphState:
        state = detect_applicable_rules(_build_state(**intent_kwargs))
        return compile_sql(state)

    def test_sum_over_revenue_flows_through_detection_to_compile_sql(self):
        state = self._detect_then_compile(aggregation="sum", metric="revenue")
        self.assertEqual(state.applicable_rules, [_NET_VS_GROSS])
        self.assertIsNone(state.error)
        self.assertEqual(
            state.sql_main,
            "SELECT SUM(quantity * unit_price) AS revenue FROM transactions",
        )
        self.assertEqual(state.sql_companions, {})

    def test_non_default_net_gross_never_hits_rule_mismatch_gate(self):
        # The exact failure this detection branch exists to prevent: the same
        # avg/unit_price + "returns" intent that fires rule 1's condition (b)
        # -- and rule 2 -- hard-fails compile_sql when rule 1 is absent...
        without_detection = compile_sql(
            _build_state(
                aggregation="avg", metric="unit_price", net_gross="returns"
            )
        )
        self.assertEqual(without_detection.error, _RULE_MISMATCH_REASON)
        self.assertIsNone(without_detection.sql_main)
        self.assertEqual(without_detection.sql_companions, {})

        # ...yet flows through cleanly when detection runs first, which is the
        # real Node 3 -> Node 4 sequence. Both rules fire on this intent, so
        # compile_sql AND-composes rule 2's exclusion alongside the "returns"
        # variant and emits exactly one companion -- for rule 2 only, never for
        # rule 1.
        state = self._detect_then_compile(
            aggregation="avg", metric="unit_price", net_gross="returns"
        )
        self.assertEqual(
            state.applicable_rules,
            [_NET_VS_GROSS, _AVG_EXCLUDE_ZERO_PRICE],
        )
        self.assertIsNone(state.error)
        self.assertEqual(
            state.sql_main,
            "SELECT AVG(unit_price) AS unit_price FROM transactions "
            "WHERE quantity < 0 AND unit_price <> 0",
        )
        self.assertEqual(
            set(state.sql_companions), {_AVG_EXCLUDE_ZERO_PRICE}
        )
        self.assertEqual(
            state.sql_companions[_AVG_EXCLUDE_ZERO_PRICE].sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE unit_price = 0",
        )

    def test_avg_over_revenue_flows_through_detection_to_compile_sql(self):
        # Rule 2 alone on avg/revenue: through Node 3 -> Node 4 the detected
        # rule must compile the zero-price exclusion AND-composed into the main
        # revenue average plus one counting companion -- proving a rule-2-only
        # intent survives compile_sql's gates (rule 1 never fires here, so no
        # companion or variant comes from it).
        state = self._detect_then_compile(aggregation="avg", metric="revenue")
        self.assertEqual(state.applicable_rules, [_AVG_EXCLUDE_ZERO_PRICE])
        self.assertIsNone(state.error)
        self.assertEqual(
            state.sql_main,
            "SELECT AVG(quantity * unit_price) AS revenue FROM transactions "
            "WHERE unit_price <> 0",
        )
        self.assertEqual(
            set(state.sql_companions), {_AVG_EXCLUDE_ZERO_PRICE}
        )
        self.assertEqual(
            state.sql_companions[_AVG_EXCLUDE_ZERO_PRICE].sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE unit_price = 0",
        )

    def test_non_firing_intent_still_compiles_as_base_case(self):
        # Negative control at the integration level: avg over quantity -- the
        # deliberate rule-2 exclusion -- fires neither rule (rule 1 keys on SUM
        # totals with the net/gross default, rule 2 on price-derived metrics),
        # so it stays rule-free through detection and compiles as the plain
        # base case.
        state = self._detect_then_compile(
            aggregation="avg", metric="quantity"
        )
        self.assertEqual(state.applicable_rules, [])
        self.assertIsNone(state.error)
        self.assertEqual(
            state.sql_main,
            "SELECT AVG(quantity) AS quantity FROM transactions",
        )
        self.assertEqual(state.sql_companions, {})

    def test_customer_grouped_revenue_flows_through_detection_to_compile_sql(
        self,
    ):
        # A customer-grouped revenue SUM fires rules 1 and 3; through Node 3 ->
        # Node 4 it must compile with the group_by intact, rule 3's
        # "customer_id IS NOT NULL" AND-composed into the main WHERE, and one
        # rule-3 companion counting the NULL-customer rows (rule 1 still emits
        # no companion).
        state = self._detect_then_compile(
            aggregation="sum",
            metric="revenue",
            group_by=["customer_id"],
        )
        self.assertEqual(
            state.applicable_rules,
            [_NET_VS_GROSS, _CUSTOMER_EXCLUDE_NULL],
        )
        self.assertIsNone(state.error)
        self.assertEqual(
            state.sql_main,
            "SELECT customer_id, SUM(quantity * unit_price) AS revenue "
            "FROM transactions WHERE customer_id IS NOT NULL "
            "GROUP BY customer_id",
        )
        self.assertEqual(
            set(state.sql_companions), {_CUSTOMER_EXCLUDE_NULL}
        )
        self.assertEqual(
            state.sql_companions[_CUSTOMER_EXCLUDE_NULL].sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE customer_id IS NULL",
        )

    def test_stock_code_grouped_query_flows_through_detection_to_compile_sql(
        self,
    ):
        # A rule-4-only intent -- avg over quantity grouped by stock_code, the
        # shape that fires no rule 1/2/3 -- through Node 3 -> Node 4 must
        # compile with rule 4's product-only restriction AND-composed into the
        # main WHERE and exactly one rule-4 companion counting the excluded
        # non-product rows.
        state = self._detect_then_compile(
            aggregation="avg",
            metric="quantity",
            group_by=["stock_code"],
        )
        self.assertEqual(
            state.applicable_rules, [_PRODUCT_EXCLUDE_NONPRODUCT]
        )
        self.assertIsNone(state.error)
        self.assertEqual(
            state.sql_main,
            "SELECT stock_code, AVG(quantity) AS quantity FROM transactions "
            "WHERE line_item_type = 'product' GROUP BY stock_code",
        )
        self.assertEqual(
            set(state.sql_companions), {_PRODUCT_EXCLUDE_NONPRODUCT}
        )
        self.assertEqual(
            state.sql_companions[_PRODUCT_EXCLUDE_NONPRODUCT].sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE line_item_type <> 'product'",
        )

    def test_all_four_rules_flow_through_detection_to_compile_sql(self):
        # The fully representable four-rule combination -- avg over a
        # price-derived metric, a non-default "returns" variant (rule 1 via
        # condition (b)), grouped by both customer and product (rules 3 and 4)
        # -- must survive Node 3 -> Node 4: compile_sql AND-composes the
        # "returns" variant plus all three exclusion predicates onto one main
        # query and emits one companion per exclusion rule (never one for rule
        # 1).
        state = self._detect_then_compile(
            aggregation="avg",
            metric="unit_price",
            group_by=["customer_id", "stock_code"],
            net_gross="returns",
        )
        self.assertEqual(
            state.applicable_rules,
            [
                _NET_VS_GROSS,
                _AVG_EXCLUDE_ZERO_PRICE,
                _CUSTOMER_EXCLUDE_NULL,
                _PRODUCT_EXCLUDE_NONPRODUCT,
            ],
        )
        self.assertIsNone(state.error)
        self.assertEqual(
            state.sql_main,
            "SELECT customer_id, stock_code, AVG(unit_price) AS unit_price "
            "FROM transactions WHERE quantity < 0 AND unit_price <> 0 AND "
            "customer_id IS NOT NULL AND line_item_type = 'product' "
            "GROUP BY customer_id, stock_code",
        )
        self.assertEqual(
            set(state.sql_companions),
            {
                _AVG_EXCLUDE_ZERO_PRICE,
                _CUSTOMER_EXCLUDE_NULL,
                _PRODUCT_EXCLUDE_NONPRODUCT,
            },
        )
        self.assertEqual(
            state.sql_companions[_AVG_EXCLUDE_ZERO_PRICE].sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE unit_price = 0",
        )
        self.assertEqual(
            state.sql_companions[_CUSTOMER_EXCLUDE_NULL].sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE customer_id IS NULL",
        )
        self.assertEqual(
            state.sql_companions[_PRODUCT_EXCLUDE_NONPRODUCT].sql,
            "SELECT COUNT(*) AS excluded_count FROM transactions "
            "WHERE line_item_type <> 'product'",
        )


if __name__ == "__main__":
    unittest.main()

