"""Tests for graph.node_assemble_answer.assemble_answer (Node 7).

Implemented contracts covered below:

1. Passthrough gate. When ``state.error`` is set, ``guardrail_status`` is not
   ``"passed"``, ``state.main_results`` is ``None``, or ``state.disclosures``
   is ``None``, the node returns the state completely untouched. The last
   condition is not reachable through normal model construction (the field is
   a non-Optional ``list[Disclosure]``) but is kept as an explicit defensive
   precondition in the node; the test exercises it by assigning ``None`` at
   runtime, which pydantic does not re-validate.

2. Setup on a gate-passing state. The node genuinely calls
   ``build_reference_dict`` with the state's own ``main_results`` /
   ``query_intent`` and passes that real dictionary to
   ``graph.answer_prompt.build_answer_prompt``; the produced prompt lists
   every key/value that actually exists for that specific call (asserted
   against the real keys and formatted values, not a fixed expected string).

3. LLM generation, validation, rendering, and retry. ``get_answer_model`` is
   mocked in every test here (no real API cost). The node invokes the model
   with a SystemMessage built from the prompt and runs the emitted segments
   through ``validate_segments`` against the reference dictionary:
   - First-attempt success validates the segments, renders the deterministic
     three-step final answer into ``state.final_answer``, and never retries or
     mutates any other state.
   - Any failure (a validation reason or a model exception) is retried exactly
     once with the identical base prompt, appending the first attempt's
     collected failure reasons to the retry's prompt context; the retry is
     recorded via ``state.answer_generation_retried``.
   - Retry-then-failure routes to the error path: ``state.error`` carries the
     final attempt's first reason and ``state.error_reasons`` the final
     attempt's full reason list -- never the first attempt's reasons (a
     dedicated, separately-named test checks this with genuinely different
     first/second failure content: a missing ref key, then a digit in text).

4. Rendering and render exceptions. Rendering is deterministic three-step
   code that runs inside the attempt: a scalar render substitutes every ``ref``
   segment from the reference dictionary and appends the disclosures section;
   a grouped render inserts the plain-text row-data table between the intro
   and the disclosures. An exception raised while rendering otherwise-valid
   segments is a failure reason (``render_error:<ExceptionClassName>``) that
   triggers the same one retry as any other failure type -- it never crashes
   and never silently produces an empty or partial answer.
"""

import unittest
from contextlib import contextmanager
from unittest import mock

from langchain_core.messages import SystemMessage

from graph.answer_prompt import build_answer_prompt
from graph.node_assemble_answer import assemble_answer
from graph.state import (
    AnswerSegments,
    Assumption,
    CountryFilter,
    DateRangeFilter,
    Disclosure,
    Filters,
    GraphState,
    QueryIntent,
)

# A valid segment template for the gate-passing state produced by
# ``_answer_state``: literal prose plus one reference into that state's real
# reference dictionary, with no digit anywhere in a text segment. Reused by
# every LLM-backed test.
_VALID_SEGMENT_DICTS = [
    {
        "type": "text",
        "content": "Total revenue in the United Kingdom since the start of "
        "the year was ",
    },
    {"type": "ref", "key": "result.revenue"},
    {"type": "text", "content": "."},
]

# The exact reference dictionary the real ``build_reference_dict`` produces for
# ``_answer_state``'s data (mirrors the setup test's expectations), used to
# build the exact prompt the node must have handed the model.
_EXPECTED_REFERENCES = {
    "result.revenue": "£1,234,567.80",
    "filters.country": "United Kingdom",
    "filters.date_range.start": "2026-01-01T00:00:00",
    "display.top_n": "5",
}


def _valid_answer_segments() -> AnswerSegments:
    """A template that passes validate_segments against ``_answer_state``."""
    return AnswerSegments(segments=_VALID_SEGMENT_DICTS)


def _missing_ref_segments(key: str) -> AnswerSegments:
    """A template whose ref key is absent -> ``missing_ref_key:<key>``."""
    return AnswerSegments(
        segments=[
            {"type": "text", "content": "Total revenue was"},
            {"type": "ref", "key": key},
        ]
    )


