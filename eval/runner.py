"""Live eval suite orchestrator: preflight checks, then the case registry.

This is the entry point for the live eval harness -- it decides whether the
environment is ready to run real cases and, once it is, dispatches the
registered cases. This module contains only orchestration: no case definitions,
no check logic, and no reference queries of its own. Run it from the project
root with the project interpreter::

    .venv\\Scripts\\python.exe -m eval.runner

Behaviour today (four registered live cases in ``eval/cases``):

1. **Preflight.** Three checks must all pass before any case can run:

   * ``DEEPSEEK_API_KEY`` is set in the environment. The runner first loads the
     project root ``.env`` (the same file ``graph/llm.py`` loads at import
     time), so this reports the exact environment the real API calls will run
     with.
   * The project's SQLite database (``data/processed/sentrasql.db``) exists and
     can be opened read-only. The check runs ``SELECT 1`` through
     ``eval.db_reference.run_reference_query`` -- a fresh
     ``db.connect.connect_readonly`` connection, i.e. the exact read-only
     plumbing real cases will use -- proving existence, openability, and that
     reads work.
   * ``sentrasql_graph`` imports successfully from ``graph.build``. The import
     is performed lazily inside the check (not at module import time) so a
     broken graph is reported as a clean preflight FAILURE with the underlying
     exception, never as a crash of the runner itself.

   Each check's PASS/FAIL status and detail line are printed. If any check
   fails, the runner prints a summary and exits with code 1.

2. **Cases.** With preflight green, the runner reads the case registry from
   ``eval.cases``. With no cases registered it prints "No cases registered yet"
   and exits cleanly with code 0. Otherwise it runs every registered case: the
   case's query is invoked through ``graph.build.sentrasql_graph`` on a fresh
   ``GraphState`` (exactly like ``graph.build``'s own ``__main__`` block), the
   resulting state is dumped via ``model_dump(mode="json")``, and the dumped
   dict is handed to the case's ``check`` callable. Each returned
   ``CheckResult`` is reported (name / PASS / FAIL / detail) beneath the
   case's header. A case whose graph invocation or ``check`` callable raises is
   reported as an ERROR and the runner moves on to the next case -- one case
   never aborts the suite.

3. **Summary.** After every case has run (or been errored), the runner prints
   total / passed / failed / errored counts. The process exit code is 1 when
   any case failed or errored (or the preflight failed) and 0 only when
   everything passed.

Exit codes: ``0`` = preflight passed and every registered case passed (or no
cases were registered); ``1`` = preflight failed or at least one case
failed/errored.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

# Make the project root importable so the ``db``/``graph``/``eval`` packages
# resolve. ``python -m eval.runner`` from the project root already puts the
# root on ``sys.path``; the explicit insert mirrors the sys.path handling used
# by the ``scripts/`` modules and keeps the same invocation robust from any
# working directory.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

# The project keeps its secret in a gitignored root ``.env`` file (see
# ``.env.example``). ``graph/llm.py`` loads that file at import time; the
# runner loads it here, up front and independently, so the API-key preflight
# reports the same environment the graph's real calls will use. As with
# ``graph/llm.py``, existing process-environment values are never overridden
# by the file.
load_dotenv(_PROJECT_ROOT / ".env")

from eval.db_reference import _MAIN_DB_PATH, run_reference_query  # noqa: E402

# Environment variable the DeepSeek API key is read from (``graph.llm`` reads
# this exact variable when it builds a model).
_API_KEY_ENV = "DEEPSEEK_API_KEY"


@dataclass(frozen=True)
class PreflightResult:
    """The verdict of one preflight check, ready for reporting."""

    name: str
    passed: bool
    detail: str


def _check_api_key() -> PreflightResult:
    """Confirm ``DEEPSEEK_API_KEY`` is present (non-empty) in the environment."""
    name = "DEEPSEEK_API_KEY is set in the environment"
    if os.environ.get(_API_KEY_ENV):
        return PreflightResult(
            name=name,
            passed=True,
            detail="a non-empty key is available (from the process environment "
            "or the project root .env file).",
        )
    return PreflightResult(
        name=name,
        passed=False,
        detail="not set: add DEEPSEEK_API_KEY to the process environment or to "
        "the project root .env file.",
    )


def _check_database() -> PreflightResult:
    """Confirm the live database exists and serves reads on a read-only conn."""
    name = "SQLite database exists and can be opened read-only"
    try:
        if not _MAIN_DB_PATH.exists():
            return PreflightResult(
                name=name,
                passed=False,
                detail=f"database file not found at {_MAIN_DB_PATH}.",
            )
        size = _MAIN_DB_PATH.stat().st_size
        rows = run_reference_query("SELECT 1")
        if rows != [(1,)]:
            return PreflightResult(
                name=name,
                passed=False,
                detail=f"SELECT 1 on a fresh read-only connection returned "
                f"unexpected rows: {rows!r}.",
            )
        return PreflightResult(
            name=name,
            passed=True,
            detail=f"found at {_MAIN_DB_PATH} ({size:,} bytes) and served "
            "SELECT 1 on a fresh read-only connection.",
        )
    except Exception as exc:  # noqa: BLE001 - a preflight must never crash
        return PreflightResult(
            name=name,
            passed=False,
            detail=f"{type(exc).__name__}: {exc}.",
        )


def _check_graph_import() -> PreflightResult:
    """Confirm ``sentrasql_graph`` imports from ``graph.build``.

    The import is intentionally lazy (performed here, not at module import
    time) so a broken graph surfaces as a reportable preflight FAILURE rather
    than an import-time crash of ``eval.runner`` itself.
    """
    name = "sentrasql_graph imports successfully from graph.build"
    try:
        from graph.build import sentrasql_graph
    except Exception as exc:  # noqa: BLE001 - report the underlying failure
        return PreflightResult(
            name=name,
            passed=False,
            detail=f"import raised {type(exc).__name__}: {exc}.",
        )
    if sentrasql_graph is None:
        return PreflightResult(
            name=name,
            passed=False,
            detail="graph.build.sentrasql_graph resolved to None.",
        )
    return PreflightResult(
        name=name,
        passed=True,
        detail=f"graph.build.sentrasql_graph imported (a compiled "
        f"{type(sentrasql_graph).__name__}).",
    )


def run_preflight() -> list[PreflightResult]:
    """Run every preflight check in order and return its verdicts."""
    return [_check_api_key(), _check_database(), _check_graph_import()]


def _report_preflight(results: list[PreflightResult]) -> None:
    """Print each check's PASS/FAIL status and its detail line."""
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"  [{status}] {result.name}")
        print(f"         {result.detail}")


