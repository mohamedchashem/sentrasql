"""Data structures describing one live eval case and its per-check results.

A single eval case couples a natural-language query with a *check*: a plain
callable that is handed the fully dumped graph state produced by running that
query through ``graph.build.sentrasql_graph`` and returns the verdict as a
list of ``CheckResult`` items. Defining the shapes here (rather than ad hoc
dicts in the runner) keeps one source of truth for what a case is and what a
case's verdicts look like, ready for the case modules in ``eval.cases`` to
start using in the next step.

Contracts:

* ``EvalCase.id`` is a unique machine-readable string identifying the case
  (used in reports and failures). No two registered cases may share an ``id``.
* ``EvalCase.category`` is a free-form grouping string (e.g. ``"basic"``,
  ``"grouped"``, ``"guardrails"``) used to organize results.
* ``EvalCase.query`` is the natural-language query text, verbatim.
* ``EvalCase.check`` is a callable taking the *full dumped graph state* -- the
  JSON-serializable dict produced by ``GraphState.model_dump(mode="json")``,
  exactly as ``graph.build``'s ``__main__`` block dumps it -- and returning a
  list of ``CheckResult`` verdicts. The callable must be pure w.r.t. the state
  it receives: all expectations are closed over at case-definition time and no
  real LLM/API calls happen inside a check (a check only inspects the dumped
  state and, where needed, queries the database read-only via
  ``eval.db_reference``). A returned ``CheckResult.passed`` of ``False`` marks
  that one named check as failed; ``detail`` carries the human-readable
  evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class CheckResult:
    """The verdict for one named assertion within a case's ``check`` callable.

    Attributes:
        name: Short machine-readable assertion name (e.g. ``"error_none"``).
        passed: ``True`` when the assertion held, ``False`` when it failed.
        detail: Human-readable evidence for the verdict (the expected vs.
            actual values, or the reason the assertion was skipped/NA).
    """

    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class EvalCase:
    """One live eval case: a natural-language query and how to check its result.

    Attributes:
        id: Unique machine-readable case identifier.
        category: Grouping string used to organize results.
        query: The natural-language query text, verbatim.
        check: Callable receiving the full dumped graph state (a
            JSON-serializable dict as produced by
            ``GraphState.model_dump(mode="json")``) and returning the list of
            ``CheckResult`` verdicts for that state.
    """

    id: str
    category: str
    query: str
    check: Callable[[dict], list[CheckResult]]