def _digit_in_text_segments() -> AnswerSegments:
    """A template with a literal digit in prose -> ``digit_in_text_segment``."""
    return AnswerSegments(
        segments=[{"type": "text", "content": "Revenue was 5 million."}]
    )


@contextmanager
def _patched_answer_model(fake_model: mock.Mock):
    """Patch graph.node_assemble_answer.get_answer_model around one test."""
    with mock.patch(
        "graph.node_assemble_answer.get_answer_model", return_value=fake_model
    ) as factory_patch:
        yield factory_patch


def _invoked_prompt(fake_model: mock.Mock, attempt_index: int) -> str:
    """Return the single SystemMessage's content from one ``invoke`` call."""
    messages = fake_model.invoke.call_args_list[attempt_index].args[0]
    return messages[0].content


def _answer_state() -> GraphState:
    """Build a fully gate-passing state with real data in every input field.

    The intent and results are chosen so the real ``build_reference_dict``
    output carries keys from every namespace (scalar result, country filter,
    date-range start, and a display parameter) -- proving the prompt is built
    from this call's actual dictionary, not from example content.
    """
    return GraphState(
        raw_query=(
            "What was total revenue in the United Kingdom since the start of "
            "the year for the top products?"
        ),
        query_intent=QueryIntent(
            aggregation="sum",
            metric="revenue",
            filters=Filters(
                country=CountryFilter(present=True, value="United Kingdom"),
                date_range=DateRangeFilter(
                    start_present=True, start="2026-01-01T00:00:00"
                ),
            ),
            assumptions=[
                Assumption(
                    field="ranking_size",
                    raw_phrase="top products",
                    resolution="no explicit count was given; used the default of 5",
                )
            ],
        ),
        applicable_rules=[],
        sql_main=None,
        sql_total=None,
        sql_companions={},
        guardrail_status="passed",
        main_truncated=False,
        main_results=[{"revenue": 1234567.8}],
        error=None,
        disclosures=[
            Disclosure(
                source="direct_filter",
                label="net_vs_gross",
                detail="The amounts were reported on a net basis.",
            )
        ],
        final_answer=None,
    )


def _expected_scalar_final_answer() -> str:
    """The exact final answer a successful scalar render must produce."""
    return (
        "Total revenue in the United Kingdom since the start of the year was "
        "£1,234,567.80.\n\n"
        "Disclosures\n"
        "- The amounts were reported on a net basis."
    )


# A valid segment template for the grouped state produced by
# ``_grouped_answer_state``: it references only keys that state's real
# reference dictionary contains and keeps every digit out of prose.
_GROUPED_SEGMENT_DICTS = [
    {
        "type": "text",
        "content": "Total revenue for the selected period was ",
    },
    {"type": "ref", "key": "result.total"},
    {"type": "text", "content": ", broken down by country:"},
]


def _grouped_answer_segments() -> AnswerSegments:
    """A template that passes validate_segments against ``_grouped_answer_state``."""
    return AnswerSegments(segments=_GROUPED_SEGMENT_DICTS)


def _grouped_answer_state() -> GraphState:
    """Build a fully gate-passing grouped state with real row data.

    The grouped result uses execute_queries' DESIGN_LOG-§20 wrapper shape
    (``rows`` breakdown plus the ungrouped ``total`` aggregate), so the real
    ``build_reference_dict`` yields ``result.total`` and the per-row
    ``result.rows.<ordinal>.<column>`` keys the renderer displays as the table.
    """
    return GraphState(
        raw_query="What was total revenue by country?",
        query_intent=QueryIntent(
            aggregation="sum",
            metric="revenue",
            group_by=["country"],
        ),
        applicable_rules=[],
        sql_main=None,
        sql_total=None,
        sql_companions={},
        guardrail_status="passed",
        main_truncated=False,
        main_results={
            "rows": [
                {"country": "United Kingdom", "revenue": 800000.0},
                {"country": "Germany", "revenue": 200000.0},
            ],
            "total": {"revenue": 1000000.0},
        },
        error=None,
        disclosures=[
            Disclosure(
                source="direct_filter",
                label="net_vs_gross",
                detail="The amounts were reported on a net basis.",
            ),
            Disclosure(
                source="truncation",
                label="main_query",
                detail=(
                    "The main result set was limited by the guardrail's "
                    "row-limit enforcement."
                ),
            ),
        ],
        final_answer=None,
    )


