"""Node 7 - ``assemble_answer`` (one node per module).

Still a stub that returns ``state`` unchanged (this file intentionally
exists so the upcoming implementation task lands in a small, clean
module from the start). The intended contract: LLM call, constrained --
take ``state.main_results`` and ``state.disclosures`` as structured
input and compose ``state.final_answer``, phrasing only numbers that
originate in those fields."""

from __future__ import annotations

from graph.state import GraphState


def assemble_answer(state: GraphState) -> GraphState:
    """Node 7. LLM call, constrained. Takes state.main_results and state.disclosures as structured input and composes state.final_answer. Only responsible for phrasing — every number or exclusion count in the output must originate from state.disclosures or state.main_results, never invented in prose."""

    # TODO: implement
    return state
