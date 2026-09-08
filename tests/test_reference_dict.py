"""Tests for graph.reference_dict.build_reference_dict.

Implemented contracts:

1. Scalar (ungrouped) queries produce exactly one result key,
   ``result.<metric>``, whose value is formatted by the module's single
   formatting table: revenue-type metrics (``revenue``, ``unit_price``) as GBP
   currency (``£`` prefix, thousands separators, two decimals) and every other
   metric (``quantity``, ``customer_id``) as a plain number (integers with
   thousands separators and no decimal point; non-integers to two decimals
   with trailing zeros trimmed). An empty or ``None`` result set produces no
   ``result.*`` key -- nothing was computed, so nothing is substituted.

2. Grouped queries produce ``result.total`` (the formatted value of the
   execute_queries top-level aggregate) plus ``result.rows.<ordinal>.<column>``
   per-row keys for every projected column. The wrapper-dict shape
   ``{"rows": [...], "total": {...}}`` is the DESIGN_LOG.md section 20 output
   contract of execute_queries; a bare list under a grouped intent is a
   pre-change/stale state and fails loudly, as does a wrapper missing its
   total.

3. Filter keys appear only when their ``*_present`` flag is true:
   ``filters.country``, ``filters.date_range.start``, ``filters.date_range.
   end``. A populated value with a false flag is never read (the flag is the
   source of truth). Dates keep the existing ISO-8601 format verbatim.

4. Fixed display parameters are added by the builder, never hard-coded into
   templates: a ``ranking_size`` assumption yields ``display.top_n`` = "5"
   (the implicit top-N default). No ranking assumption means no display key.
"""

import unittest

from graph.reference_dict import build_reference_dict
from graph.state import (
    Assumption,
    CountryFilter,
    DateRangeFilter,
    Filters,
    QueryIntent,
)


def _intent(
    aggregation: str = "sum",
    metric: str = "revenue",
    group_by: list[str] | None = None,
    filters: Filters | None = None,
    assumptions: list[Assumption] | None = None,
    distinct: bool = False,
) -> QueryIntent:
    """Build the smallest QueryIntent carrying the requested fields."""
    return QueryIntent(
        aggregation=aggregation,
        metric=metric,
        distinct=distinct,
        group_by=group_by,
        filters=filters or Filters(),
        assumptions=assumptions or [],
    )


def _grouped_result(
    rows: list[dict], total: dict
) -> dict:
    """Build the execute_queries grouped wrapper shape (DESIGN_LOG §20)."""
    return {"rows": rows, "total": total}


class BuildReferenceDictScalarTest(unittest.TestCase):
    """Scalar queries: one ``result.<metric>`` key, formatted once here."""

    def test_scalar_revenue_formatted_as_currency(self):
        intent = _intent(aggregation="sum", metric="revenue")
        refs = build_reference_dict([{"revenue": 1234567.8}], intent)

        self.assertEqual(refs["result.revenue"], "£1,234,567.80")

    def test_scalar_unit_price_formatted_as_currency(self):
        intent = _intent(aggregation="avg", metric="unit_price")
        refs = build_reference_dict([{"unit_price": 4.2}], intent)

        self.assertEqual(refs["result.unit_price"], "£4.20")

    def test_scalar_quantity_sum_plain_integer(self):
        intent = _intent(aggregation="sum", metric="quantity")
        refs = build_reference_dict([{"quantity": 1323232}], intent)

        # Counts are plain numbers: no currency decoration, thousands
        # separators, no decimal point.
        self.assertEqual(refs["result.quantity"], "1,323,232")

    def test_scalar_customer_count_plain_integer(self):
        intent = _intent(
            aggregation="count", metric="customer_id", distinct=True
        )
        refs = build_reference_dict([{"customer_id": 4372}], intent)

        self.assertEqual(refs["result.customer_id"], "4,372")

    def test_scalar_average_quantity_plain_number_trims_trailing_zero(self):
        intent = _intent(aggregation="avg", metric="quantity")
        refs = build_reference_dict([{"quantity": 5.4}], intent)

        self.assertEqual(refs["result.quantity"], "5.4")

    def test_scalar_integral_float_value_renders_without_decimal_point(self):
        intent = _intent(aggregation="sum", metric="quantity")
        refs = build_reference_dict([{"quantity": 5000.0}], intent)

        self.assertEqual(refs["result.quantity"], "5,000")

    def test_scalar_empty_result_produces_no_result_key(self):
        # Zero rows is a valid outcome; with no computed value there must be no
        # result.* key for a template to reference.
        intent = _intent(aggregation="sum", metric="revenue")
        refs = build_reference_dict([], intent)

        self.assertNotIn("result.revenue", refs)

    def test_scalar_none_result_produces_no_result_key(self):
        intent = _intent(aggregation="sum", metric="revenue")
        refs = build_reference_dict(None, intent)

        self.assertNotIn("result.revenue", refs)

    def test_scalar_multi_row_result_fails_loudly(self):
        # An ungrouped intent whose query returned several rows is an upstream
        # contradiction: the scalar value would otherwise be chosen silently.
        intent = _intent(aggregation="sum", metric="revenue")
        with self.assertRaises(ValueError):
            build_reference_dict(
                [{"revenue": 1.0}, {"revenue": 2.0}], intent
            )

    def test_scalar_grouped_wrapper_under_ungrouped_intent_fails_loudly(self):
        intent = _intent(aggregation="sum", metric="revenue")
        with self.assertRaises(ValueError):
            build_reference_dict(
                _grouped_result([{"country": "UK"}], {"revenue": 1.0}), intent
            )

    def test_missing_metric_alias_in_row_fails_loudly(self):
        intent = _intent(aggregation="sum", metric="revenue")
        with self.assertRaises(ValueError):
            build_reference_dict([{"total_revenue": 1.0}], intent)


