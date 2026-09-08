"""Node 7 - ``assemble_answer`` (one node per module).

LLM-backed answer composition. This module implements the passthrough gate
and, once it passes, the full generation-and-rendering pipeline: it builds the
two pieces the constrained LLM call consumes -- the reference dictionary from
the executed results (``graph.reference_dict``) and the per-call answer prompt
that lists that dictionary (``graph.answer_prompt``) -- invokes the strict
structured-output model (``graph.llm.get_answer_model``) with that prompt,
runs the emitted segments through the deterministic gate
``graph.segment_validator.validate_segments`` with exactly one retry on
failure (recorded via ``state.answer_generation_retried``), and finally
renders the validated segments into ``state.final_answer``. Rendering is real
deterministic code and therefore runs inside the attempt loop: an exception
raised after validation passed is a failure reason like any other
(``render_error:<ExceptionClassName>``) and is retried exactly once, because a
different model response can render cleanly where the previous one failed.
"""

from __future__ import annotations

from langchain_core.messages import SystemMessage

from graph.answer_prompt import build_answer_prompt
from graph.llm import get_answer_model
from graph.reference_dict import build_reference_dict
from graph.segment_validator import validate_segments
from graph.state import AnswerSegments, Disclosure, GraphState, QueryIntent

# Node-level machine-readable error prefix for this node's failure reasons,
# mirroring Node 2's ``intent_extraction_failed:`` for its sibling flag
# ``answer_generation_retried``: ``state.error`` and every
# ``state.error_reasons`` entry use the same exact ``<prefix>:<detail>`` form.
_ANSWER_GENERATION_FAILED_PREFIX = "answer_generation_failed"

# Machine-readable reason prefix for an exception raised by the deterministic
# render step (``_render_answer``) after validation already passed. Rendering
# is real code that can fail for reasons ``validate_segments`` never checked
# (a result shape it did not anticipate, a lookup that somehow misses), so an
# exception there is its own failure reason -- routed through the same
# retry-then-error path as every other failure type.
_RENDER_ERROR_PREFIX = "render_error"

# Plain-text representation choices for the deterministic sections rendered
# after the model-authored intro. The grouped table is deliberately NOT
# Markdown: no leading/trailing pipes and no header/separator row -- each data
# row is one line and its cells are joined by this single consistent delimiter.
_ROW_DELIMITER = " | "

# Heading of the answer's closing disclosures section (rendered verbatim from
# ``state.disclosures`` in every case, never generated or rephrased by the
# model).
_DISCLOSURES_HEADING = "Disclosures"


def _retry_prompt_context(retry_reasons: list[str]) -> str:
    """Build the plain-text block appended to the retry attempt's prompt.

    The block tells the model its previous attempt was rejected and lists the
    exact deterministic failure reasons as structural pointers only (e.g.
    ``missing_ref_key:<key>``, ``digit_in_text_segment:<index>``, and
    ``render_error:<ExceptionClassName>``) -- never the model's own generated
    text or numbers echoed back verbatim.
    """
    lines = [
        "",
        "PREVIOUS ATTEMPT REJECTED -- RETRY REQUIRED",
        "===========================================",
        "Your previous attempt failed the deterministic answer checks "
        "(segment validation or rendering) for the reason(s) listed below. "
        "Fix the segment list for exactly those reasons and resubmit; do not "
        "repeat the previous attempt unchanged.",
        "",
    ]
    lines.extend(f"- {reason}" for reason in retry_reasons)
    return "\n".join(lines)


def _render_intro(
    segments: list[dict], references: dict[str, str]
) -> str:
    """Render validated answer segments into the model-authored intro prose.

    Walks ``segments`` in order and concatenates each segment's contribution
    into one string: a ``text`` segment contributes its literal ``content``
    verbatim and a ``ref`` segment contributes the reference dictionary's
    value for its ``key`` (``references[key]``). Every ``ref`` key was already
    checked to exist in this same ``references`` dictionary by
    ``validate_segments``, so the substitution cannot produce a hole; a miss
    here would mean validation and rendering disagreed about the dictionary,
    which is exactly the kind of real-code failure the caller treats as a
    ``render_error`` reason.

    Args:
        segments: The validated answer-template segment list -- each item is
            either ``{"type": "text", "content": str}`` or
            ``{"type": "ref", "key": str}``.
        references: The flat substitution dictionary built by
            ``graph.reference_dict.build_reference_dict`` for this call.

    Returns:
        The substituted intro as a single string.
    """
    parts: list[str] = []
    for segment in segments:
        if segment["type"] == "text":
            parts.append(segment["content"])
        else:
            parts.append(references[segment["key"]])
    return "".join(parts)