class AssembleAnswerGatePassthroughTest(unittest.TestCase):
    """Node 7 gate: an errored/unvalidated/unexecuted state returns unchanged."""

    def test_error_set_returns_state_completely_untouched(self):
        state = _answer_state()
        state.error = "intent_extraction_failed:SomeError"

        result = assemble_answer(state)

        self.assertEqual(result.model_dump(), state.model_dump())
        self.assertEqual(result.error, "intent_extraction_failed:SomeError")

    def test_guardrail_not_passed_returns_state_completely_untouched(self):
        state = _answer_state()
        state.guardrail_status = "pending"

        result = assemble_answer(state)

        self.assertEqual(result.model_dump(), state.model_dump())
        self.assertEqual(result.guardrail_status, "pending")

    def test_main_results_none_returns_state_completely_untouched(self):
        state = _answer_state()
        state.main_results = None

        result = assemble_answer(state)

        self.assertEqual(result.model_dump(), state.model_dump())
        self.assertIsNone(result.main_results)

    def test_disclosures_none_returns_state_completely_untouched(self):
        # Unreachable through normal construction (disclosures is a
        # non-Optional list), but the node keeps the clause as a defensive
        # full-precondition check -- so force the impossible value by runtime
        # assignment, which pydantic does not re-validate.
        state = _answer_state()
        state.disclosures = None

        result = assemble_answer(state)

        self.assertEqual(result.model_dump(), state.model_dump())
        self.assertIsNone(result.disclosures)


class AssembleAnswerSetupTest(unittest.TestCase):
    """Node 7 setup: a gate-passing state reaches the prompt-building step."""

    def test_reaches_prompt_building_with_the_states_own_reference_keys(self):
        state = _answer_state()
        expected_references = {
            "result.revenue": "£1,234,567.80",
            "filters.country": "United Kingdom",
            "filters.date_range.start": "2026-01-01T00:00:00",
            "display.top_n": "5",
        }

        # Wrap -- not replace -- the real prompt builder, so the node genuinely
        # runs it while the test can observe what dictionary it received, and
        # mock the LLM factory so the generation path costs no API money.
        fake_model = mock.Mock()
        fake_model.invoke.return_value = _valid_answer_segments()
        with mock.patch(
            "graph.node_assemble_answer.build_answer_prompt",
            wraps=build_answer_prompt,
        ) as prompt_spy, mock.patch(
            "graph.node_assemble_answer.get_answer_model",
            return_value=fake_model,
        ):
            result = assemble_answer(state)

        # The node passed the setup gate, invoked the real builder exactly
        # once with the real dictionary built from this state's own data, and
        # sent that prompt to the (mocked) model exactly once.
        self.assertEqual(prompt_spy.call_count, 1)
        self.assertEqual(fake_model.invoke.call_count, 1)
        captured_references = prompt_spy.call_args.args[0]
        self.assertEqual(captured_references, expected_references)

        # The success path rendered the validated segments into the finished
        # final answer -- the only field that differs from the input state.
        self.assertEqual(result.final_answer, _expected_scalar_final_answer())
        expected = _answer_state()
        expected.final_answer = _expected_scalar_final_answer()
        self.assertEqual(result.model_dump(), expected.model_dump())

        # The prompt produced from that dictionary -- via the real builder --
        # actually contains the real keys and values of THIS call.
        prompt = build_answer_prompt(captured_references)
        for required in (
            "- result.revenue: £1,234,567.80",
            "- filters.country: United Kingdom",
            "- filters.date_range.start: 2026-01-01T00:00:00",
            "- display.top_n: 5",
        ):
            self.assertIn(required, prompt)
        # And it carries the segment-structure and no-digits rule sections.
        self.assertIn("ANSWER SEGMENT STRUCTURE", prompt)
        self.assertIn('{"type": "text", "content": "<prose>"}', prompt)
        self.assertIn('{"type": "ref", "key": "<key>"}', prompt)
        self.assertIn("NO-DIGITS-IN-TEXT RULE", prompt)
        self.assertIn(
            "a text segment's content must not contain any digit", prompt
        )


