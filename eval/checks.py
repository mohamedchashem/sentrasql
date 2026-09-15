"""Shared pure helpers used by eval-case ``check`` callables.

Every check callable in ``eval/cases`` works against the same two inputs -- the
dumped graph-state dict and the live read-only database -- so the small,
repetitive plumbing is kept here instead of being copied into each case module:

* ``scalar_metric`` -- pull the single aggregate value out of a scalar
  (ungrouped) ``main_results`` list, the shape ``execute_queries`` stores for
  non-grouped queries (one column-name-keyed record).
* ``scalar_reference`` -- run a hand-written reference SELECT through
  ``eval.db_reference.run_reference_query`` and return its single scalar value,
  failing loudly when the query does not return exactly one row and one column.
  This is the independent-verification pipe: a fresh read-only connection per
  call, entirely separate from the graph's own compiled queries.
* ``format_currency`` -- the exact GBP rendering the graph's own answer layer
  uses for money metrics (``graph/reference_dict.py``): ``£`` prefix, thousands
  separators, two decimals. Checks that assert a formatted value appears in
  ``final_answer`` must reproduce this spelling or they can never pass.

Helpers raise (rather than return a failed ``CheckResult``) on structurally
impossible inputs -- a grouped wrapper where a scalar was expected, a
NULL-valued reference aggregate, a non-list check result -- so a case that
misreads the dumped state surfaces as a loud ERROR in the runner, never as a
silent ``False``. The runner catches those exceptions per case.
"""

from __future__ import annotations

from eval.db_reference import run_reference_query


def scalar_metric(dumped_state: dict, metric: str) -> int | float:
    """Return the single ``metric`` value from a scalar main_results list.

    Args:
        dumped_state: The dumped graph state (``model_dump(mode="json")``).
        metric: The metric column alias (e.g. ``"revenue"``, ``"unit_price"``).

    Returns:
        The numeric value of ``main_results[0][metric]``.

    Raises:
        ValueError: When ``main_results`` is not a one-record list (a grouped
            wrapper, the empty list, or ``None``) or the record has no
            ``metric`` column.
        TypeError: When the stored cell is not a real number.
    """
    main_results = dumped_state.get("main_results")
    if not isinstance(main_results, list) or len(main_results) != 1:
        raise ValueError(
            f"Expected scalar main_results as a one-record list for metric "
            f"{metric!r}, got {type(main_results).__name__}: {main_results!r}"
        )
    row = main_results[0]
    if metric not in row:
        raise ValueError(
            f"Scalar result record carries no {metric!r} column (keys: "
            f"{sorted(row)})."
        )
    value = row[metric]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"Scalar {metric!r} cell is not numeric: {value!r} "
            f"({type(value).__name__})."
        )
    return value


def scalar_reference(sql: str, db_path=None) -> int | float:
    """Run a reference SELECT and return its single scalar value.

    Args:
        sql: A hand-written SELECT that must return exactly one row and one
            column (e.g. ``SELECT SUM(...) ...`` or ``SELECT AVG(...) ...``).
        db_path: Optional explicit database path forwarded to
            ``run_reference_query``; defaults to the live project database.

    Returns:
        The single returned value.

    Raises:
        ValueError: When the query returns zero rows, more than one row, more
            than one column, or a NULL value in its single cell.
    """
    rows = run_reference_query(sql, db_path)
    if len(rows) != 1 or len(rows[0]) != 1:
        raise ValueError(
            "Reference SELECT must return exactly one row and one column, got "
            f"{len(rows)} row(s) x "
            f"{len(rows[0]) if rows else 0} column(s)."
        )
    value = rows[0][0]
    if value is None:
        raise ValueError(f"Reference SELECT returned NULL: {sql!r}")
    return value


def format_currency(value: int | float) -> str:
    """Render a money value exactly as the graph's answer layer does.

    Mirrors ``graph.reference_dict._format_currency`` (GBP is the dataset's
    single currency convention): ``£`` prefix, thousands separators, two
    decimals -- e.g. ``£7,511,063.74``.
    """
    return f"£{value:,.2f}"