def _render_table(
    main_results: list[dict] | dict | None,
    query_intent: QueryIntent,
    references: dict[str, str],
) -> str:
    """Render a grouped query's row-level breakdown as deterministic plain text.

    Runs only for grouped queries (``query_intent.group_by`` non-empty) and
    renders ``main_results["rows"]`` -- the ``execute_queries`` row-level
    breakdown -- as plain text, never Markdown: each data row is one line and
    its cells are joined by the module's single consistent delimiter
    (``_ROW_DELIMITER``). The block is inserted immediately after the intro
    and is never touched by the model.

    Column order is deterministic: the intent's ``group_by`` columns in their
    declared order followed by the metric column, matching the projection the
    rest of the pipeline compiled. Row order is the breakdown's own order --
    the same row-ordinal order ``result.rows.<ordinal>.*`` addresses -- keeping
    the table aligned with any per-row references the intro cites. Cell text is
    the exact pre-formatted value the reference dictionary already holds for
    that row/column (``result.rows.<ordinal>.<column>``), so the table and the
    model's substituted prose can never display the same number differently.

    Returns an empty string when the query is not grouped or when no breakdown
    exists (a zero-row grouped query stores the empty list, and an empty
    breakdown has no data rows to render).

    Raises:
        ValueError: On a grouped result shape that cannot be rendered -- a bare
            non-empty row list (the pre-DESIGN_LOG-§20 shape with no total), a
            wrapper whose ``rows`` value is not a list, a non-dict row, or a
            row missing one of the projected columns.
    """
    if not query_intent.group_by:
        return ""
    if not main_results:
        # ``None`` (never executed) or the empty list a zero-row grouped query
        # stores: no breakdown exists, so there is no row data to render.
        return ""
    if isinstance(main_results, list):
        raise ValueError(
            "Grouped main_results with rows must be the execute_queries "
            "wrapper dict (\"rows\" / \"total\"), got a bare list."
        )
    rows = main_results.get("rows")
    if not rows:
        return ""
    if not isinstance(rows, list):
        raise ValueError(
            "Grouped main_results wrapper must carry a list-valued \"rows\" "
            f"entry, got {type(rows).__name__}."
        )
    columns = list(query_intent.group_by) + [query_intent.metric]
    lines: list[str] = []
    for ordinal, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(
                "Every grouped row must be a column-name-keyed record dict, "
                f"got {type(row).__name__} at ordinal {ordinal}."
            )
        missing = [column for column in columns if column not in row]
        if missing:
            raise ValueError(
                f"Row at ordinal {ordinal} carries no projected column(s) "
                f"{missing} (keys: {sorted(row)})."
            )
        cells = [
            references[f"result.rows.{ordinal}.{column}"]
            for column in columns
        ]
        lines.append(_ROW_DELIMITER.join(cells))
    return "\n".join(lines)


def _render_disclosures(disclosures: list[Disclosure]) -> str:
    """Render ``state.disclosures`` verbatim into the answer's closing section.

    The disclosures step is deterministic and unconditional -- it always runs,
    in every case, after the intro (and, for grouped queries, the table), and
    the model never touches it. Each disclosure contributes its user-facing
    ``detail`` text verbatim, one bullet per disclosure, in the exact order
    ``assemble_disclosures`` produced, under a fixed section heading. With no
    disclosures the section has nothing to state and contributes nothing to
    the joined answer.

    Returns:
        The disclosures section as a single string, or the empty string when
        ``disclosures`` is empty.
    """
    if not disclosures:
        return ""
    lines = [_DISCLOSURES_HEADING]
    lines.extend(f"- {disclosure.detail}" for disclosure in disclosures)
    return "\n".join(lines)


def _join_sections(*sections: str) -> str:
    """Join the non-empty final-answer sections, blank-line separated."""
    return "\n\n".join(section for section in sections if section)


