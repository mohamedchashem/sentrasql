"""Tests for graph.nodes.extract_query_intent (Node 2).

The node wires the independently-built pieces -- ``build_system_prompt``,
``get_intent_model`` (strict structured output), and
``normalize_and_validate_intent`` -- plus the retry/error routing. The LLM
call is mocked in every test here (no real API cost); one small, clearly
separated real-API smoke test lives in ``scripts/smoke_extract_query_intent.py``
and is run by hand.

Implemented contracts covered below:

1. Error passthrough. A state that already carries ``state.error`` is returned
   completely untouched -- no prompt build, no model call, no field mutation
   (same gating pattern as ``validate_guardrails``).

2. Success on the first attempt. The prompt is built from the query text
   (``state.normalized_query`` when non-empty, else ``state.raw_query``), the
   model is invoked once with a SystemMessage carrying that prompt, the
   returned intent passes normalization, ``state.query_intent`` is set to it,
   and ``state.intent_extraction_retried`` stays ``False``.

3. Failure then success. Any failure (model exception or normalization
   reason) triggers exactly one retry reusing the *identical* call
   configuration (same prompt/messages/model); the retry is recorded via
   ``state.intent_extraction_retried = True``, and the successful retry's
   intent is stored.

4. Failure then failure. Both attempts fail; ``state.error`` is set to
   ``intent_extraction_failed:<underlying_reason>`` (the second failure's
   reason), ``state.intent_extraction_retried`` is ``True``, and
   ``state.query_intent`` is never set.
"""

import unittest
from contextlib import contextmanager
from unittest import mock

from langchain_core.messages import SystemMessage

from graph.nodes import extract_query_intent
from graph.state import (
    CountryFilter,
    Filters,
    GraphState,
    QueryIntent,
)

# Sentinel prompt text returned by the patched build_system_prompt, so tests
# assert on the assembled message without depending on the (DB-introspecting)
# real prompt builder.
_PROMPT = "PROMPT-SENTINEL"


def _consistent_intent(**overrides: object) -> QueryIntent:
    """A structurally valid, fully consistent intent (all defaults clean)."""
    kwargs: dict[str, object] = {
        "aggregation": "sum",
        "metric": "revenue",
        "group_by": [],
        "filters": Filters(),
    }
    kwargs.update(overrides)
    return QueryIntent(**kwargs)  # type: ignore[arg-type]


def _state(
    raw_query: str = "What was total revenue?",
    normalized_query: str = "",
    error: str | None = None,
) -> GraphState:
    return GraphState(
        raw_query=raw_query,
        normalized_query=normalized_query,
        error=error,
    )


@contextmanager
def _patched_model(fake_model: mock.Mock):
    """Yield patched (build_system_prompt, get_intent_model) around one test."""
    with mock.patch(
        "graph.nodes.build_system_prompt", return_value=_PROMPT
    ) as build_patch, mock.patch(
        "graph.nodes.get_intent_model", return_value=fake_model
    ) as factory_patch:
        yield build_patch, factory_patch


class ExtractQueryIntentSuccessTest(unittest.TestCase):
    """Node 2 first-attempt success: intent stored, no retry recorded."""

    def test_successful_first_attempt_stores_intent_without_retry(self):
        intent = _consistent_intent()
        fake_model = mock.Mock()
        fake_model.invoke.return_value = intent

        with _patched_model(fake_model) as (build_patch, _model_factory_patch):
            result = extract_query_intent(_state(raw_query="What was total revenue?"))

        self.assertIsNone(result.error)
        self.assertIs(result.query_intent, intent)
        self.assertFalse(result.intent_extraction_retried)
        fake_model.invoke.assert_called_once()
        # The single SystemMessage carries exactly the assembled prompt.
        (messages,) = fake_model.invoke.call_args.args
        self.assertEqual(len(messages), 1)
        self.assertIsInstance(messages[0], SystemMessage)
        self.assertEqual(messages[0].content, _PROMPT)
        # normalized_query is empty in the v1 graph -> raw_query is used.
        build_patch.assert_called_once_with("What was total revenue?")

    def test_normalized_query_is_preferred_when_present(self):
        intent = _consistent_intent()
        fake_model = mock.Mock()
        fake_model.invoke.return_value = intent

        with _patched_model(fake_model) as (build_patch, _model_factory_patch):
            result = extract_query_intent(
                _state(
                    raw_query="original wording",
                    normalized_query="normalized wording",
                )
            )

        self.assertIsNone(result.error)
        self.assertIs(result.query_intent, intent)
        build_patch.assert_called_once_with("normalized wording")