class AnswerPromptContentTest(unittest.TestCase):
    """Direct checks that the prompt content is dynamic, not hardcoded."""

    def test_empty_references_prompt_is_dynamic_and_states_no_numbers(self):
        prompt = build_answer_prompt({})

        self.assertIn("- (none", prompt)
        self.assertIn("ANSWER SEGMENT STRUCTURE", prompt)
        self.assertIn("NO-DIGITS-IN-TEXT RULE", prompt)
        # No example keys leak into a call that produced no values -- proof the
        # listing is generated from the given dictionary, not fixed text.
        self.assertNotIn("result.revenue", prompt)
        self.assertNotIn("filters.country", prompt)

    def test_same_references_build_byte_identical_prompt(self):
        references = {"result.revenue": "£1,234,567.80"}

        self.assertEqual(
            build_answer_prompt(references), build_answer_prompt(references)
        )


class AssembleAnswerFirstAttemptSuccessTest(unittest.TestCase):
    """Node 7 first-attempt success: validated segments, no retry recorded."""

    def test_successful_first_attempt_validates_and_never_retries(self):
        state = _answer_state()
        fake_model = mock.Mock()
        fake_model.invoke.return_value = _valid_answer_segments()

        with _patched_answer_model(fake_model):
            result = assemble_answer(state)

        # Exactly one LLM call, made with a single SystemMessage carrying the
        # real per-call prompt (built from this state's own reference dict).
        self.assertEqual(fake_model.invoke.call_count, 1)
        self.assertEqual(
            _invoked_prompt(fake_model, 0),
            build_answer_prompt(_EXPECTED_REFERENCES),
        )
        messages = fake_model.invoke.call_args_list[0].args[0]
        self.assertEqual(len(messages), 1)
        self.assertIsInstance(messages[0], SystemMessage)

        # Success reached on the first attempt: the validated segments were
        # rendered into the finished final answer, the retry flag stays False,
        # and no other state field was mutated.
        self.assertIsNone(result.error)
        self.assertFalse(result.answer_generation_retried)
        self.assertEqual(result.final_answer, _expected_scalar_final_answer())
        expected = _answer_state()
        expected.final_answer = _expected_scalar_final_answer()
        self.assertEqual(result.model_dump(), expected.model_dump())


class AssembleAnswerRetryThenSuccessTest(unittest.TestCase):
    """Node 7 retry-then-success: retried exactly once, then validated."""

    def test_validation_failure_then_success_retries_exactly_once(self):
        state = _answer_state()
        fake_model = mock.Mock()
        fake_model.invoke.side_effect = [
            _missing_ref_segments("result.total"),
            _valid_answer_segments(),
        ]

        with _patched_answer_model(fake_model):
            result = assemble_answer(state)

        self.assertIsNone(result.error)
        self.assertTrue(result.answer_generation_retried)
        self.assertEqual(fake_model.invoke.call_count, 2)
        # The retried (and validated) attempt rendered a real final answer.
        self.assertEqual(result.final_answer, _expected_scalar_final_answer())

        # The first attempt ran with the base prompt alone; the retry reused
        # the identical base prompt with the first attempt's failure reason
        # appended as plain structural text (the missing key name only).
        first_prompt = build_answer_prompt(_EXPECTED_REFERENCES)
        self.assertEqual(_invoked_prompt(fake_model, 0), first_prompt)
        retry_prompt = _invoked_prompt(fake_model, 1)
        self.assertTrue(retry_prompt.startswith(first_prompt))
        self.assertIn("missing_ref_key:result.total", retry_prompt)

    def test_model_exception_then_success_retries_exactly_once(self):
        state = _answer_state()
        fake_model = mock.Mock()
        fake_model.invoke.side_effect = [
            ValueError("first attempt exploded"),
            _valid_answer_segments(),
        ]

        with _patched_answer_model(fake_model):
            result = assemble_answer(state)

        self.assertIsNone(result.error)
        self.assertTrue(result.answer_generation_retried)
        self.assertEqual(fake_model.invoke.call_count, 2)
        # The retried (and validated) attempt rendered a real final answer.
        self.assertEqual(result.final_answer, _expected_scalar_final_answer())
        # The retry context names the exception class, never model-generated
        # text or numbers echoed back verbatim.
        self.assertIn("ValueError", _invoked_prompt(fake_model, 1))