def _invoke_case(case, graph, state_cls) -> tuple[str | None, dict | None]:
    """Run one case's query through ``graph`` and return ``(dumped_state, err)``.

    Mirrors ``graph.build``'s ``__main__`` block: a fresh ``GraphState`` is
    built from the case's raw query, invoked through the compiled graph, and
    the resulting state is normalized back into the typed ``GraphState`` (if
    LangGraph returned a plain dict) and dumped with
    ``model_dump(mode="json")`` -- the exact JSON-serializable dict shape the
    ``EvalCase.check`` contract documents. Any exception raised by the graph
    invocation itself is caught here and returned as the error string, never
    propagated: the runner must keep going with the remaining cases.

    Returns:
        ``(dumped_state, None)`` on success or ``(None, error_detail)`` when
        the invocation raised.
    """
    try:
        result = graph.invoke(state_cls(raw_query=case.query))
        state = (
            result if isinstance(result, state_cls) else state_cls(**result)
        )
        return state.model_dump(mode="json"), None
    except Exception as exc:  # noqa: BLE001 - one bad case must not stop the suite
        return None, f"{type(exc).__name__}: {exc}"


def _run_case(case, graph, state_cls) -> str:
    """Execute one eval case, print its verdicts, and return the outcome.

    Prints the case header, the individual check verdicts from
    ``case.check(dumped_state)``, and a one-line CASE RESULT. Returns one of
    "passed", "failed", or "errored".
    """
    print("-" * 78)
    print(f"CASE: {case.id}  (category: {case.category})")
    print(f"QUERY: {case.query}")
    print("-" * 78)

    dumped, error_detail = _invoke_case(case, graph, state_cls)
    if error_detail is not None:
        print(f"  [ERROR] graph invocation raised: {error_detail}")
        return "errored"
    if dumped.get("error") is not None:
        print(f"  note: graph state.error = {dumped['error']!r}")

    try:
        check_results = case.check(dumped)
    except Exception as exc:  # noqa: BLE001 - report, do not crash the suite
        print(f"  [ERROR] case.check raised: {type(exc).__name__}: {exc}")
        return "errored"

    if not isinstance(check_results, list):
        print(
            "  [ERROR] case.check did not return a list of CheckResult items "
            f"(got {type(check_results).__name__})."
        )
        return "errored"

    passed_count = 0
    for result in check_results:
        name = getattr(result, "name", "?")
        passed = bool(getattr(result, "passed", False))
        detail = getattr(result, "detail", "")
        status = "PASS" if passed else "FAIL"
        if passed:
            passed_count += 1
        print(f"  [{status}] {name}")
        print(f"         {detail}")

    total = len(check_results)
    if total and passed_count == total:
        print(f"CASE RESULT: PASS ({passed_count}/{total} checks passed)")
        return "passed"
    print(f"CASE RESULT: FAIL ({passed_count}/{total} checks passed)")
    return "failed"


def _print_summary(totals: dict[str, int]) -> int:
    """Print the end-of-run totals and return the process exit code."""
    total = totals["passed"] + totals["failed"] + totals["errored"]
    print("=" * 78)
    print("SENTRASQL LIVE EVAL RUNNER - summary")
    print("=" * 78)
    print(f"  total cases: {total}")
    print(f"  passed:      {totals['passed']}")
    print(f"  failed:      {totals['failed']}")
    print(f"  errored:     {totals['errored']}")
    if totals["failed"] or totals["errored"]:
        print("RESULT: FAILURES PRESENT - exit code 1")
        return 1
    print("RESULT: ALL CASES PASSED - exit code 0")
    return 0


def main() -> int:
    """Run the preflight and then every registered live eval case."""
    print("=" * 78)
    print("SENTRASQL LIVE EVAL RUNNER - preflight")
    print("=" * 78)

    results = run_preflight()
    _report_preflight(results)

    all_passed = all(result.passed for result in results)
    print()
    if not all_passed:
        print(
            "PREFLIGHT FAILED: at least one check did not pass, so live eval "
            "cases cannot run reliably."
        )
        return 1

    print("PREFLIGHT PASSED: the environment is ready to run live eval cases.")
    print()

    print("=" * 78)
    print("SENTRASQL LIVE EVAL RUNNER - cases")
    print("=" * 78)
    print()

    # The case registry lives in eval.cases; importing it lazily keeps this
    # module's import light until the preflight has already passed. The graph
    # and state models are imported only when there is something to run.
    from eval.cases import CASES

    if not CASES:
        print("No cases registered yet")
        return 0

    from graph.build import sentrasql_graph
    from graph.state import GraphState

    totals = {"passed": 0, "failed": 0, "errored": 0}
    for case in CASES:
        outcome = _run_case(case, sentrasql_graph, GraphState)
        totals[outcome] += 1
        print()

    return _print_summary(totals)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())

