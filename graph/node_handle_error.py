"""Error path - ``handle_error`` (one node per module).

Still a stub that returns ``state`` unchanged. The intended contract:
deterministic, no LLM -- run when ``state.error`` is set and compose a
user-facing message into ``state.final_answer`` that states the system
could not produce a reliable answer rather than guessing or returning
a partial result."""

from __future__ import annotations

from graph.state import GraphState


def handle_error(state: GraphState) -> GraphState:
    """Error path. Deterministic, no LLM. Runs when state.error is set (guardrail validation failure or companion query execution failure). Composes a user-facing message from state.error into state.final_answer, explicitly stating that the system could not produce a reliable answer rather than guessing or returning a partial result. Must never be bypassed when state.error is set."""

    # TODO: implement
    return state
