"""Wire the skeleton LangGraph pipeline (Nodes 2-7 + 6.5 + error path).

The node functions are still stubs that return state unchanged; this module
only defines the graph topology: a linear sequence with two conditional
branches that route to ``handle_error`` on guardrail failure or execution
error, then always terminate at ``END``.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from graph.nodes import (
    assemble_answer,
    assemble_disclosures,
    compile_sql,
    detect_applicable_rules,
    execute_queries,
    extract_query_intent,
    handle_error,
    validate_guardrails,
)
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
    graph.add_edge("assemble_answer", END)
    graph.add_edge("handle_error", END)

    return graph.compile()


sentrasql_graph = build_graph()


if __name__ == "__main__":
    initial_state = GraphState(raw_query="test query")
    result = sentrasql_graph.invoke(initial_state)
    print(f"final_answer: {result.get('final_answer')}")
    print("full state:")
    print(result)
