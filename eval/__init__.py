"""Live eval suite for the SentraSQL analytics agent.

This package is the permanent, re-runnable live-eval harness: it runs real
natural-language queries through the compiled LangGraph
(``graph.build.sentrasql_graph``) against the real DeepSeek API and the real
SQLite database at ``data/processed/sentrasql.db``, then checks each result's
structured state against a stated expectation.

Module map:

* ``eval.case`` -- the data structures describing one eval case and its
  per-check results (``EvalCase`` / ``CheckResult``).
* ``eval.cases`` -- the case registry (``CASES``). Individual case definitions
  live in submodules of this package and register themselves into ``CASES``.
* ``eval.db_reference`` -- read-only reference-query helpers eval cases use to
  verify results independently against the real database (a fresh connection
  per call, separate from the graph's own compiled queries).
* ``eval.runner`` -- the orchestrator. ``python -m eval.runner`` runs the
  preflight checks and then every registered case.

The package deliberately imports nothing heavyweight at ``__init__`` time so
that importing ``eval`` (or starting ``python -m eval.runner``) never
accidentally triggers a graph or LLM import before the preflight has run.
"""