class BuildReferenceDictGroupedTest(unittest.TestCase):
    """Grouped queries: ``result.total`` plus ordinal-addressed per-row keys."""

    def test_grouped_result_total_and_per_row_keys(self):
        intent = _intent(
            aggregation="sum", metric="revenue", group_by=["country"]
        )
        refs = build_reference_dict(
            _grouped_result(
                rows=[
                    {"country": "United Kingdom", "revenue": 800000.0},
                    {"country": "Germany", "revenue": 200000.0},
                ],
                total={"revenue": 1000000.0},
            ),
            intent,
        )

        self.assertEqual(refs["result.total"], "£1,000,000.00")
        # Grouped results expose the total, never a lone result.revenue key.
        self.assertNotIn("result.revenue", refs)
        # Per-row keys, ordinal-addressed per projected column.
        self.assertEqual(refs["result.rows.0.country"], "United Kingdom")
        self.assertEqual(refs["result.rows.0.revenue"], "£800,000.00")
        self.assertEqual(refs["result.rows.1.country"], "Germany")
        self.assertEqual(refs["result.rows.1.revenue"], "£200,000.00")

    def test_grouped_multi_column_group_by_is_fully_addressed(self):
        intent = _intent(
            aggregation="sum",
            metric="quantity",
            group_by=["country", "stock_code"],
        )
        refs = build_reference_dict(
            _grouped_result(
                rows=[
                    {
                        "country": "United Kingdom",
                        "stock_code": "85123A",
                        "quantity": 250,
                    }
                ],
                total={"quantity": 250},
            ),
            intent,
        )

        self.assertEqual(refs["result.total"], "250")
        self.assertEqual(refs["result.rows.0.country"], "United Kingdom")
        # A value with letters and digits stays a plain dimension value; the
        # metric formatting decision applies only to the metric column.
        self.assertEqual(refs["result.rows.0.stock_code"], "85123A")
        self.assertEqual(refs["result.rows.0.quantity"], "250")

    def test_grouped_empty_rows_produce_no_result_keys(self):
        intent = _intent(
            aggregation="sum", metric="revenue", group_by=["country"]
        )
        refs = build_reference_dict([], intent)

        self.assertNotIn("result.total", refs)
        self.assertEqual(
            [key for key in refs if key.startswith("result.")], []
        )

    def test_grouped_bare_list_with_rows_fails_loudly(self):
        # A bare row list under a grouped intent is the pre-DESIGN_LOG-§20
        # shape with no total: answering from it would silently omit the very
        # aggregate the design requires, so it fails loudly instead.
        intent = _intent(
            aggregation="sum", metric="revenue", group_by=["country"]
        )
        with self.assertRaises(ValueError):
            build_reference_dict(
                [{"country": "UK", "revenue": 1.0}], intent
            )

    def test_grouped_wrapper_missing_total_fails_loudly(self):
        intent = _intent(
            aggregation="sum", metric="revenue", group_by=["country"]
        )
        with self.assertRaises(ValueError):
            build_reference_dict({"rows": [{"country": "UK"}]}, intent)


