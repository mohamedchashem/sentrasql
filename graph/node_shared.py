"""Module-level helpers genuinely shared by more than one graph node.

Per the one-responsibility-per-module pattern, a node module keeps only
the helpers/constants its single node function depends on; anything used
by two or more node modules lives here instead of being duplicated.

Currently shared: ``_MAIN_DB_PATH`` -- the read-only live database path
used by ``node_compile_sql`` (runtime introspection gates),
``node_validate_guardrails`` (live schema fetch), and
``node_execute_queries`` (execution connection)."""

from __future__ import annotations

from pathlib import Path


# Path to the read-only live database shared by the graph node modules:
# node_compile_sql's runtime introspection gates (group-by columns must exist in
# the live schema, and a scalar filter value must match real rows),
# node_validate_guardrails' live schema fetch, and node_execute_queries'
# execution connection. Derivation mirrors the rest of the repository (e.g.
# scripts/run_load.py): project root / data / processed / sentrasql.db.
_MAIN_DB_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "processed"
    / "sentrasql.db"
)
