"""Talk to the SentraSQL graph from the dashboard.

The single home of every ``sentrasql_graph`` invocation made by the dashboard:
``ask()`` turns one user question string into a ``QueryResult`` -- either the
resulting ``GraphState`` (from which the UI reads ``final_answer``/``error``)
or, when the invocation itself fails, a clean error indicator carrying a fixed,
non-technical message. No exception text, traceback, or internal reason code
ever crosses this module's boundary, and ``app.py`` never needs a try/except:
``ask`` is total. Because UI components import this module rather than
``graph.build`` directly, future dashboard features (step tracker, detail
panel, sample questions, ...) all share one invocation path and can never
duplicate graph-calling logic across their own files.
"""

from __future__ import annotations

from dataclasses import dataclass

from graph.build import sentrasql_graph
from graph.state import GraphState

# Fixed, pre-authored user-facing failure messages. Every string stored on
# ``QueryResult.error`` is one of these constants -- never text built from the
# exception itself -- so the UI is guaranteed non-technical copy.

# Returned when the caller passes a blank question (nothing to run).
_EMPTY_QUESTION_MESSAGE = "Please type a question before pressing Ask."

# Returned when the graph cannot even start because the service key is missing
# (``os.environ["DEEPSEEK_API_KEY"]`` raises ``KeyError`` at model-construction
# time inside a node). The actionable fix is configuration, not a retry.
_CONFIGURATION_MESSAGE = (
    "The analytics engine isn't configured yet -- its service key is missing. "
    "Please ask an administrator to check the configuration, then try again."
)

# Returned when the failure looks like a network/transport problem (by
# exception class name only): a retry after the connection recovers can work.
_NETWORK_MESSAGE = (
    "I couldn't reach the analytics engine. Please check your internet "
    "connection and try again."
)

# Generic fallback for any failure not recognized above. Never empty, never
# technical; mirrors the graph's own generic-fallback discipline.
_GENERIC_FAILURE_MESSAGE = (
    "Something went wrong while processing that question. Please try again."
)

# Exception class *names* treated as network/transport failures. Matching by
# class name (never by message text, never interpolating it) keeps the check
# stable across the many client libraries the graph stack raises through
# (httpx, openai-compatible SDKs, ...) without importing any of them here.
_NETWORK_EXCEPTION_NAMES = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "ConnectError",
        "ConnectTimeout",
        "ConnectionError",
        "ReadTimeout",
        "RemoteProtocolError",
        "TimeoutError",
    }
)


@dataclass(frozen=True)
class QueryResult:
    """Outcome of one ``ask()`` call.

    Exactly one of the two fields is meaningful: ``state`` is set when the
    graph ran to completion (a state whose ``error`` field may still be set --
    the graph routes internal failures through ``handle_error`` and records a
    friendly ``final_answer``), and ``error`` is set only when the invocation
    itself failed before producing a state. ``state is None`` therefore means
    "the graph never returned"; ``error is not None`` means "show this
    non-technical message".
    """

    state: GraphState | None = None
    error: str | None = None


def ask(question: str) -> QueryResult:
    """Run ``question`` through ``sentrasql_graph`` and return the outcome.

    Args:
        question: The user's raw question. Whitespace is stripped; a blank
            question is rejected up front with a friendly message rather than
            sent to the graph.

    Returns:
        A ``QueryResult`` carrying the resulting ``GraphState`` on success, or
        a fixed non-technical message on ``error`` when the invocation raised
        (missing key, network failure, any other exception). Never raises.
    """
    cleaned = (question or "").strip()
    if not cleaned:
        return QueryResult(error=_EMPTY_QUESTION_MESSAGE)

    try:
        raw = sentrasql_graph.invoke(GraphState(raw_query=cleaned))
    except Exception as exc:  # noqa: BLE001 -- ask() is the total boundary.
        return QueryResult(error=f"{_friendly_message_for(exc)} [DEBUG: {type(exc).__name__}: {exc}]")

    # LangGraph returns the final state either as the typed GraphState or as a
    # plain dict of its fields depending on version/config; normalize both to
    # the typed model exactly like graph/build.py's ``__main__`` probe does, so
    # callers always read attributes (``state.final_answer``, ``state.error``).
    state = raw if isinstance(raw, GraphState) else GraphState(**raw)
    return QueryResult(state=state)


def _friendly_message_for(exc: Exception) -> str:
    """Map an exception to a fixed non-technical message (never its text)."""
    if isinstance(exc, KeyError):
        return _CONFIGURATION_MESSAGE
    if type(exc).__name__ in _NETWORK_EXCEPTION_NAMES:
        return _NETWORK_MESSAGE
    return _GENERIC_FAILURE_MESSAGE


if __name__ == "__main__":
    # Minimal wiring probe (real API call), mirroring graph/build.py's
    # ``__main__``: run one real question through the same code path the
    # dashboard uses and print the outcome.
    import sys

    question = " ".join(sys.argv[1:]).strip()
    if not question:
        question = "What was total revenue in the United Kingdom?"

    print(f"question: {question!r}")
    result = ask(question)
    if result.error is not None:
        print(f"ERROR: {result.error}")
    else:
        print(f"state.error:  {result.state.error!r}")
        print(f"final_answer: {result.state.final_answer!r}")