class AssembleAnswerRetryThenFailureTest(unittest.TestCase):
    """Node 7 retry-then-failure: error fields come from the final attempt."""

    def test_second_failure_error_reasons_reflect_only_final_attempt(self):
        # Dedicated check (design review): the two attempts fail for genuinely
        # different reasons -- first a missing ref key, then a digit in text --
        # and state.error / state.error_reasons must carry the FINAL attempt's
        # failure content specifically, never the first attempt's.
        state = _answer_state()
        fake_model = mock.Mock()
        fake_model.invoke.side_effect = [
            _missing_ref_segments("result.total"),
            _digit_in_text_segments(),
        ]

        with _patched_answer_model(fake_model):
            result = assemble_answer(state)

        self.assertTrue(result.answer_generation_retried)
        self.assertEqual(fake_model.invoke.call_count, 2)
        self.assertIsNone(result.final_answer)

        # The retry's prompt context carried the first attempt's structural
        # reason (the missing key name) so the model could fix exactly that.
        self.assertIn(
            "missing_ref_key:result.total", _invoked_prompt(fake_model, 1)
        )

        # The terminal error reflects the SECOND attempt's digit-in-text
        # failure specifically: the first attempt's missing-key reason must not
        # leak into state.error or into any state.error_reasons entry.
        self.assertEqual(
            result.error,
            "answer_generation_failed:digit_in_text_segment:0",
        )
        self.assertEqual(
            result.error_reasons,
            ["answer_generation_failed:digit_in_text_segment:0"],
        )
        self.assertNotIn("missing_ref_key", result.error)
        for reason in result.error_reasons:
            self.assertNotIn("missing_ref_key", reason)

    def test_two_model_exceptions_set_error_from_the_final_attempt(self):
        state = _answer_state()
        fake_model = mock.Mock()
        fake_model.invoke.side_effect = [
            TimeoutError("first attempt timed out"),
            ValueError("second attempt failed"),
        ]

        with _patched_answer_model(fake_model):
            result = assemble_answer(state)

        self.assertTrue(result.answer_generation_retried)
        self.assertEqual(fake_model.invoke.call_count, 2)
        self.assertEqual(result.error, "answer_generation_failed:ValueError")
        self.assertEqual(
            result.error_reasons, ["answer_generation_failed:ValueError"]
        )
        self.assertNotIn("TimeoutError", result.error)
        for reason in result.error_reasons:
            self.assertNotIn("TimeoutError", reason)


class AssembleAnswerScalarRenderTest(unittest.TestCase):
    """Node 7 rendering, scalar case: refs substituted, disclosures appended."""

    def test_scalar_render_substitutes_refs_and_appends_disclosures(self):
        state = _answer_state()
        fake_model = mock.Mock()
        fake_model.invoke.return_value = _valid_answer_segments()

        with _patched_answer_model(fake_model):
            result = assemble_answer(state)

        final = result.final_answer
        self.assertEqual(final, _expected_scalar_final_answer())
        # The intro carries the resolved, pre-formatted reference value (with
        # currency formatting intact) rather than a key name or a raw hole.
        self.assertIn("was £1,234,567.80.", final)
        self.assertIn("United Kingdom", final)
        # The disclosures section is appended verbatim as its own section.
        self.assertIn("Disclosures", final)
        self.assertIn("- The amounts were reported on a net basis.", final)

        # No raw template/segment artifacts leak into the rendered text: no
        # type discriminators, keys, or JSON braces from the segment dicts,
        # and no un-substituted reference key survives.
        for artifact in (
            '"type"',
            '"ref"',
            '"text"',
            '"content"',
            '"key"',
            "result.revenue",
            "{",
            "}",
            "[",
            "]",
        ):
            self.assertNotIn(artifact, final)

        self.assertIsNone(result.error)
        self.assertFalse(result.answer_generation_retried)
        self.assertEqual(fake_model.invoke.call_count, 1)


