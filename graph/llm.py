"""Configure and expose the DeepSeek chat model used for intent extraction.

This module is deliberately narrow: it owns the single responsibility of
producing the *bound, ready-to-invoke* model object for the
``extract_query_intent`` graph node. It does not contain the real system
prompt, retry logic, or the post-parse normalization step -- those are separate
tasks that will layer on top of what is proven here.

The entry point is ``get_intent_model()``, which returns a configured
``ChatDeepSeek`` instance bound to ``graph.state.QueryIntent`` via
``with_structured_output(..., strict=True)``. Invoking the returned runnable
yields a fully validated ``QueryIntent`` Pydantic instance -- never raw JSON
that would need manual parsing.

The ``__main__`` block is a minimal, throwaway wiring probe: it sends one
trivial hardcoded query with a bare-bones inline prompt and prints the raw
returned ``QueryIntent`` object and its field values, proving the wiring works
end-to-end against the live API.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from langchain_deepseek import ChatDeepSeek

from graph.state import QueryIntent

# The project keeps secrets in a gitignored root ``.env`` file (see
# ``.env.example`` for the canonical key list). Loading it at import time with
# the standard ``load_dotenv()`` makes ``DEEPSEEK_API_KEY`` available to
# ``ChatDeepSeek``, which reads that exact variable by default. Existing
# process-environment values are never overridden by the file.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

# DeepSeek's strict structured-output (JSON-schema) endpoint. This is passed to
# ``ChatDeepSeek`` explicitly instead of trusting ``langchain-deepseek``'s
# automatic strict-mode re-route -- see ``get_intent_model()`` for why that
# re-route is broken and must not be relied on.
_DEEPSEEK_BETA_API_BASE = "https://api.deepseek.com/beta"


def get_intent_model() -> Runnable[LanguageModelInput, QueryIntent]:
    """Return a configured ChatDeepSeek bound to QueryIntent in strict mode.

    Configuration replicates the verified-working live probe setup recorded in
    DESIGN_LOG.md section 16:

    * ``model="deepseek-v4-pro"``: thinking mode defaults ON for this model,
      and DeepSeek rejects a function-calling/tool-choice request while
      thinking is enabled with HTTP 400 ("Thinking mode does not support this
      tool_choice"). Thinking is therefore explicitly disabled on every request
      via ``extra_body={"thinking": {"type": "disabled"}}`` -- this is the
      DeepSeek-documented control knob, and ``extra_body`` is the field
      ``BaseChatOpenAI`` (which ``ChatDeepSeek`` extends) merges into the
      request body.
    * Endpoint: DeepSeek's beta API base (``https://api.deepseek.com/beta``),
      the endpoint implementing strict schema validation. It is passed
      explicitly as ``base_url`` rather than relying on ``langchain-deepseek``'s
      automatic strict-mode re-route, because that re-route is broken in
      ``langchain-deepseek==1.1.0``: it creates the beta model via
      ``model_copy(update={"api_base": .../beta})``, and pydantic's
      ``model_copy`` is a shallow copy that never re-runs the model validator.
      The already-built OpenAI client objects (``root_client``/``client``,
      constructed against the default ``/v1`` base at init time) are therefore
      copied onto the "beta" model as shared references, so it *reports*
      ``api_base=.../beta`` while every request still goes out over the
      ``/v1``-bound client to ``https://api.deepseek.com/v1/chat/completions``
      (confirmed by live wire capture). Building the model with the beta base
      URL up front makes the OpenAI root client target ``/beta`` from the
      start; ``with_structured_output``'s re-route branch only fires when
      ``api_base`` equals the default ``/v1``, so with this configuration it is
      skipped and strict calls genuinely hit
      ``https://api.deepseek.com/beta/chat/completions``.
    * API key: read from the ``DEEPSEEK_API_KEY`` environment variable
      (populated from the root ``.env`` by the ``load_dotenv()`` above), which
      is ``ChatDeepSeek``'s documented default source for its ``api_key``
      argument.
    * Binding: ``with_structured_output(QueryIntent, strict=True)`` returns a
      runnable whose invocations produce validated ``QueryIntent`` instances.

    Returns:
        A runnable chat model: same inputs as any LangChain chat model, outputs
        a validated ``QueryIntent`` instance.
    """
    model = ChatDeepSeek(
        model="deepseek-v4-pro",
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=_DEEPSEEK_BETA_API_BASE,
        temperature=0,
        extra_body={"thinking": {"type": "disabled"}},
    )
    return model.with_structured_output(QueryIntent, strict=True)


if __name__ == "__main__":
    # Throwaway wiring probe (real API call). Bare-bones inline prompt only --
    # the real system prompt is a separate task.
    structured_model = get_intent_model()

    query = "total revenue"
    messages = [
        SystemMessage(
            "Extract the user's analytics question into the requested structured "
            "output. When nothing was mentioned, use the documented defaults "
            "and empty values."
        ),
        HumanMessage(query),
    ]

    intent = structured_model.invoke(messages)

    if not isinstance(intent, QueryIntent):
        raise SystemExit(
            f"Expected a validated QueryIntent instance, got "
            f"{type(intent).__module__}.{type(intent).__name__}: {intent!r}"
        )

    print(f"query: {query!r}")
    print(f"returned type: {type(intent).__module__}.{type(intent).__name__}")
    print(f"isinstance(intent, QueryIntent): {isinstance(intent, QueryIntent)}")
    print(f"raw returned QueryIntent object:\n{intent!r}")
    print("field values:")
    print(f"  aggregation:   {intent.aggregation!r}")
    print(f"  metric:        {intent.metric!r}")
    print(f"  distinct:      {intent.distinct!r}")
    print(f"  group_by:      {intent.group_by!r}")
    print(f"  filters:       {intent.filters!r}")
    print(f"  net_gross:     {intent.net_gross!r}")
    print(f"  output_format: {intent.output_format!r}")
    print(f"  assumptions:   {intent.assumptions!r}")
