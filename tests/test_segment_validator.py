"""Tests for graph.segment_validator.validate_segments.

Implemented contracts -- two independent hard-fail conditions, each with zero
exceptions by design:

(a) Every ``ref`` segment's key must exist in the reference dictionary. A
    missing key returns ``missing_ref_key:<key>``.

(b) No ``text`` segment's content may contain any digit. This is absolute and
    has no exceptions: a literal digit in prose means a value was hard-coded
    instead of routed through the reference dictionary. The "top 5" style
    case proves the point -- the phrase is only legal as a ``ref`` to
    ``display.top_n`` (added by the dictionary builder), never as literal text
    containing the digit ``5``.

Valid segment lists pass (return ``None``); violations return the exact
machine-readable reason of the first failing check.
"""

import unittest

from graph.segment_validator import validate_segments


def _text(content: str) -> dict:
    """A literal-prose segment."""
    return {"type": "text", "content": content}


def _ref(key: str) -> dict:
    """A substitution-hole segment referencing one dictionary key."""
    return {"type": "ref", "key": key}


class ValidateSegmentsValidTest(unittest.TestCase):
    """Segment lists with no violation pass and return None."""

    def test_empty_segment_list_passes(self):
        self.assertIsNone(validate_segments([], {"result.total": "£1.00"}))

    def test_plain_text_without_digits_passes(self):
        self.assertIsNone(
            validate_segments(
                [_text("Revenue for the United Kingdom was"), _ref("result.total")],
                {"result.total": "£1.00"},
            )
        )

    def test_ref_values_may_carry_digits(self):
        # The digit rule applies to literal text content, not to values that
        # arrive through refs from the dictionary (which are the sanctioned
        # channel for every computed number and fixed display parameter).
        refs = {"result.total": "£1,234.56", "display.top_n": "5"}
        self.assertIsNone(
            validate_segments(
                [
                    _text("The leading market contributed"),
                    _ref("result.total"),
                    _text("; showing the top"),
                    _ref("display.top_n"),
                ],
                refs,
            )
        )

    def test_ref_only_list_passes_when_every_key_exists(self):
        refs = {"filters.country": "United Kingdom"}
        self.assertIsNone(
            validate_segments([_ref("filters.country")], refs)
        )


class ValidateSegmentsMissingRefTest(unittest.TestCase):
    """Condition (a): a ref key absent from the dictionary is a hard failure."""

    def test_missing_ref_key_fails_with_exact_reason(self):
        result = validate_segments(
            [
                _text("Total revenue was"),
                _ref("result.total"),
            ],
            {"result.revenue": "£1.00"},
        )

        self.assertEqual(result, "missing_ref_key:result.total")

    def test_first_missing_key_is_reported(self):
        result = validate_segments(
            [
                _ref("result.rows.0.country"),
                _ref("result.rows.1.country"),
                _ref("result.total"),
            ],
            {"result.rows.0.country": "United Kingdom"},
        )

        self.assertEqual(result, "missing_ref_key:result.rows.1.country")

    def test_valid_keys_elsewhere_do_not_mask_a_missing_one(self):
        result = validate_segments(
            [_ref("filters.country"), _ref("filters.date_range.end")],
            {"filters.country": "United Kingdom"},
        )

        self.assertEqual(result, "missing_ref_key:filters.date_range.end")


class ValidateSegmentsDigitInTextTest(unittest.TestCase):
    """Condition (b): any digit in any text segment is a hard failure.

    This is absolute by design -- there are no exceptions, including for
    phrases that look like fixed display parameters ("top 5"): those must be
    represented as their own ref key (display.top_n) added by the dictionary
    builder, never special-cased here.
    """

    def test_digit_in_text_fails_with_exact_reason(self):
        result = validate_segments([_text("There were 12,345 orders")], {})

        self.assertEqual(result, "digit_in_text_segment:0")

    def test_digit_reporting_uses_the_segment_index(self):
        result = validate_segments(
            [
                _text("First, a safe sentence"),
                _text("Second one mentions 5 markets"),
            ],
            {},
        )

        self.assertEqual(result, "digit_in_text_segment:1")

    def test_top_5_phrase_in_text_fails_even_though_display_top_n_exists(self):
        # The reference dictionary DOES carry display.top_n -- proving the
        # point that the phrase must be routed through that ref key and is
        # never permitted as a literal text exception.
        refs = {"display.top_n": "5"}
        result = validate_segments(
            [_text("Here are the top 5 products by revenue:")],
            refs,
        )

        self.assertEqual(result, "digit_in_text_segment:0")

    def test_top_5_routed_through_display_top_n_ref_passes(self):
        # The sanctioned spelling of the same phrase: no digit in the text
        # segment; the digit lives only in the display.top_n ref value.
        result = validate_segments(
            [
                _text("Here are the top"),
                _ref("display.top_n"),
                _text("products by revenue:"),
                _ref("result.total"),
            ],
            {"display.top_n": "5", "result.total": "£1.00"},
        )

        self.assertIsNone(result)

    def test_any_digit_anywhere_in_text_fails(self):
        # Digits embedded in a word, a currency-looking literal, a year, or a
        # value-looking literal all fail identically -- no carve-outs.
        for content in ("R2D2 was sold", "£1,000 revenue", "Since 2026"):
            with self.subTest(content=content):
                self.assertEqual(
                    validate_segments([_text(content)], {}),
                    "digit_in_text_segment:0",
                )

    def test_both_conditions_checked_independently(self):
        # A text digit and a missing ref key coexist: condition (a) is checked
        # first and returns its reason; the digit is checked in its own pass,
        # so neither condition can mask or excuse the other.
        result = validate_segments(
            [_text("top 5 markets"), _ref("result.missing")], {}
        )

        self.assertEqual(result, "missing_ref_key:result.missing")


class ValidateSegmentsMalformedTest(unittest.TestCase):
    """Malformed segments are caller bugs and fail loudly, not verdicts."""

    def test_unknown_segment_type_raises(self):
        with self.assertRaises(ValueError):
            validate_segments([{"type": "image", "content": "x"}], {})

    def test_ref_without_key_raises(self):
        with self.assertRaises(ValueError):
            validate_segments([{"type": "ref"}], {})

    def test_text_without_content_raises(self):
        with self.assertRaises(ValueError):
            validate_segments([{"type": "text"}], {})


if __name__ == "__main__":
    unittest.main()