def _render_answer(
    state: GraphState,
    validated_segments: list[dict],
    references: dict[str, str],
) -> str:
    """Compose the final answer string from the validated segments and state.

    Three explicit steps, rendered in fixed order:

    1. Intro -- ``_render_intro`` substitutes every ``ref`` segment's value
       from ``references`` and concatenates it with the ``text`` segments'
       content, in order, into one string.
    2. Table -- only when ``state.query_intent.group_by`` is non-empty:
       ``_render_table`` renders ``state.main_results``' row data as plain
       text, inserted immediately after the intro. Never touched by the model.
    3. Disclosures -- ``_render_disclosures`` appends ``state.disclosures``
       verbatim, in their existing order, joined as their own section; always.

    The non-empty sections are joined into one string, which the caller stores
    in ``state.final_answer``. This function is real code, not a re-validation
    gate: exceptions raised here are caught by the caller and treated as a
    ``render_error`` failure reason.
    """
    intro = _render_intro(validated_segments, references)
    table = _render_table(state.main_results, state.query_intent, references)
    disclosures = _render_disclosures(state.disclosures)
    return _join_sections(intro, table, disclosures)


def assemble_answer(state: GraphState) -> GraphState:
    """Node 7. Gate, LLM answer-segment generation, validation, retry, render.

    Gate: a state that already carries ``state.error``, a plan that never
    passed guardrail validation (``guardrail_status != "passed"``), a main
    query that never produced ``state.main_results``, or a disclosure list
    that is not present is returned completely untouched. The ``disclosures``
    clause is technically dead for any model-valid state -- the field is typed
    ``list[Disclosure]`` with an empty-list default, so validation can never
    yield ``None`` -- but it is kept deliberately: pydantic does not validate
    attribute *assignment*, so a runtime ``state.disclosures = None`` would
    otherwise pass the gate and crash the later composition step, and naming
    the full precondition explicitly is the same gate-documentation
    discipline ``assemble_disclosures`` follows.

    Setup (gate passed): builds the flat reference dictionary by genuinely
    calling ``build_reference_dict(state.main_results, state.query_intent)``
    with this state's real data, then builds this call's prompt from that
    actual dictionary via ``build_answer_prompt``.

    Generation, validation, and rendering: invokes the strict structured-output
    model (``get_answer_model``) with a SystemMessage built from that prompt
    and runs the emitted ``AnswerSegments`` through
    ``validate_segments(segments, references)``. On success the validated
    segment list is rendered into ``state.final_answer`` by ``_render_answer``
    -- a deterministic three-step composition: the model-authored intro
    (validated segments substituted against ``references``), the grouped
    row-data table when ``state.query_intent.group_by`` is non-empty, and the
    disclosures section (``state.disclosures`` verbatim, in existing order).
    Rendering runs inside the attempt: it is real code that can still fail
    after validation passed, so an exception there is treated as a failure
    reason (``render_error:<ExceptionClassName>``) like any other.

    Retry policy (same discipline as Node 2): any failure -- a model/parse
    exception, a non-``None`` ``validate_segments`` reason, or a render
    exception -- is retried exactly once with the identical call configuration
    (same model object and base prompt), appending the first attempt's
    collected failure reasons as plain text to the retry's prompt context
    (structural pointers only, never the model's generated text/numbers echoed
    back). A render exception on the first successful-validation attempt
    therefore triggers the same one retry as any other failure type, because a
    different model response can render cleanly. The retry is recorded via
    ``state.answer_generation_retried``.

    Second failure: routes to the error path. ``state.error`` carries the
    first/primary collected reason of the FINAL attempt and
    ``state.error_reasons`` the full reason list of that same final attempt --
    never the first attempt's reasons (each entry uses the exact
    ``answer_generation_failed:<underlying_reason>`` form, so a render
    exception surfaces as ``answer_generation_failed:render_error:<Class>``).
    """

    # Passthrough gate -- never compose an answer from an errored state, an
    # unvalidated plan, unexecuted results, or a missing disclosure list.
    if (
        state.error is not None
        or state.guardrail_status != "passed"
        or state.main_results is None
        or state.disclosures is None
    ):
        return state

    # --- Setup only: no LLM invocation, no rendering, no retry yet. ---
    # 1. Reference dictionary from the state's own executed results and parsed
    #    intent -- the real builder over the real state data, never a
    #    placeholder or example dictionary.
    references = build_reference_dict(state.main_results, state.query_intent)

    # 2. Per-call answer prompt: every key that actually exists in the
    #    dictionary for THIS query (key name plus value), the segment-structure
    #    instructions, and the no-digits-in-text rule restated plainly.
    prompt = build_answer_prompt(references)

    # --- LLM invocation with exactly one retry, render included per attempt. ---
    # The bound model and the base prompt are built once and shared by both
    # attempts: get_answer_model() is deterministic, and the retry must reuse
    # the identical call configuration -- the only difference is the retry's
    # prompt context, which appends the first attempt's collected failure
    # reasons as plain text so the model can fix exactly what failed.
    model = get_answer_model()

    def _attempt(
        retry_reasons: list[str] | None,
    ) -> tuple[list[dict] | None, str | None, list[str]]:
        """Run one answer-generation attempt, including the render step.

        Returns ``(validated_segments, rendered_answer, [])`` when the emitted
        segments pass ``validate_segments`` against ``references`` AND the
        deterministic render step (``_render_answer``) completes, or
        ``(None, None, reasons)`` on failure. ``reasons`` holds THIS attempt's
        collected failure reasons only -- a validator reason
        (``missing_ref_key:<key>`` or ``digit_in_text_segment:<index>``) for a
        rejection, a ``render_error:<ExceptionClassName>`` reason when the
        otherwise-valid segments failed to render, or the exception class name
        for a model/parse failure -- and is never empty on failure.
        """
        attempt_prompt = prompt
        if retry_reasons is not None:
            attempt_prompt = prompt + _retry_prompt_context(retry_reasons)
        messages = [SystemMessage(content=attempt_prompt)]
        try:
            answer = model.invoke(messages)
        except Exception as exc:
            # Network error, timeout, structural parse failure: never echo the
            # model's output back at the model -- only the exception's class.
            return None, None, [type(exc).__name__]
        if not isinstance(answer, AnswerSegments):
            return (
                None,
                None,
                [f"unexpected_result_type:{type(answer).__module__}."
                 f"{type(answer).__name__}"],
            )
        validated_segments = answer.model_dump()["segments"]
        validation_reason = validate_segments(validated_segments, references)
        if validation_reason is not None:
            return None, None, [validation_reason]
        # Validated -> render. Rendering is real code that can still fail for
        # reasons validation never checked (a result shape it did not
        # anticipate, a lookup that somehow misses), so it is part of THIS
        # attempt: an exception is a failure reason like any other and is given
        # the same one retry as a validation or model failure.
        try:
            rendered_answer = _render_answer(
                state, validated_segments, references
            )
        except Exception as exc:
            return (
                None,
                None,
                [f"{_RENDER_ERROR_PREFIX}:{type(exc).__name__}"],
            )
        return validated_segments, rendered_answer, []

    # First attempt.
    _, rendered_answer, attempt_reasons = _attempt(retry_reasons=None)
    if not attempt_reasons:
        # Success reached on the first attempt: render already ran, so the
        # only state mutation is the finished final answer.
        state.final_answer = rendered_answer
        return state

    # First attempt failed -> exactly one retry, identical call configuration
    # (same model object, same base prompt), recorded on the state.
    state.answer_generation_retried = True
    _, rendered_answer, attempt_reasons = _attempt(
        retry_reasons=attempt_reasons
    )
    if not attempt_reasons:
        # Success reached on the retry: render already ran, so the state
        # mutations are the retry flag above and the finished final answer.
        state.final_answer = rendered_answer
        return state

    # Second failure: route to the error path. state.error carries the
    # first/primary collected reason of the FINAL attempt and
    # state.error_reasons carries the full reason list of that same final
    # attempt -- never the first attempt's reasons. A render exception on both
    # attempts lands here as ``answer_generation_failed:render_error:<Class>``.
    terminal_reasons = [
        f"{_ANSWER_GENERATION_FAILED_PREFIX}:{reason}"
        for reason in attempt_reasons
    ]
    state.error = terminal_reasons[0]
    state.error_reasons = terminal_reasons

    return state

