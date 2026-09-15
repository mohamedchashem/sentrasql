"""Error path - ``handle_error`` (one node per module).

Deterministic, no-LLM terminal node for the graph's error branch: it runs only
when ``state.error`` is set (guardrail validation failure, intent-extraction
failure, execution failure, disclosure-assembly inconsistency, or
answer-generation failure) and translates the machine-readable reason into one
fixed, user-facing message stored in ``state.final_answer`` -- the same field
successful answers use -- so the user is never shown internal reason codes and
the system never guesses or returns a partial result.

The translation is a pure prefix lookup. The reason's top-level prefix (the
part before the first ``:`` -- for the bare guardrail reasons that carry no
``:``, the whole string) selects one pre-authored message from the module-level
``_PREFIX_MESSAGES`` mapping. No part of ``state.error`` (or of
``state.error_reasons`` when present) is ever interpolated into the user-facing
text, and any prefix absent from the mapping falls through silently to the
generic fallback message.

``state.error_reasons`` is treated purely as optional context: only Node 7's
``assemble_answer`` failure path populates it, and ``handle_error`` neither
reads nor writes it, so the common case (every other node's failures, where it
stays at its default empty list) and the assemble-answer case both behave
identically.
"""

from __future__ import annotations

from graph.state import GraphState

# Fixed, pre-authored user-facing messages. Every string the lookup can select
# is one of these constants -- never text dynamically constructed from
# ``state.error`` or ``state.error_reasons`` content.

# Selected for the ``intent_extraction_failed`` prefix (Node 2): the system
# could not turn the question into a query at all, so the actionable ask is a
# rephrase.
_INTENT_EXTRACTION_FAILED_MESSAGE = (
    "I wasn't able to understand that question — could you try rephrasing it?"
)

# Selected for every guardrail/validation rejection: the request itself could
# not be processed safely (a disallowed table/column/function, a wildcard
# select, an unconditioned join, an invalid intent, a disclosure-assembly
# inconsistency, ...).
_GUARDRAIL_REJECTION_MESSAGE = "That request couldn't be safely processed."

# Generic fallback for any prefix not covered above: something failed while
# processing the request (query execution, result-shape validation,
# answer generation, an as-yet-unknown prefix, ...).
_GENERIC_FALLBACK_MESSAGE = (
    "Something went wrong while processing that request — please try again."
)

# Machine-readable reason prefix set on ``state.error`` by Node 2
# (``extract_query_intent``) after its one retry is exhausted.
_INTENT_EXTRACTION_FAILED_PREFIX = "intent_extraction_failed"

# Guardrail/validation reason prefixes that may appear as the top-level prefix
# of ``state.error``. This is the full taxonomy across the codebase, not a
# guess: the bare and ``<prefix>:<detail>`` rejection reasons that
# ``db.guardrails.validate_sql`` returns and ``validate_guardrails`` mirrors
# verbatim (``unparseable_sql``, ``no_statement``, ``multiple_statements``,
# ``not_a_select``, ``disallowed_table``, ``disallowed_column``,
# ``disallowed_function``, ``wildcard_select``, ``unconditioned_join``),
# ``compile_sql``'s early-validation gates (``invalid_intent:...``), and
# ``assemble_disclosures``' rule/companion hard-fail
# (``disclosure_assembly_inconsistency:<rule>``).
_GUARDRAIL_REJECTION_PREFIXES = frozenset(
    {
        "unparseable_sql",
        "no_statement",
        "multiple_statements",
        "not_a_select",
        "disallowed_table",
        "disallowed_column",
        "disallowed_function",
        "wildcard_select",
        "unconditioned_join",
        "invalid_intent",
        "disclosure_assembly_inconsistency",
    }
)

# Static, module-level ``<prefix> -> message>`` lookup used by ``handle_error``.
# Keyed by the reason prefix matched against ``state.error`` before the first
# ``:`` (bare guardrail reasons match by their whole string). Prefixes absent
# from this mapping fall through to ``_GENERIC_FALLBACK_MESSAGE``.
_PREFIX_MESSAGES: dict[str, str] = {
    _INTENT_EXTRACTION_FAILED_PREFIX: _INTENT_EXTRACTION_FAILED_MESSAGE,
    **{
        prefix: _GUARDRAIL_REJECTION_MESSAGE
        for prefix in _GUARDRAIL_REJECTION_PREFIXES
    },
}


def handle_error(state: GraphState) -> GraphState:
    """Error path. Deterministic, no LLM. Pure, single-pass, no-retry terminal
    node: it never calls out to a model, never retries, and has no downstream
    work -- it composes the user-facing failure message and stops. Runs when
    ``state.error`` is set (the graph only routes here when ``state.error`` is
    not ``None``), so no passthrough gate is needed and the node is never
    bypassed.

    Matches ``state.error`` against the module-level ``_PREFIX_MESSAGES``
    lookup by prefix (the part before the first ``:``) and stores the selected
    fixed message in ``state.final_answer``. The message is always one of the
    pre-authored constants -- never a string interpolated from ``state.error``
    or ``state.error_reasons``. Any prefix not in the lookup falls through
    silently to the generic fallback: never raises, never produces an empty
    message. ``state.error_reasons`` is optional context (populated only by
    ``assemble_answer``'s failure path) and is neither read nor modified, so
    its presence or absence cannot change the outcome.
    """
    # ``state.error`` is guaranteed set whenever the graph reaches this node.
    # The ``or ""`` guard merely keeps the lookup total if the function is ever
    # invoked directly on a state carrying no error -- it still selects the
    # generic fallback rather than raising, and never yields an empty message.
    prefix = (state.error or "").partition(":")[0]
    state.final_answer = _PREFIX_MESSAGES.get(prefix, _GENERIC_FALLBACK_MESSAGE)
    return state
