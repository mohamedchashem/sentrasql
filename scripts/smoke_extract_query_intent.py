"""Real-API smoke test for ``graph.node_extract_query_intent.extract_query_intent`` (Node 2).

This is the one clearly-separated live-API test for the fully assembled Node 2
pipeline -- real DeepSeek call, real system prompt (schema block introspected
from the live database), real strict structured output, real post-parse
normalization, real retry/error routing. It is intentionally NOT part of the
pytest suite (which must stay offline and free); run it by hand when
verifying the end-to-end wiring, keeping the query trivial to control cost::

    .venv\\Scripts\\python.exe scripts/smoke_extract_query_intent.py
    .venv\\Scripts\\python.exe scripts/smoke_extract_query_intent.py "revenue by country"

Exit code 0 = intent extracted successfully on (at most) the one retry;
1 = the node routed to the error path.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the project root importable so the ``graph``/``db`` packages resolve.
# Mirrors the sys.path handling used by the other run_*/verify_* scripts.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from graph.node_extract_query_intent import extract_query_intent  # noqa: E402
from graph.state import GraphState  # noqa: E402


def _print_intent(intent) -> None:
    """Print every QueryIntent field for a visual end-to-end confirmation."""
    print("  intent fields:")
    print(f"    aggregation:    {intent.aggregation!r}")
    print(f"    metric:         {intent.metric!r}")
    print(f"    distinct:       {intent.distinct!r}")
    print(f"    group_by:       {intent.group_by!r}")
    print(f"    filters:        {intent.filters!r}")
    print(f"    net_gross:      {intent.net_gross!r}")
    print(f"    output_format:  {intent.output_format!r}")
    print(f"    assumptions:    {intent.assumptions!r}")


def main() -> int:
    query = sys.argv[1] if len(sys.argv) > 1 else "total revenue"
    print("=" * 78)
    print("REAL-API SMOKE TEST: extract_query_intent (Node 2)")
    print("=" * 78)
    print(f"  query:           {query!r}")
    print(f"  prompt source:   build_system_prompt (stable blocks + query)")
    print(f"  model:           get_intent_model() -> DeepSeek strict structured output")
    print(f"  normalization:   normalize_and_validate_intent (present-flag + group_by)")
    print(f"  retry policy:    exactly one retry on failure, recorded in state\n")

    state = GraphState(raw_query=query, normalized_query="")
    result = extract_query_intent(state)

    print(f"  intent_extraction_retried: {result.intent_extraction_retried}")
    print(f"  state.error:               {result.error!r}")

    if result.error is not None:
        print(f"\nFAILED: node routed to the error path ({result.error}).")
        return 1
    if result.query_intent is None:
        print("\nFAILED: no error set but query_intent is None.")
        return 1

    _print_intent(result.query_intent)
    print("\nPASSED: real-API end-to-end intent extraction succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
