"""Node 2 - ``extract_query_intent`` (one node per module).

LLM-backed intent extraction: assembles the stable system prompt from
the query text, invokes the strict structured-output model, runs the
parsed intent through post-parse normalization/validation, and stores
the normalized intent in ``state.query_intent``. Contains no prompt
content and no normalization logic of its own -- it only wires the
independently-built pieces (``build_system_prompt``,
``get_intent_model``, ``normalize_and_validate_intent``) plus the
retry/error routing (DESIGN_LOG.md section 16).

Only this module defines the Node 2 function; the module-level helper and
constant set is limited to what ``extract_query_intent`` alone
depends on (one node per module, matching db/ and the Node 2
prompt-content modules)."""

from __future__ import annotations

from langchain_core.messages import SystemMessage

from graph.intent_normalizer import normalize_and_validate_intent
from graph.llm import get_intent_model
from graph.state import GraphState, QueryIntent
from graph.system_prompt import build_system_prompt


def extract_query_intent(state: GraphState) -> GraphState:
    """Node 2. LLM call.

    Assembles the stable system prompt from the query text, invokes the strict
    structured-output model, runs the parsed intent through post-parse
    normalization/validation, and stores the normalized intent in
    ``state.query_intent``. This node contains no new prompt content and no
    new normalization logic -- it only wires the independently-built pieces
    (``build_system_prompt``, ``get_intent_model``,
    ``normalize_and_validate_intent``) plus the retry/error routing.

    Error passthrough mirrors ``validate_guardrails``: a state that already
    carries ``state.error`` is returned completely untouched (no prompt
    build, no model call).

    Retry policy (DESIGN_LOG.md section 16): any failure -- a model/parse
    exception or a normalization reason -- is retried exactly once with the
    *identical* call configuration (same prompt text, same messages, same
    model object), and the retry is recorded via
    ``state.intent_extraction_retried``. A second failure sets
    ``state.error`` to ``intent_extraction_failed:<underlying_reason>`` and
    never sets ``state.query_intent``.

    The query text used is ``state.normalized_query`` when non-empty, falling
    back to ``state.raw_query``: language normalization is a deferred v1
    feature, so in the current graph the entry state only ever carries
    ``raw_query``.
    """

    # Error passthrough: never touch a state already routing toward the error
    # path -- no prompt build, no model call, no field mutation.
    if state.error is not None:
        return state

    # The v1 graph entry carries only raw_query (language normalization is
    # deferred); once a normalization node exists it populates
    # normalized_query and this preference automatically takes effect.
    query_text = state.normalized_query if state.normalized_query else state.raw_query

    # Build the prompt once and reuse the identical messages for both
    # attempts: build_system_prompt is deterministic, and the retry must reuse
    # the exact same call configuration -- never a fresh/different attempt.
    prompt = build_system_prompt(query_text)
    model = get_intent_model()
    messages = [SystemMessage(content=prompt)]

    def _attempt() -> tuple[QueryIntent | None, str | None]:
        """Run one extraction attempt. Returns (intent, None) or (None, reason).

        ``reason`` is the underlying failure detail in machine-readable form:
        an exception's class name for a model/parse failure, the qualified
        name of an unexpected return type, or the exact
        ``intent_inconsistency:...`` reason from ``normalize_and_validate_intent``.
        """
        try:
            intent = model.invoke(messages)
        except Exception as exc:  # network error, timeout, structural parse failure
            return None, type(exc).__name__
        if not isinstance(intent, QueryIntent):
            return (
                None,
                "unexpected_result_type:"
                f"{type(intent).__module__}.{type(intent).__name__}",
            )
        normalized, reason = normalize_and_validate_intent(intent)
        if reason is not None:
            return None, reason
        return normalized, None

    intent, failure_reason = _attempt()
    if failure_reason is None:
        state.query_intent = intent
        return state

    # First attempt failed -> exactly one retry, identical configuration.
    state.intent_extraction_retried = True
    intent, failure_reason = _attempt()
    if failure_reason is None:
        state.query_intent = intent
        return state

    # Second failure: route to the error path and never set query_intent.
    state.error = f"intent_extraction_failed:{failure_reason}"
    return state