class AssembleAnswerGroupedRenderTest(unittest.TestCase):
    """Node 7 rendering, grouped case: intro, then table, then disclosures."""

    def test_grouped_render_orders_intro_table_then_disclosures(self):
        state = _grouped_answer_state()
        fake_model = mock.Mock()
        fake_model.invoke.return_value = _grouped_answer_segments()

        with _patched_answer_model(fake_model):
            result = assemble_answer(state)

        self.assertIsNone(result.error)
        self.assertFalse(result.answer_generation_retried)
        self.assertEqual(fake_model.invoke.call_count, 1)

        # 1. Intro: the model-authored sentence with every ref substituted.
        intro = (
            "Total revenue for the selected period was "
            "£1,000,000.00, broken down by country:"
        )
        # 2. Table: one plain-text data row per line, joined by one consistent
        #    delimiter, never Markdown. Cell text reuses the reference
        #    dictionary's exact formatted values (currency intact).
        table = "United Kingdom | £800,000.00\nGermany | £200,000.00"
        # 3. Disclosures: verbatim, in their existing order, own section.
        disclosures = (
            "Disclosures\n"
            "- The amounts were reported on a net basis.\n"
            "- The main result set was limited by the guardrail's "
            "row-limit enforcement."
        )
        self.assertEqual(
            result.final_answer, "\n\n".join([intro, table, disclosures])
        )

        # The three steps appear in exactly this order in the final string.
        intro_idx = result.final_answer.index(
            "£1,000,000.00, broken down by country:"
        )
        table_idx = result.final_answer.index("United Kingdom | £800,000.00")
        disclosures_idx = result.final_answer.index("Disclosures")
        self.assertLess(intro_idx, table_idx)
        self.assertLess(table_idx, disclosures_idx)

        # Plain text, not a Markdown table: no separator row, and no data row
        # begins or ends with the delimiter pipe.
        self.assertNotIn("| ---", result.final_answer)
        for line in table.splitlines():
            self.assertFalse(line.startswith("|"))
            self.assertFalse(line.endswith("|"))


class AssembleAnswerRenderErrorRetryTest(unittest.TestCase):
    """Render exceptions are failure reasons: retried once, then error path."""

    def test_render_exception_on_first_attempt_triggers_exactly_one_retry(self):
        state = _answer_state()
        fake_model = mock.Mock()
        fake_model.invoke.return_value = _valid_answer_segments()

        with _patched_answer_model(fake_model), mock.patch(
            "graph.node_assemble_answer._render_answer",
            side_effect=[
                RuntimeError("render exploded"),
                "second attempt rendered cleanly",
            ],
        ):
            result = assemble_answer(state)

        # The render failure on the first successful-validation attempt was
        # treated exactly like any other failure: the whole LLM call was
        # retried once (a different response could render cleanly), recorded
        # on the state -- it did not crash, and it did not silently leave an
        # empty or partial final answer.
        self.assertEqual(fake_model.invoke.call_count, 2)
        self.assertTrue(result.answer_generation_retried)
        self.assertIsNone(result.error)
        self.assertEqual(result.final_answer, "second attempt rendered cleanly")
        # The retry prompt context carries the render failure reason (only the
        # exception's class name -- a structural pointer, never model output).
        self.assertIn(
            "render_error:RuntimeError", _invoked_prompt(fake_model, 1)
        )

    def test_render_exception_on_both_attempts_routes_to_error_path(self):
        state = _answer_state()
        fake_model = mock.Mock()
        fake_model.invoke.return_value = _valid_answer_segments()

        with _patched_answer_model(fake_model), mock.patch(
            "graph.node_assemble_answer._render_answer",
            side_effect=[
                RuntimeError("first render exploded"),
                RuntimeError("retry render exploded"),
            ],
        ):
            result = assemble_answer(state)

        # Both attempts produced cleanly-valid segments but crashed while
        # rendering, so the node took the normal retry-then-error path with a
        # render_error reason from the final attempt -- no crash, no silent
        # partial answer.
        self.assertEqual(fake_model.invoke.call_count, 2)
        self.assertTrue(result.answer_generation_retried)
        self.assertIsNone(result.final_answer)
        self.assertEqual(
            result.error,
            "answer_generation_failed:render_error:RuntimeError",
        )
        self.assertEqual(
            result.error_reasons,
            ["answer_generation_failed:render_error:RuntimeError"],
        )


if __name__ == "__main__":
    unittest.main()
