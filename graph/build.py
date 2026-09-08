"""Wire the skeleton LangGraph pipeline (Nodes 2-7 + 6.5 + error path).

The node functions are still stubs that return state unchanged; this module
only defines the graph topology: a linear sequence with three conditional
branches that route to ``handle_error`` on guardrail failure, execution
error, or answer-generation failure, then always terminate at ``END``.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from graph.node_assemble_answer import assemble_answer
from graph.node_assemble_disclosures import assemble_disclosures
from graph.node_compile_sql import compile_sql
from graph.node_detect_applicable_rules import detect_applicable_rules
from graph.node_execute_queries import execute_queries
from graph.node_extract_query_intent import extract_query_intent
from graph.node_handle_error import handle_error
from graph.node_validate_guardrails import validate_guardrails
from graph.state import GraphState


def _route_after_guardrails(state: GraphState) -> str:
    """Return the branch after guardrail validation."""
    if state.guardrail_status == "failed":
        return "error"
    return "execute"


def _route_after_execution(state: GraphState) -> str:
    """Return the branch after query execution."""
    if state.error is not None:
        return "error"
    return "continue"


def _route_after_answer(state: GraphState) -> str:
    """Return the branch after answer assembly."""
    if state.error is not None:
        return "error"
    return "continue"


def build_graph():
    """Construct, wire, and compile the SentraSQL state graph."""
    graph = StateGraph(GraphState)

    graph.add_node("extract_query_intent", extract_query_intent)
    graph.add_node("detect_applicable_rules", detect_applicable_rules)
    graph.add_node("compile_sql", compile_sql)
    graph.add_node("validate_guardrails", validate_guardrails)
    graph.add_node("execute_queries", execute_queries)
    graph.add_node("assemble_disclosures", assemble_disclosures)
    graph.add_node("assemble_answer", assemble_answer)
    graph.add_node("handle_error", handle_error)

    graph.set_entry_point("extract_query_intent")

    graph.add_edge("extract_query_intent", "detect_applicable_rules")
    graph.add_edge("detect_applicable_rules", "compile_sql")
    graph.add_edge("compile_sql", "validate_guardrails")

    graph.add_conditional_edges(
        "validate_guardrails",
        _route_after_guardrails,
        {"error": "handle_error", "execute": "execute_queries"},
    )

    graph.add_conditional_edges(
        "execute_queries",
        _route_after_execution,
        {"error": "handle_error", "continue": "assemble_disclosures"},
    )

    graph.add_edge("assemble_disclosures", "assemble_answer")

    graph.add_conditional_edges(
        "assemble_answer",
        _route_after_answer,
        {"error": "handle_error", "continue": END},
    )

    graph.add_edge("handle_error", END)

    return graph.compile()


sentrasql_graph = build_graph()


if __name__ == "__main__":
    """Run the full SentraSQL pipeline end-to-end on one real query.

    Usage: ``python -m graph.build "what was total revenue in the United Kingdom?"``

    The query is taken from ``sys.argv`` (all arguments joined by spaces, so
    quoting the query as one argument works), defaulting to a straightforward
    low-ambiguity query when no argument is given. The compiled
    ``sentrasql_graph`` is invoked on a fresh ``GraphState(raw_query=query)``,
    and the complete resulting state is printed -- every field, in model order,
    not just ``final_answer`` -- so each node's output can be inspected.
    """
    import json
    import sys

    query = " ".join(sys.argv[1:]).strip()
    if not query:
        query = "What was total revenue in the United Kingdom?"

    print(f"raw_query: {query!r}")
    print("invoking sentrasql_graph ...")

    result = sentrasql_graph.invoke(GraphState(raw_query=query))
    # LangGraph returns a plain state dict; normalize it back into the typed
    # GraphState so model_dump(mode="json") gives clean, fully-serialized output
    # (nested models expanded, RuleName enums as their values).
    state = result if isinstance(result, GraphState) else GraphState(**result)
    dumped = state.model_dump(mode="json")

    print("\n=== FULL RESULTING STATE ===")
    for key, value in dumped.items():
        print(f"\n--- {key} ---")
        print(json.dumps(value, indent=2, ensure_ascii=False))

    print("\n=== SUMMARY ===")
    print(f"error:        {state.error!r}")
    print(f"guardrail:    {state.guardrail_status}")
    print(f"final_answer: {state.final_answer!r}")