class BuildReferenceDictFiltersTest(unittest.TestCase):
    """Filter keys follow the ``*_present`` flags, never the value strings."""

    def test_all_present_filters_are_included(self):
        intent = _intent(
            aggregation="sum",
            metric="revenue",
            filters=Filters(
                country=CountryFilter(present=True, value="United Kingdom"),
                date_range=DateRangeFilter(
                    start_present=True,
                    start="2009-12-01",
                    end_present=True,
                    end="2011-12-09",
                ),
            ),
        )
        refs = build_reference_dict([{"revenue": 1.0}], intent)

        self.assertEqual(refs["filters.country"], "United Kingdom")
        # Dates keep the project's existing ISO-8601 format verbatim.
        self.assertEqual(refs["filters.date_range.start"], "2009-12-01")
        self.assertEqual(refs["filters.date_range.end"], "2011-12-09")

    def test_half_open_range_emits_only_the_present_boundary(self):
        intent = _intent(
            aggregation="sum",
            metric="revenue",
            filters=Filters(
                date_range=DateRangeFilter(
                    start_present=True, start="2010-01-01"
                )
            ),
        )
        refs = build_reference_dict([{"revenue": 1.0}], intent)

        self.assertEqual(refs["filters.date_range.start"], "2010-01-01")
        self.assertNotIn("filters.date_range.end", refs)
        self.assertNotIn("filters.country", refs)

    def test_end_only_range_emits_only_end_boundary(self):
        intent = _intent(
            aggregation="sum",
            metric="revenue",
            filters=Filters(
                date_range=DateRangeFilter(
                    end_present=True, end="2010-01-01"
                )
            ),
        )
        refs = build_reference_dict([{"revenue": 1.0}], intent)

        self.assertNotIn("filters.date_range.start", refs)
        self.assertEqual(refs["filters.date_range.end"], "2010-01-01")

    def test_populated_value_with_false_flag_is_never_emitted(self):
        # present=False is the source of truth even when a value string sits in
        # the paired field (a state extract_query_intent should never produce,
        # but which must not leak into the dictionary if it does).
        intent = _intent(
            aggregation="sum",
            metric="revenue",
            filters=Filters(
                country=CountryFilter(
                    present=False, value="United Kingdom"
                ),
                date_range=DateRangeFilter(
                    start_present=False,
                    start="2009-12-01",
                    end_present=False,
                    end="2011-12-09",
                ),
            ),
        )
        refs = build_reference_dict([{"revenue": 1.0}], intent)

        self.assertNotIn("filters.country", refs)
        self.assertNotIn("filters.date_range.start", refs)
        self.assertNotIn("filters.date_range.end", refs)


class BuildReferenceDictDisplayTest(unittest.TestCase):
    """Fixed display parameters come from the builder, never template text."""

    def test_ranking_size_assumption_adds_display_top_n(self):
        intent = _intent(
            aggregation="sum",
            metric="revenue",
            assumptions=[
                Assumption(
                    field="ranking_size",
                    raw_phrase="top products",
                    resolution="no explicit count was given; used the default of 5",
                )
            ],
        )
        refs = build_reference_dict([{"revenue": 1.0}], intent)

        # "top 5" must be referenceable only via the ref key; the value is the
        # digit-carrying fixed display parameter.
        self.assertEqual(refs["display.top_n"], "5")

    def test_no_ranking_assumption_means_no_display_key(self):
        intent = _intent(aggregation="sum", metric="revenue")
        refs = build_reference_dict([{"revenue": 1.0}], intent)

        self.assertNotIn("display.top_n", refs)

    def test_query_intent_none_is_a_caller_error(self):
        with self.assertRaises(ValueError):
            build_reference_dict([{"revenue": 1.0}], None)


if __name__ == "__main__":
    unittest.main()

