"""Guardrail: only a single, SELECT-only SQL statement may pass.

This module backs the ``validate_guardrails`` graph node. It is deliberately
narrow -- it checks only that input parses as exactly one SQL statement and
that the statement is a SELECT. Table/column/function whitelisting, JOIN
validation, ``SELECT *`` rejection, and row limits are separate, later tasks.

SQLite is the dialect throughout (``read="sqlite"``), matching the project's
database (``db/schema.sql``).

Statement counting uses ``sqlglot.parse``, which returns one entry per parsed
statement. Two non-obvious library behaviors are accounted for here:

* Input with no real SQL (empty, whitespace-only, comment-only, or a stray
  ``;``) parses *successfully* to ``[None]`` instead of raising.
* A ``;`` that carries only a trailing comment parses as an ``exp.Semicolon``
  node -- sqlglot's marker for an empty statement, not an executable one.

Both are discarded before counting, so ``"SELECT 1; -- done"`` is treated as
the one real statement it is, while stacked statements still parse to multiple
real expressions and are rejected.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


def validate_sql(sql: str) -> tuple[bool, str | None]:
    """Return ``(True, None)`` when ``sql`` is exactly one SQL SELECT statement.

    Otherwise returns ``(False, reason)``, where ``reason`` is one of:

    * ``"unparseable_sql"`` -- sqlglot could not parse ``sql`` at all.
    * ``"no_statement"`` -- ``sql`` contains no real SQL statement (empty or
      whitespace-only input, comment-only input, stray ``;`` separators).
    * ``"multiple_statements"`` -- ``sql`` contains more than one real
      statement (stacked queries).
    * ``"not_a_select"`` -- ``sql`` is exactly one statement, but not a SELECT.
    """
    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except ParseError:
        return False, "unparseable_sql"

    # Drop sqlglot's empty-statement markers: ``None`` for inputs with no real
    # SQL, and ``exp.Semicolon`` for a ``;`` that only carries a comment. Only
    # real, executable statements count toward "exactly one".
    statements = [
        statement
        for statement in statements
        if statement is not None and not isinstance(statement, exp.Semicolon)
    ]

    if not statements:
        return False, "no_statement"

    if len(statements) > 1:
        return False, "multiple_statements"

    # Deliberate current scope: SELECT-only, not yet extended to compound set operations, so UNION/INTERSECT/EXCEPT (which parse to exp.Union) currently fall under "not_a_select".
    if not isinstance(statements[0], exp.Select):
        return False, "not_a_select"

    return True, None


if __name__ == "__main__":
    cases = [
        "SELECT * FROM transactions",          # valid: single SELECT
        "DROP TABLE transactions",              # single statement, not a SELECT
        "SELECT 1; DROP TABLE x",               # stacked statements
        "",                                     # no statement at all
        "this is not sql at all",               # unparseable garbage
        # Extra edge case: sqlglot emits an extra exp.Semicolon (an empty
        # statement) for a ";" that carries only a trailing comment, so a naive
        # count would reject this benign single SELECT as "multiple". It must
        # pass, which verifies the counting ignores empty-statement markers.
        "SELECT 1; -- trailing comment",
        # Compound set operation: currently rejected as "not_a_select" --
        # deliberate SELECT-only scope (see comment at the statement-type check).
        "SELECT 1 UNION SELECT 2",
    ]

    for case in cases:
        print(f"{case!r} -> {validate_sql(case)}")
