"""Graph-topology tests for graph.build (the compiled, wired-together pipeline).

Every other test file in this suite exercises a single node function in
isolation. These tests are the first to run the graph *wired together*: they
monkeypatch all eight node functions in ``graph.build`` -- the seven
intermediate nodes and ``handle_error`` -- with lightweight stubs (no LLM, no
SQL, no guardrail engine, no reference dictionary), then compile a fresh graph
via ``build_graph()``, so only the real routing/topology is under test.

Covered wiring contracts:

1. Linear happy path reaches END. When every stubbed node passes the state
   through unchanged and the stubbed ``assemble_answer`` succeeds (writes
   ``state.final_answer`` and leaves ``state.error`` ``None``), the compiled
   graph reaches ``END``: the final state carries the stub's final answer and
   ``state.error`` is still ``None``. The ``handle_error`` stub never runs.

2. assemble_answer's error branch routes through handle_error. When the
   stubbed ``assemble_answer`` sets ``state.error`` (the real node's
   second-failure contract) and nothing else, the compiled graph must NOT
   dead-end at ``END`` with the error set but no answer composed -- which is
   exactly what the old unconditional ``assemble_answer -> END`` edge did. The
   conditional edge added by ``graph.build._route_after_answer`` routes the
   errored state to the ``handle_error`` stub, which composes the mapped
   user-facing message into ``state.final_answer`` while ``state.error`` stays
   populated.
"""

import unittest
from contextlib import ExitStack, contextmanager
from unittest import mock

import graph.build as build
from graph.state import GraphState

# Fixed user-facing message the handle_error stub composes. It mirrors the real
# node's mapping for the ``answer_generation_failed`` prefix used below (that
# prefix is absent from the real ``_PREFIX_MESSAGES`` lookup, so it falls
# through to the generic fallback). The real node's full prefix -> message
# mapping is unit-tested in tests/test_handle_error.py; this stub only needs to
# prove the graph routed there.
_GENERIC_FALLBACK_MESSAGE = (
    "Something went wrong while processing that request — please try again."
)

# Distinctive final answer the success stub writes, so the happy-path assertion
# cannot pass by accident (nothing upstream ever writes ``final_answer``).
_STUBBED_ANSWER_TEXT = "Answer composed by the stubbed assemble_answer."

_QUERY = "What was total revenue in the United Kingdom?"

# The six intermediate node names whose stubs are identical in every test.
_PASS_THROUGH_NODE_NAMES = (
    "extract_query_intent",
    "detect_applicable_rules",
    "compile_sql",
    "validate_guardrails",
    "execute_queries",
    "assemble_disclosures",
)


def _pass_through(state: GraphState) -> GraphState:
    """Identity node stub: hand the state on unchanged to the next node."""
    return state


def _handle_error_stub(state: GraphState) -> GraphState:
    """Lightweight stand-in for the real handle_error node.

    Composes the fixed generic message into ``state.final_answer``, exactly the
    real node's outcome for the ``answer_generation_failed`` prefix.
    """
    state.final_answer = _GENERIC_FALLBACK_MESSAGE
    return state


def _as_state(result: GraphState | dict) -> GraphState:
    """Normalize LangGraph's ``invoke()`` dict back into the typed GraphState."""
    return result if isinstance(result, GraphState) else GraphState(**result)


@contextmanager
def _compiled_stubbed_graph(assemble_answer_stub):
    """Yield a fresh ``build_graph()`` whose eight node functions are all stubs.

    ``build_graph`` resolves each node from the ``graph.build`` module's own
    globals at call time, so the patches must target those module attributes
    (patching the source ``graph.node_*`` modules would have no effect). Only
    ``assemble_answer``'s stub differs between tests; the six intermediates are
    identity stubs and ``handle_error`` is the fixed mapping stub.
    """
    with ExitStack() as stack:
        for name in _PASS_THROUGH_NODE_NAMES:
            stack.enter_context(mock.patch.object(build, name, _pass_through))
        stack.enter_context(
            mock.patch.object(build, "assemble_answer", assemble_answer_stub)
        )
        stack.enter_context(mock.patch.object(build, "handle_error", _handle_error_stub))
        yield build.build_graph()


class BuildGraphTopologyTest(unittest.TestCase):
    """End-to-end routing through the compiled graph, nodes all stubbed."""

    def test_successful_assemble_answer_reaches_end(self):
        def assemble_answer_success(state: GraphState) -> GraphState:
            state.final_answer = _STUBBED_ANSWER_TEXT
            return state

        with _compiled_stubbed_graph(assemble_answer_success) as graph:
            result = graph.invoke(GraphState(raw_query=_QUERY))

        state = _as_state(result)
        self.assertEqual(state.final_answer, _STUBBED_ANSWER_TEXT)
        self.assertIsNone(state.error)

    def test_assemble_answer_error_routes_through_handle_error(self):
        def assemble_answer_failure(state: GraphState) -> GraphState:
            state.error = "answer_generation_failed:render_error:RuntimeError"
            state.error_reasons = [
                "answer_generation_failed:render_error:RuntimeError"
            ]
            return state

        with _compiled_stubbed_graph(assemble_answer_failure) as graph:
            result = graph.invoke(GraphState(raw_query=_QUERY))

        state = _as_state(result)
        # The stubbed assemble_answer wrote only error/error_reasons -- never
        # final_answer -- so a populated final_answer can only have been
        # composed by the handle_error stub. Its presence is the proof that the
        # conditional edge routed assemble_answer -> handle_error instead of
        # dead-ending at END (the pre-fix behavior left error set and
        # final_answer None).
        self.assertEqual(
            state.error, "answer_generation_failed:render_error:RuntimeError"
        )
        self.assertEqual(
            state.error_reasons, ["answer_generation_failed:render_error:RuntimeError"]
        )
        self.assertEqual(state.final_answer, _GENERIC_FALLBACK_MESSAGE)


if __name__ == "__main__":
    unittest.main()
