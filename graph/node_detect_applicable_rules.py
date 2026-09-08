"""Node 3 - ``detect_applicable_rules`` (one node per module).

Deterministic, no LLM: reads ``state.query_intent`` and mechanically
checks it against the four policy rules, populating
``state.applicable_rules`` (see the function docstring for the exact
per-rule triggers).

Only this module defines the Node 3 function."""

from __future__ import annotations

from graph.state import GraphState, RuleName


def detect_applicable_rules(state: GraphState) -> GraphState:
    """Node 3. Deterministic, no LLM. Reads state.query_intent and mechanically
    checks it against the four policy rules (net/gross/returns disclosure,
    average-excludes-zero-price, customer-grouping-excludes-null,
    product-ranking-excludes-nonproduct). Populates state.applicable_rules.
    Contains no interpretive judgment -- pure structural checks against
    query_intent's fields.

    Rule scope: all four rules fire. ``NET_VS_GROSS`` (rule 1) fires on a
    SUM over a net/gross amount metric or any non-default ``net_gross``
    variant; ``AVG_EXCLUDE_ZERO_PRICE`` (rule 2) on an AVG over a
    price-derived metric; ``CUSTOMER_EXCLUDE_NULL`` (rule 3) on a group_by
    that includes ``customer_id``; and ``PRODUCT_EXCLUDE_NONPRODUCT`` (rule 4)
    on a group_by that includes ``stock_code`` -- grouping by the product
    dimension, never merely filtering on it (each trigger is spelled out in
    the inline comment above its check below). When ``state.query_intent`` is
    ``None`` -- Node 2 (``extract_query_intent``) is still a stub -- the node
    is inert and returns ``state`` unchanged, mirroring ``compile_sql``'s
    None-intent contract.
    """

    intent = state.query_intent
    if intent is None:
        return state

    # Recomputed from scratch every call: this node is deterministic, so a
    # stale ``applicable_rules`` from an earlier pass must be replaced, never
    # appended to.
    applicable_rules: list[RuleName] = []

    # Rule NET_VS_GROSS (rule 1): revenue/quantity totals default to "net" (sum
    # the signed amounts as recorded), and the rule resolves which of its three
    # named filter variants the query applies. It fires when either:
    #   (a) the query is a SUM over an amount metric that carries the
    #       net/gross distinction -- metric "revenue" or "quantity"; or
    #   (b) query_intent.net_gross explicitly requests a non-default variant
    #       ("gross_of_cancellations" or "returns"), regardless of aggregation
    #       and metric. Condition (b) exists specifically to satisfy
    #       compile_sql's invalid_intent:rule_mismatch gate, which hard-fails
    #       when a non-default net_gross appears without this rule present --
    #       getting this trigger wrong would directly cause that downstream
    #       failure.
    sums_amount_metric = (
        intent.aggregation == "sum"
        and intent.metric in {"revenue", "quantity"}
    )
    if sums_amount_metric or intent.net_gross != "net":
        applicable_rules.append(RuleName.NET_VS_GROSS)

    # Rule AVG_EXCLUDE_ZERO_PRICE (rule 2): an AVG over a price-derived metric
    # is distorted by zero-price rows -- a row priced at 0 contributes 0 to
    # AVG(unit_price) and, since revenue is quantity * unit_price, also to
    # AVG(quantity * unit_price) -- so both price metrics fire the rule that
    # drops those rows and counts them in a companion query. Metric "quantity"
    # is deliberately NOT included (DESIGN_LOG.md section 13): a zero-price row
    # still carries a real, non-zero quantity (e.g. a free sample), so quantity
    # averages are not distorted by zero-price rows and must not fire this rule.
    averages_price_derived_metric = (
        intent.aggregation == "avg"
        and intent.metric in {"unit_price", "revenue"}
    )
    if averages_price_derived_metric:
        applicable_rules.append(RuleName.AVG_EXCLUDE_ZERO_PRICE)

    # Rule CUSTOMER_EXCLUDE_NULL (rule 3): a customer-grouped query must not
    # surface a spurious "no customer" bucket, so rows with a NULL customer_id
    # drop out of the main result and are counted by a companion query (see
    # DESIGN_LOG.md section 4.2, policy rule 3). It fires exactly when the
    # query groups by the customer dimension -- membership anywhere in
    # group_by, regardless of position or how many other columns group beside
    # it -- because that is the only intent shape a NULL-customer bucket can
    # arise in. It is independent of rules 1 and 2, so it composes freely with
    # both.
    if intent.group_by is not None and "customer_id" in intent.group_by:
        applicable_rules.append(RuleName.CUSTOMER_EXCLUDE_NULL)

    # Rule PRODUCT_EXCLUDE_NONPRODUCT (rule 4): a product-ranking query -- one
    # grouped by the product dimension -- aggregates across a mixed population
    # of product and non-product line items, because fee/adjustment/voucher
    # rows (postage, discounts, "Manual", gift vouchers, ...) share the same
    # transactions table as real products and would otherwise surface as their
    # own pseudo-product buckets in the ranked result (DESIGN_LOG.md section
    # 2.3/4.2, policy rule 4). It fires exactly when query_intent.group_by
    # includes the product dimension ``"stock_code"`` -- membership anywhere
    # in group_by, regardless of position or how many other columns group
    # beside it -- mirroring how rule 3 keys on ``"customer_id"`` in the same
    # list. A query that merely *filters* to a specific stock_code value (via
    # query_intent.filters) without grouping by it does NOT fire this rule: a
    # single stock code denotes one homogeneous population -- a given code is
    # either a product or a non-product line item, never both -- so no
    # aggregation across a mixed product/non-product population happens in
    # that case and the ambiguity this rule exists to resolve does not arise.
    # The distinction is deliberate and load-bearing: this rule keys on the
    # GROUP BY dimension, not on the presence of the column anywhere in the
    # intent. The rule is independent of rules 1-3, so it composes freely with
    # all of them.
    if intent.group_by is not None and "stock_code" in intent.group_by:
        applicable_rules.append(RuleName.PRODUCT_EXCLUDE_NONPRODUCT)

    state.applicable_rules = applicable_rules
    return state