class ExtractQueryIntentRetrySuccessTest(unittest.TestCase):
    """Node 2 retry-on-failure: first attempt fails, identical retry succeeds."""

    def test_model_exception_then_success_retries_and_records_flag(self):
        intent = _consistent_intent()
        fake_model = mock.Mock()
        fake_model.invoke.side_effect = [RuntimeError("transient upstream error"), intent]

        with _patched_model(fake_model):
            result = extract_query_intent(_state())

        self.assertIsNone(result.error)
        self.assertIs(result.query_intent, intent)
        self.assertTrue(result.intent_extraction_retried)
        self.assertEqual(fake_model.invoke.call_count, 2)
        # Retry reused the identical call configuration (same prompt/messages).
        self.assertEqual(
            fake_model.invoke.call_args_list[0], fake_model.invoke.call_args_list[1]
        )

    def test_normalization_rejection_then_success_retries(self):
        inconsistent = _consistent_intent(
            filters=Filters(
                country=CountryFilter(present=True, value="")
            )
        )
        intent = _consistent_intent()
        fake_model = mock.Mock()
        fake_model.invoke.side_effect = [inconsistent, intent]

        with _patched_model(fake_model):
            result = extract_query_intent(_state())

        self.assertIsNone(result.error)
        self.assertIs(result.query_intent, intent)
        self.assertTrue(result.intent_extraction_retried)
        self.assertEqual(fake_model.invoke.call_count, 2)


class ExtractQueryIntentDoubleFailureTest(unittest.TestCase):
    """Node 2 retry-then-fail: both attempts fail, error path taken."""

    def test_two_model_failures_set_error_with_second_underlying_reason(self):
        fake_model = mock.Mock()
        fake_model.invoke.side_effect = [
            ValueError("first attempt failure"),
            TimeoutError("second attempt failure"),
        ]

        with _patched_model(fake_model):
            result = extract_query_intent(_state())

        self.assertEqual(
            result.error,
            "intent_extraction_failed:TimeoutError",
        )
        self.assertIsNone(result.query_intent)
        self.assertTrue(result.intent_extraction_retried)
        self.assertEqual(fake_model.invoke.call_count, 2)
        # Retry reused the identical call configuration.
        self.assertEqual(
            fake_model.invoke.call_args_list[0], fake_model.invoke.call_args_list[1]
        )

    def test_two_normalization_rejections_embed_the_second_reason(self):
        inconsistent = _consistent_intent(
            filters=Filters(country=CountryFilter(present=True, value=""))
        )
        fake_model = mock.Mock()
        fake_model.invoke.side_effect = [inconsistent, inconsistent]

        with _patched_model(fake_model):
            result = extract_query_intent(_state())

        self.assertEqual(
            result.error,
            "intent_extraction_failed:intent_inconsistency:"
            "present_true_empty_value:filters.country",
        )
        self.assertIsNone(result.query_intent)
        self.assertTrue(result.intent_extraction_retried)
        self.assertEqual(fake_model.invoke.call_count, 2)


class ExtractQueryIntentErrorPassthroughTest(unittest.TestCase):
    """Node 2 error passthrough: an already-errored state returns unchanged."""

    def test_errored_state_is_returned_completely_unchanged(self):
        state = _state(error="invalid_intent:rule_mismatch")
        fake_model = mock.Mock()

        with _patched_model(fake_model) as (build_patch, model_factory_patch):
            result = extract_query_intent(state)

        self.assertIs(result, state)
        self.assertEqual(result.model_dump(), state.model_dump())
        build_patch.assert_not_called()
        model_factory_patch.assert_not_called()
        fake_model.invoke.assert_not_called()


if __name__ == "__main__":
    unittest.main()

