"""Tests for graph.node_handle_error.handle_error (error-path terminal node).

Implemented contracts:

1. Prefix-based message selection. ``state.error`` is matched by its top-level
   prefix (the part before the first ``:``; bare guardrail reasons match by
   their whole string) against a static module-level prefix -> message lookup:

   - ``intent_extraction_failed:...`` (Node 2) -> the rephrase request.
   - every guardrail/validation reason prefix in the codebase's taxonomy
     (``unparseable_sql``, ``no_statement``, ``multiple_statements``,
     ``not_a_select``, ``disallowed_table``, ``disallowed_column``,
     ``disallowed_function``, ``wildcard_select``, ``unconditioned_join``,
     ``invalid_intent``, ``disclosure_assembly_inconsistency``) -> the
     "couldn't be safely processed" message.
   - anything else (execution failures, result-shape failures,
     answer-generation failures, unknown prefixes) -> the generic fallback
     message. An unrecognized prefix always falls through silently -- it never
     raises and never produces an empty message.

2. No interpolation. ``state.final_answer`` is always exactly one of the fixed
   messages above; the raw ``state.error`` value and any part of
   ``state.error_reasons`` never appear anywhere in it.

3. ``state.error_reasons`` is optional context. The common case for every node
   except ``assemble_answer`` has it empty/absent, while ``assemble_answer``'s
   failure path populates it; neither presence nor absence may change the
   selected message or break the node.
"""

import unittest

from graph.node_handle_error import handle_error
from graph.state import GraphState

# The fixed user-facing messages, spelled out so a wording change fails loudly.
_INTENT_EXTRACTION_FAILED_MESSAGE = (
    "I wasn't able to understand that question — could you try rephrasing it?"
)
_GUARDRAIL_REJECTION_MESSAGE = "That request couldn't be safely processed."
_GENERIC_FALLBACK_MESSAGE = (
    "Something went wrong while processing that request — please try again."
)


def _state(
    *,
    error: str,
    error_reasons: list[str] | None = None,
) -> GraphState:
    """Build a minimal GraphState routed toward the error path."""
    return GraphState(
        raw_query="What was total revenue in 2011?",
        error=error,
        error_reasons=[] if error_reasons is None else error_reasons,
    )


class HandleErrorMessageCategoryTest(unittest.TestCase):
    """One message per error-reason category, selected purely by prefix."""

    def test_intent_extraction_failed_uses_rephrase_message(self):
        state = _state(error="intent_extraction_failed:TimeoutError")

        result = handle_error(state)

        self.assertEqual(
            result.final_answer, _INTENT_EXTRACTION_FAILED_MESSAGE
        )

    def test_guardrail_rejection_prefixes_use_safe_processing_message(self):
        # Every guardrail/validation reason prefix in the codebase taxonomy,
        # in both its bare and its ``<prefix>:<detail>`` spellings.
        guardrail_errors = [
            "unparseable_sql",
            "no_statement",
            "multiple_statements",
            "not_a_select",
            "disallowed_table:orders",
            "disallowed_table:secret_orders",
            "disallowed_column:customer_id",
            "disallowed_function:RANDOM",
            "wildcard_select",
            "unconditioned_join",
            "invalid_intent:aggregation_metric_mismatch",
            "invalid_intent:invalid_group_by",
            "invalid_intent:invalid_date_range",
            "invalid_intent:no_matching_data",
            "invalid_intent:rule_mismatch",
            "disclosure_assembly_inconsistency:NET_VS_GROSS",
        ]
        for error in guardrail_errors:
            with self.subTest(error=error):
                state = _state(error=error)

                result = handle_error(state)

                self.assertEqual(
                    result.final_answer, _GUARDRAIL_REJECTION_MESSAGE
                )

    def test_unmapped_prefix_falls_through_to_generic_fallback(self):
        # A prefix that is genuinely not part of the taxonomy...
        unmapped_errors = [
            "unknown_prefix:some_detail",
            "intent_inconsistency:present_true_empty_value:filters.country",
            "missing_ref_key:result.revenue",
        ]
        # ...plus the real codebase prefixes that are deliberately NOT in the
        # guardrail category: execution/result-shape/answer-generation failures
        # are not validation rejections and must use the generic fallback.
        unmapped_errors.extend(
            [
                "query_execution_failed:main",
                "query_execution_failed:total",
                "query_execution_failed:AVG_EXCLUDE_ZERO_PRICE",
                "query_result_shape_invalid:total",
                "query_result_shape_invalid:NET_VS_GROSS",
                "answer_generation_failed:render_error:RuntimeError",
                "answer_generation_failed:ValueError",
            ]
        )
        for error in unmapped_errors:
            with self.subTest(error=error):
                state = _state(error=error)

                result = handle_error(state)

                self.assertEqual(result.final_answer, _GENERIC_FALLBACK_MESSAGE)


class HandleErrorNoInterpolationTest(unittest.TestCase):
    """The raw state.error value never leaks into the user-facing message."""

    def test_raw_error_value_never_appears_in_final_answer(self):
        error = "intent_extraction_failed:UniqueInternalCode_ABC123"
        state = _state(error=error)

        result = handle_error(state)

        self.assertEqual(
            result.final_answer, _INTENT_EXTRACTION_FAILED_MESSAGE
        )
        self.assertNotIn("UniqueInternalCode_ABC123", result.final_answer)
        self.assertNotIn(error, result.final_answer)
        self.assertNotIn("intent_extraction_failed", result.final_answer)

    def test_error_reasons_content_never_appears_in_final_answer(self):
        # Even when state.error_reasons is populated, none of its content may
        # reach the user-facing message -- the message stays the fixed string
        # selected by state.error's prefix alone.
        state = _state(
            error="intent_extraction_failed:SecondSecret_XYZ789",
            error_reasons=[
                "answer_generation_failed:render_error:SecretDetail_ABC123"
            ],
        )

        result = handle_error(state)

        self.assertEqual(
            result.final_answer, _INTENT_EXTRACTION_FAILED_MESSAGE
        )
        self.assertNotIn("SecretDetail_ABC123", result.final_answer)
        self.assertNotIn("SecondSecret_XYZ789", result.final_answer)
        self.assertNotIn("answer_generation_failed", result.final_answer)


class HandleErrorErrorReasonsOptionalityTest(unittest.TestCase):
    """state.error_reasons is optional context: absent or populated must both work."""

    def test_error_reasons_absent_common_case(self):
        # The common case: every node except assemble_answer fails without ever
        # touching error_reasons, so it stays at its default empty list.
        state = _state(error="disallowed_table:orders")

        result = handle_error(state)

        self.assertEqual(result.error, "disallowed_table:orders")
        self.assertEqual(result.error_reasons, [])
        self.assertEqual(result.final_answer, _GUARDRAIL_REJECTION_MESSAGE)

    def test_error_reasons_populated_assemble_answer_case(self):
        # The assemble_answer-specific case: error_reasons carries the full
        # final-attempt reason list and must not break prefix selection.
        state = _state(
            error="answer_generation_failed:render_error:RuntimeError",
            error_reasons=[
                "answer_generation_failed:render_error:RuntimeError"
            ],
        )

        result = handle_error(state)

        self.assertEqual(
            result.error, "answer_generation_failed:render_error:RuntimeError"
        )
        # Populated reasons are preserved untouched.
        self.assertEqual(
            result.error_reasons,
            ["answer_generation_failed:render_error:RuntimeError"],
        )
        self.assertEqual(result.final_answer, _GENERIC_FALLBACK_MESSAGE)

    def test_populated_error_reasons_do_not_break_a_mapped_prefix(self):
        # Neither path breaks the other: with error_reasons populated, a
        # guardrail-prefix error still selects the guardrail message exactly as
        # it does when error_reasons is empty.
        state = _state(
            error="wildcard_select",
            error_reasons=[
                "answer_generation_failed:render_error:RuntimeError"
            ],
        )

        result = handle_error(state)

        self.assertEqual(result.error, "wildcard_select")
        self.assertEqual(
            result.error_reasons,
            ["answer_generation_failed:render_error:RuntimeError"],
        )
        self.assertEqual(result.final_answer, _GUARDRAIL_REJECTION_MESSAGE)


if __name__ == "__main__":
    unittest.main()
