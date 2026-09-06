"""Guardrail: only a single, SELECT-only SQL statement may pass.

This module backs the ``validate_guardrails`` graph node. It checks that input
parses as exactly one SQL statement, that the statement is a SELECT, that every
table the query references is listed in the caller-supplied schema, that every
column reference resolves to a valid column of one of those tables, that every
function call in the query is on the permitted whitelist, that no SELECT scope
projects a bare wildcard, and that no JOIN clause is unconditioned. It then
enforces the row-limit ceiling for the active tier (DESIGN_LOG.md section 12):
the SQL returned for execution is guaranteed to carry a LIMIT at or below the
ceiling -- one is injected when the query has none, and an over-ceiling or
unverifiable LIMIT is clamped down. Because this step modifies the query rather
than merely passing or rejecting it, ``validate_sql`` returns the SQL to execute
(plus a truncation flag), not just a verdict; see its docstring for the exact
shape.

Wildcard rejection (reason ``"wildcard_select"``) is its own gate. sqlglot
models ``SELECT *`` as a bare ``exp.Star`` node directly in a Select's
``expressions`` list, and ``SELECT table.*`` as an ``exp.Column`` whose
``this`` is an ``exp.Star``. Both projection shapes bypass the column
whitelist, so they are rejected in every SELECT scope -- the outermost query,
nested subqueries, and CTE bodies alike. A ``*`` inside an aggregate like
``COUNT(*)`` is a function *argument*, never a projection entry, so those
queries still pass.

JOIN conditioning (reason ``"unconditioned_join"``) is another structural gate.
sqlglot maps the three SQL spellings of an unconditioned join onto only *two*
AST shapes, so they cannot always be told apart structurally. An explicit
``CROSS JOIN`` and an implicit comma join (``FROM a, b``) both parse to the
same node -- ``exp.Join`` with ``kind="CROSS"`` and no ``on``/``using``
argument -- one check catches both. A ``JOIN``/``INNER JOIN``/``LEFT JOIN``/etc.
written with *no* condition parses anyway because the parser patches a synthetic
``on=exp.Boolean(True)`` (``ON TRUE``) onto the node; no flag records that the
``TRUE`` was synthesized, so an author who literally writes ``ON TRUE`` gets the
identical AST. Both describe the same unconditional (cartesian) join, so both
are rejected. A genuine ON predicate is any other expression (``EQ``, ``AND``,
``LIKE``, ...) in ``on``, and a USING clause is a column list in ``using``;
either makes the join pass. ``NATURAL JOIN`` is the residual shape: it carries
only ``method="NATURAL"`` with neither ``on`` nor ``using``, so under the
no-ON/USING rule it is rejected too. Like the wildcard gate, the walk covers
nested subqueries and CTE bodies via ``find_all(exp.Join)``.

SQLite is the dialect throughout (``read="sqlite"``), matching the project's
database (``db/schema.sql``).

Function whitelisting walks ``exp.Func`` nodes: sqlglot models every call --
aggregates (COUNT/SUM/AVG/MIN/MAX), scalar builtins (ROUND/COALESCE/...),
unknown calls (``exp.Anonymous``), and even CAST's type-casting syntax -- as an
``exp.Func`` subclass, so a single ``find_all(exp.Func)`` traversal finds them
all. The per-node AST shapes and the pitfalls (``exp.Cast.name`` returning the
inner column, ``STRFTIME`` parsing to ``exp.TimeToStr``, ``DATETIME`` staying
an ``exp.Anonymous``) are documented at ``_function_call_name``.

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

from dataclasses import dataclass
from pathlib import Path
import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


# SQL functions a validated query is permitted to call. SQLite matches function
# names case-insensitively, so every comparison is folded. The whitelist is
# deliberately small: aggregates, ROUND, the date/time helpers the schema's
# TEXT timestamps need, CAST type conversion, and COALESCE.
ALLOWED_FUNCTIONS = frozenset(
    {
        "COUNT",
        "SUM",
        "AVG",
        "MIN",
        "MAX",
        "ROUND",
        "STRFTIME",
        "DATE",
        "DATETIME",
        "CAST",
        "COALESCE",
    }
)
_ALLOWED_FUNCTION_NAMES = {name.casefold() for name in ALLOWED_FUNCTIONS}

# Row-limit ceilings -- PROVISIONAL, NOT FINAL. See DESIGN_LOG.md section 12:
# neither number is derived from real requirements yet, and both must be
# revisited once real query patterns are observed. Do not treat these values as
# settled limits.
#
# The two named tiers:
#   * LIMIT_TIER_LLM_CONTEXT_ROWS is the low ceiling for any query result feeding
#     into Node 7 (LLM phrasing). It is a pathological-case safety guard against
#     an unanticipated high-cardinality group-by, not a number any expected query
#     type should routinely approach.
#   * LIMIT_TIER_BULK_EXPORT_ROWS is a higher ceiling reserved for a future
#     bulk-data consumer (PDF/report export, DESIGN_LOG.md section 5.3) that is
#     explicitly deferred and not yet scoped. It is currently UNUSED -- no caller
#     needs it yet -- and is defined here only so the tier exists as a selectable
#     option once that consumer is actually built. Its value is a placeholder:
#     the correct ceiling depends on what that node ends up needing.
#
# Enforcement picks a tier by ceiling value; _LIMIT_TIER_ROWS is the set of
# sanctioned selectors so a caller cannot accidentally enforce an arbitrary,
# unsanctioned ceiling (a stricter ad-hoc number would silently swallow rows the
# policy intended to allow, and a looser one would silently defeat the guard).
LIMIT_TIER_LLM_CONTEXT_ROWS = 500
LIMIT_TIER_BULK_EXPORT_ROWS = 50_000
_LIMIT_TIER_ROWS = frozenset(
    {LIMIT_TIER_LLM_CONTEXT_ROWS, LIMIT_TIER_BULK_EXPORT_ROWS}
)

# Matches the leading ``NAME(`` of a rendered function call. Used to recover a
# call's SQLite-visible name from its rendered SQL.
_FUNCTION_CALL_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def _function_call_name(call: exp.Expression) -> str | None:
    """Return the SQLite-visible name of an ``exp.Func`` node, or ``None``.

    Every genuine function call is an ``exp.Func`` subclass, including the
    aggregates (``exp.Count``, ``exp.Sum``, ...), scalar builtins (``exp.Round``,
    ``exp.Coalesce``), unknown functions (``exp.Anonymous``), and CAST type
    conversions (``exp.Cast``). The node's ``name`` property is *not* reliable
    for identifying the function -- ``exp.Cast.name`` is the inner expression's
    name (``CAST(quantity AS INTEGER).name == "quantity"``), and aggregate
    ``name`` properties are their argument's. So the function name is recovered
    by re-rendering the node in the sqlite dialect and reading the leading
    identifier before the parenthesis.

    Shapes that are *not* genuine calls return ``None``:

    * sqlglot inserts internal conversion helpers while parsing that subclass
      ``exp.Func`` but render with no ``NAME(...)`` syntax -- e.g.
      ``exp.TsOrDsToTimestamp`` wraps ``STRFTIME``'s argument, and the
      ``CURRENT_TIMESTAMP`` keyword parses to ``exp.CurrentTimestamp``. Both
      render as a bare expression/keyword and are skipped by the call-syntax
      check.
    * A typed node whose re-rendered SQL re-parses to a *different* node type is
      a cross-function rewrite rather than a plain call -- sqlglot turns an
      unsupported ``exp.DateDiff`` into ``CAST(JULIANDAY(...) - JULIANDAY(...)
      AS INTEGER)`` under sqlite, so its leading ``CAST`` token would be
      misleading. Such nodes use the class SQL name (``DATEDIFF``) instead.
      This is also why the name is compared case-insensitively against
      ``ALLOWED_FUNCTIONS`` rather than trusting rendered token text blindly.
    """
    if isinstance(call, exp.Anonymous):
        # Anonymous keeps the written name in its ``this`` arg (sqlglot's sqlite
        # parser normalizes it to uppercase). DATETIME and multi-argument
        # STRFTIME take this path in the sqlite dialect.
        return call.name
    rendered = call.sql(dialect="sqlite")
    match = _FUNCTION_CALL_RE.match(rendered)
    if match is None:
        return None  # internal helper/keyword, not a call with ``NAME(...)`` syntax
    try:
        reparsed = sqlglot.parse_one(f"SELECT {rendered}", read="sqlite")
    except ParseError:
        reparsed = None
    top = getattr(reparsed, "expressions", None)
    top = top[0] if top else None
    if top is not None and type(top) is not type(call):
        # sqlglot rewrote a different function into this node's SQL; the leading
        # token is not the function's own name, so fall back to the class name.
        return type(call).sql_name()
    return match.group(1)


def _first_disallowed_function(statement: exp.Expression) -> str | None:
    """Return the first non-whitelisted function called in ``statement``.

    Walks the entire statement AST (nested subqueries and CTE bodies included)
    and returns the name of the first genuine function call whose SQLite-visible
    name is not in ``ALLOWED_FUNCTIONS``, or ``None`` when every call passes.
    """
    for call in statement.find_all(exp.Func):
        name = _function_call_name(call)
        if name is None:
            continue  # not a real function call (internal helper / keyword)
        if name.casefold() not in _ALLOWED_FUNCTION_NAMES:
            return name.upper()
    return None


def _has_wildcard_select(statement: exp.Expression) -> bool:
    """Return True when any SELECT scope in ``statement`` projects a bare wildcard.

    sqlglot represents ``SELECT *`` as a bare ``exp.Star`` node directly in the
    Select's ``expressions`` list, and ``SELECT table.*`` as an ``exp.Column``
    whose ``this`` is an ``exp.Star`` (the qualifier sits in the column's
    ``table``). Either projection bypasses the column whitelist, so both are
    violations wherever they appear -- the outermost query, a nested subquery,
    or a CTE body, each found via ``find_all(exp.Select)``.

    A ``*`` nested inside an aggregate like ``COUNT(*)`` is *not* a projection:
    it is the ``this`` argument of an ``exp.Count`` (an ``exp.Func``) node, so
    checking only each Select's direct projection entries leaves those valid
    row-count patterns untouched.
    """
    for select_node in statement.find_all(exp.Select):
        for projection in select_node.args.get("expressions") or ():
            if isinstance(projection, exp.Star):
                return True
            if (
                isinstance(projection, exp.Column)
                and isinstance(projection.this, exp.Star)
            ):
                return True
    return False


def _has_unconditioned_join(statement: exp.Expression) -> bool:
    """Return True when any JOIN in ``statement`` lacks a real ON or USING clause.

    The three SQL spellings of an unconditioned join map onto two AST shapes,
    which is why the detection cannot distinguish all of them structurally:

    * ``FROM a CROSS JOIN b`` and the implicit comma join ``FROM a, b`` both
      parse to ``exp.Join`` with ``kind="CROSS"`` and no ``on``/``using``
      argument -- a comma join is indistinguishable from an explicit CROSS JOIN.
    * ``FROM a JOIN b`` (also INNER/LEFT/RIGHT/FULL... with no constraint) would
      have nothing to render, so sqlglot's parser fills in a synthetic
      ``on`` = ``TRUE`` (an ``exp.Boolean`` whose value is literally True).
      Because nothing records that the ``TRUE`` was synthesized, a written
      ``ON TRUE`` is byte-for-byte the same node -- and the same unconditional
      join -- so it is rejected too.
    * ``NATURAL JOIN`` is parsed with ``method="NATURAL"`` and neither ``on``
      nor ``using``; it has no written ON/USING condition and is rejected.

    A join passes only when it carries a genuine condition: a ``using`` column
    list, or an ``on`` predicate that is not the synthetic bare ``TRUE``.

    The walk uses ``find_all(exp.Join)``, so joins hidden in nested subqueries
    or CTE bodies are caught just as the wildcard gate catches nested ``SELECT
    *`` projections.
    """
    for join in statement.find_all(exp.Join):
        if join.args.get("kind") == "CROSS":
            # Explicit CROSS JOIN or implicit comma join -- sqlglot parses both
            # to kind="CROSS", and neither is a conditioned join.
            return True
        on = join.args.get("on")
        using = join.args.get("using")
        if using:
            continue  # USING (col, ...) is a genuine condition
        if on is None:
            # A join with neither an ON predicate nor a USING list (NATURAL
            # JOIN, or any shape sqlglot leaves without a condition).
            return True
        if isinstance(on, exp.Boolean) and on.this is True:
            # The parser synthesized `ON TRUE` for a JOIN written without any
            # condition; explicit `ON TRUE` is the same node. Both are
            # unconditional cartesian joins.
            return True
    return False


def _limit_count(limit: exp.Limit) -> int | None:
    """Return the row count of a LIMIT clause as a non-negative int, or None.

    Only a plain integer literal has a bound verifiable from the AST. Every
    other shape returns ``None``: a parameter placeholder (``LIMIT ?``),
    ``LIMIT ALL``, an arbitrary expression (``LIMIT (SELECT ...)``), or a
    negative count. A negative count specifically must NOT be read as zero --
    SQLite interprets a negative LIMIT as *no limit*, so such a query is
    unbounded in practice and must be treated as non-compliant.
    """
    expression = limit.args.get("expression")
    if not isinstance(expression, exp.Literal) or expression.is_string:
        return None
    try:
        count = int(expression.this)
    except (TypeError, ValueError):
        return None
    if count < 0:
        return None
    return count


def _enforce_row_limit(
    statement: exp.Select,
    sql: str,
    limit_tier: int,
) -> tuple[str, bool]:
    """Cap ``statement``'s result rows at ``limit_tier``; return the SQL to run.

    Returns ``(sql_to_execute, truncated)``. ``truncated`` is True exactly when
    the guardrail changed the row bound the query originally expressed: a LIMIT
    was injected into a query that had none (an unbounded request), or an
    existing LIMIT was clamped down -- including one whose value cannot be
    verified as at-or-below the tier (``LIMIT ALL``, a placeholder, an
    expression, or a negative count), all of which are unbounded or unprovable
    and therefore clamped to the ceiling too. A query that already carries a
    verifiable LIMIT at or below the tier is returned unchanged with
    ``truncated=False`` -- the guardrail did not alter what the caller asked
    for.

    Enforcement targets the outermost SELECT scope only: that is the scope whose
    result rows reach the caller, so a LIMIT hidden in a nested subquery does
    not satisfy the ceiling. When a limit must be injected or clamped, the whole
    statement is re-rendered canonically in the sqlite dialect (the parsed AST
    is the source of truth); a clamping edit mutates the existing LIMIT node in
    place so any OFFSET clause is preserved.
    """
    limit = statement.args.get("limit")
    if limit is None:
        statement.set("limit", exp.Limit(expression=exp.Literal.number(limit_tier)))
        return statement.sql(dialect="sqlite"), True

    count = _limit_count(limit)
    if count is not None and count <= limit_tier:
        return sql, False

    # Clamp an over-ceiling or unverifiable LIMIT in place (preserving any
    # OFFSET), then re-render the statement canonically.
    limit.set("expression", exp.Literal.number(limit_tier))
    return statement.sql(dialect="sqlite"), True


def validate_sql(
    sql: str,
    schema: dict[str, set[str]],
    limit_tier: int = LIMIT_TIER_LLM_CONTEXT_ROWS,
) -> tuple[bool, str | None, str | None, bool]:
    """Validate ``sql`` against the schema and return the SQL that should run.

    On success the return value is ``(True, None, enforced_sql, truncated)``:
    ``sql`` has passed every gate below and ``enforced_sql`` is the statement to
    execute, carrying a LIMIT guaranteed to be at or below ``limit_tier``. On
    rejection it is ``(False, reason, None, False)``: ``reason`` names the gate
    that failed and the SQL slot is ``None`` because a rejected query must never
    reach execution at all.

    ``schema`` maps table names to the set of their valid column names. This
    function performs no database I/O of its own -- the caller is responsible
    for supplying an accurate, current schema.

    ``limit_tier`` selects which provisional row-limit ceiling to enforce (see
    DESIGN_LOG.md section 12) and must be one of the sanctioned module constants
    ``LIMIT_TIER_LLM_CONTEXT_ROWS`` / ``LIMIT_TIER_BULK_EXPORT_ROWS`` -- pass
    ``LIMIT_TIER_BULK_EXPORT_ROWS`` once the future bulk-data consumer exists.
    The default applies the low tier (``LIMIT_TIER_LLM_CONTEXT_ROWS``), which is
    the only tier any current caller needs.

    Row-limit enforcement is the reason this function returns SQL rather than a
    bare verdict: it is a query *modification*, not a pass/fail check. After all
    gates pass, ``_enforce_row_limit`` injects ``LIMIT {tier}`` into a query
    that has no LIMIT, clamps an existing LIMIT above the ceiling (or one whose
    value cannot be verified, such as ``LIMIT ALL``, a bound parameter, or a
    negative count) down to the ceiling, and leaves an existing LIMIT at or
    below the ceiling untouched. ``truncated`` is True in exactly the first two
    cases -- whenever the effective limit differs from what the query requested
    (an unbounded query is itself a request for every matching row, so capping
    it truncates) -- and False when the guardrail changed nothing. The SQL slot
    carries the enforced statement on success; when no modification was needed
    it is the original text unchanged.

    Return-shape note (tuple vs. structured object): a four-field positional
    tuple is the mandated contract for this change, and it is kept deliberately
    small and stable. Every field is documented above and every caller in this
    repo is updated in the same change, so the positional shape carries no
    real risk yet. The moment the result needs a further field (for example an
    ``effective_limit`` int so a caller can phrase "results limited to N rows",
    or additional modification metadata) or gains a second production caller,
    this should be migrated to a small structured result object (a frozen
    dataclass such as ``ValidationResult(passed, reason, sql, truncated)`` or a
    NamedTuple) so fields are accessed by name and future fields extend the
    object instead of growing the tuple. That migration is intentionally *not*
    done now because the change was scoped as a tuple and the existing self-test
    harness consumes it positionally; converting now would churn every case
    comment for no current consumer benefit.

    Every column reference is resolved against the table(s) it belongs to.
    Unqualified references bind to their scope's table when exactly one visible
    table has that column name; when several visible tables share the name the
    reference is genuinely ambiguous, so it is rejected rather than guessed
    (qualify it explicitly to pass). Nested scopes (subqueries, CTE bodies,
    EXISTS) are validated against their own tables, and SELECT output aliases
    used in ORDER BY / GROUP BY are recognized.

    On rejection the ``reason`` string (second element) is one of:

    * ``"unparseable_sql"`` -- sqlglot could not parse ``sql`` at all.
    * ``"no_statement"`` -- ``sql`` contains no real SQL statement (empty or
      whitespace-only input, comment-only input, stray ``;`` separators).
    * ``"multiple_statements"`` -- ``sql`` contains more than one real
      statement (stacked queries).
    * ``"not_a_select"`` -- ``sql`` is exactly one statement, but not a SELECT.
    * ``"disallowed_table:<tablename>"`` -- ``sql`` references a table that is
      not a key in ``schema`` (the actual offending table name is substituted).
    * ``"disallowed_column:<columnname>"`` -- ``sql`` references a column that
      does not resolve to a valid column of its table (the actual offending
      column name is substituted).
    * ``"disallowed_function:<functionname>"`` -- ``sql`` calls a SQL function
      that is not on the permitted whitelist (the actual offending function
      name is substituted).
    * ``"wildcard_select"`` -- some SELECT scope in ``sql`` (the top-level
      query or a nested subquery / CTE body) projects a bare ``*`` or
      ``table.*``, which would bypass the column whitelist.
    * ``"unconditioned_join"`` -- some JOIN clause in ``sql`` (the top-level
      query or a nested subquery / CTE body) has no real ON or USING condition:
      an explicit CROSS JOIN, an implicit comma join (``FROM a, b``), a
      ``JOIN``/``INNER JOIN``/etc. written without a constraint (sqlglot
      patches a synthetic ``ON TRUE`` onto these), or a NATURAL JOIN. Each
      would produce an unconditional (cartesian) join.
    """
    # A tier that is not a sanctioned ceiling is a caller bug, not a query
    # property -- fail fast rather than silently enforcing a number the
    # DESIGN_LOG policy never approved.
    if limit_tier not in _LIMIT_TIER_ROWS:
        raise ValueError(
            f"unsupported limit_tier {limit_tier!r}; pass one of the sanctioned "
            f"tier constants: {sorted(_LIMIT_TIER_ROWS)}"
        )

    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except ParseError:
        return False, "unparseable_sql", None, False

    # Drop sqlglot's empty-statement markers: ``None`` for inputs with no real
    # SQL, and ``exp.Semicolon`` for a ``;`` that only carries a comment. Only
    # real, executable statements count toward "exactly one".
    statements = [
        statement
        for statement in statements
        if statement is not None and not isinstance(statement, exp.Semicolon)
    ]

    if not statements:
        return False, "no_statement", None, False

    if len(statements) > 1:
        return False, "multiple_statements", None, False

    statement = statements[0]

    # Deliberate current scope: SELECT-only, not yet extended to compound set operations, so UNION/INTERSECT/EXCEPT (which parse to exp.Union) currently fall under "not_a_select".
    if not isinstance(statement, exp.Select):
        return False, "not_a_select", None, False

    # Table-level whitelist: every table referenced anywhere in the AST (FROM,
    # JOIN, and nested subqueries) must be a key in ``schema``. CTE names are
    # local aliases, not database tables, so they are skipped -- but tables
    # referenced inside a CTE body are still validated. SQLite identifier
    # matching is case-insensitive, so names are compared casefolded.
    cte_names = {
        cte.alias_or_name.casefold() for cte in statement.find_all(exp.CTE)
    }
    allowed_tables = {name.casefold() for name in schema}
    for table in statement.find_all(exp.Table):
        if table.name.casefold() in cte_names:
            continue
        if table.name.casefold() not in allowed_tables:
            return False, f"disallowed_table:{table.name}", None, False

    # Wildcard rejection: a bare ``SELECT *`` / ``table.*`` projection bypasses
    # the column whitelist below, so it is rejected here in every SELECT scope
    # (nested subqueries and CTE bodies included -- a star hidden there evades
    # the same check). This gate runs *before* column resolution because sqlglot
    # parses ``table.*`` as an exp.Column wrapping an exp.Star; the resolver
    # would otherwise report the misleading ``disallowed_column:*`` instead of
    # ``wildcard_select``.
    if _has_wildcard_select(statement):
        return False, "wildcard_select", None, False

    # JOIN-condition gate: every JOIN must carry a genuine ON predicate or a
    # USING column list. sqlglot parses an explicit CROSS JOIN and an implicit
    # comma join (FROM a, b) into the same kind="CROSS" node, and it patches a
    # synthetic on=TRUE onto a JOIN/INNER JOIN/etc. written without any
    # condition; both shapes (and NATURAL JOIN's on-less, using-less node) are
    # unconditioned and are rejected here -- in nested subqueries and CTE bodies
    # alike -- before column resolution can misreport their columns.
    if _has_unconditioned_join(statement):
        return False, "unconditioned_join", None, False

    # Column-level whitelist: every column reference must resolve to a real
    # column of one of the tables visible in its query scope.
    schema_columns = {
        table_name.casefold(): {column.casefold() for column in columns}
        for table_name, columns in schema.items()
    }
    reason = _validate_columns(statement, schema_columns, cte_names)
    if reason is not None:
        return False, reason, None, False

    # Function-call whitelist: after the column check passes, walk the whole
    # statement (nested scopes and CTE bodies included) and reject any call not
    # in ALLOWED_FUNCTIONS. Every call -- aggregates, scalar functions, unknown
    # exp.Anonymous calls, and CAST's type-casting syntax -- is an exp.Func, so
    # one traversal covers them all; names are matched case-insensitively.
    func_name = _first_disallowed_function(statement)
    if func_name is not None:
        return False, f"disallowed_function:{func_name}", None, False

    # All structural gates passed; enforce the row-limit ceiling. This is the
    # one step that modifies rather than rejects, so it produces the SQL slot
    # and the truncation flag of the return tuple.
    enforced_sql, truncated = _enforce_row_limit(statement, sql, limit_tier)
    return True, None, enforced_sql, truncated


@dataclass(frozen=True)
class _TableScope:
    """The tables and aliases visible to column references in one SELECT scope.

    ``occurrences`` lists each base-table occurrence in FROM/JOIN order (a
    self-join therefore lists the table twice). ``qualifiers`` maps every name
    that can qualify a column (real table names and aliases) to its real table.
    ``derived`` holds qualifiers that are CTE or subquery aliases: their output
    columns come from a nested scope that is validated separately, so
    references qualified by them are not checked against ``schema``.
    ``aliases`` holds this scope's SELECT output aliases (usable in ORDER BY /
    GROUP BY).
    """

    occurrences: tuple[str, ...]
    qualifiers: dict[str, str]
    derived: frozenset[str]
    aliases: frozenset[str]


def _scope_env(select_node: exp.Select, cte_names: set[str]) -> _TableScope:
    """Build the visible-table scope for one SELECT node."""
    occurrences: list[str] = []
    qualifiers: dict[str, str] = {}
    derived: set[str] = set()

    sources: list[exp.Expression] = []
    from_ = select_node.args.get("from_")
    if from_ is not None:
        sources.append(from_.this)
    sources.extend(join.this for join in select_node.args.get("joins") or ())

    for source in sources:
        if isinstance(source, exp.Table):
            table_name = source.name
            table_key = table_name.casefold()
            alias = source.alias
            if table_key in cte_names:
                derived.add(table_key)
                if alias:
                    derived.add(alias.casefold())
            else:
                occurrences.append(table_name)
                qualifiers[table_key] = table_name
                if alias:
                    qualifiers[alias.casefold()] = table_name
        elif isinstance(source, exp.Subquery) and source.alias:
            derived.add(source.alias.casefold())
        elif source.alias:
            derived.add(source.alias.casefold())

    aliases = {
        projection.alias.casefold()
        for projection in select_node.args.get("expressions") or ()
        if projection.alias
    }
    return _TableScope(
        occurrences=tuple(occurrences),
        qualifiers=qualifiers,
        derived=frozenset(derived),
        aliases=frozenset(aliases),
    )


def _nested_scopes(select_node: exp.Select) -> list[exp.Select]:
    """Return the SELECT scopes nested directly under ``select_node``."""
    return [
        nested
        for nested in select_node.find_all(exp.Select)
        if nested is not select_node
        and nested.find_ancestor(exp.Select) is select_node
    ]


def _resolve_column(
    column: exp.Column,
    scope_stack: list[_TableScope],
    schema_columns: dict[str, set[str]],
) -> str | None:
    """Return a ``disallowed_column`` reason if ``column`` cannot be resolved.

    Qualified references are looked up through the scope stack (so a nested
    scope can correlate against an outer table). References qualified by a CTE
    or subquery alias are skipped: their columns are outputs of a nested scope
    that was validated separately. Unqualified references bind when exactly one
    visible table in scope holds the name; if several visible tables share the
    name the reference is genuinely ambiguous without deeper resolution (types,
    expression lineage), so it is rejected rather than guessed.
    """
    column_name = column.name
    name = column_name.casefold()
    qualifier = column.table.casefold() if column.table else ""

    if qualifier:
        for scope in reversed(scope_stack):
            if qualifier in scope.qualifiers:
                if name not in schema_columns[scope.qualifiers[qualifier].casefold()]:
                    return f"disallowed_column:{column_name}"
                return None
            if qualifier in scope.derived:
                return None
        return f"disallowed_column:{column_name}"

    # Unqualified. A SELECT output alias referenced from ORDER BY / GROUP BY
    # is not a schema column reference.
    if (
        column.find_ancestor(exp.Order, exp.Group) is not None
        and name in scope_stack[-1].aliases
    ):
        return None

    for scope in reversed(scope_stack):
        holders = [
            table
            for table in scope.occurrences
            if name in schema_columns[table.casefold()]
        ]
        if len(holders) == 1:
            return None
        if len(holders) > 1:
            return f"disallowed_column:{column_name}"
    return f"disallowed_column:{column_name}"


def _validate_columns(
    select_node: exp.Select,
    schema_columns: dict[str, set[str]],
    cte_names: set[str],
) -> str | None:
    """Validate every column reference across ``select_node`` and its nested scopes."""
    scope_stack: list[_TableScope] = []
    return _validate_scope(select_node, scope_stack, schema_columns, cte_names)


def _validate_scope(
    select_node: exp.Select,
    scope_stack: list[_TableScope],
    schema_columns: dict[str, set[str]],
    cte_names: set[str],
) -> str | None:
    """Validate one SELECT scope (its own columns, then its nested scopes)."""
    scope_stack.append(_scope_env(select_node, cte_names))
    try:
        limit_node = select_node.args.get("limit")
        for column in select_node.find_all(exp.Column):
            if column.find_ancestor(exp.Select) is not select_node:
                continue  # belongs to a nested scope; validated there
            if limit_node is not None and column.find_ancestor(exp.Limit) is limit_node:
                # A column-shaped token inside this scope's own LIMIT clause is
                # not a real column reference: sqlglot models `LIMIT ALL` as a
                # Column whose name is the keyword ALL. It must not surface as
                # disallowed_column:ALL -- the row-limit enforcement step owns
                # LIMIT handling and clamps unverifiable limits instead.
                continue
            reason = _resolve_column(column, scope_stack, schema_columns)
            if reason is not None:
                return reason

        for nested in _nested_scopes(select_node):
            reason = _validate_scope(nested, scope_stack, schema_columns, cte_names)
            if reason is not None:
                return reason
    finally:
        scope_stack.pop()
    return None


if __name__ == "__main__":
    # Build the schema from the real DDL in db/schema.sql (same directory as
    # this file) so the harness always reflects the actual tables and columns.
    schema_ddl = Path(__file__).with_name("schema.sql").read_text(
        encoding="utf-8"
    )
    schema = {
        create.this.this.name: {
            column.name for column in create.this.find_all(exp.ColumnDef)
        }
        for create in sqlglot.parse(schema_ddl, read="sqlite")
        if isinstance(create, exp.Create) and isinstance(create.this, exp.Schema)
    }
    print("schema loaded from db/schema.sql:")
    for table_name, columns in schema.items():
        print(f"  {table_name}: {sorted(columns)}")

    cases = [
        # Bare wildcard on an allowed table. A star is not an exp.Column, so it
        # would pass the column whitelist (under the old 2-tuple contract this
        # previously printed (True, None)); the wildcard gate rejects it before
        # row-limit enforcement is reached.
        "SELECT * FROM transactions",  # -> (False, 'wildcard_select', None, False)
        "DROP TABLE transactions",  # single statement, not a SELECT
        "SELECT 1; DROP TABLE x",  # stacked statements
        "",  # no statement at all
        "this is not sql at all",  # unparseable garbage
        # sqlglot emits an extra exp.Semicolon (an empty statement) for a ";"
        # that carries only a trailing comment, so a naive count would reject
        # this benign single SELECT as "multiple". It must pass, which verifies
        # the counting ignores empty-statement markers.
        "SELECT 1; -- trailing comment",
        # Compound set operation: currently rejected as "not_a_select" --
        # deliberate SELECT-only scope (see comment at the statement-type check).
        "SELECT 1 UNION SELECT 2",
        # Table-level whitelist checks.
        # The table whitelist runs before the wildcard gate, so this unlisted
        # table still surfaces as disallowed_table:orders, not wildcard_select.
        "SELECT * FROM orders",  # unlisted table -> disallowed_table:orders
        "SELECT COUNT(*) FROM transactions",  # exactly transactions -> (True, None, LIMIT 500 injected, True)
        # Both permitted tables referenced together -> allowed (explicit column
        # list; a bare SELECT * here would be rejected as wildcard_select).
        "SELECT t.country, c.timezone FROM transactions t "
        "JOIN country_timezones c ON t.country = c.country",
        # Qualified wildcard forms. sqlglot parses table.* as exp.Column(this=
        # exp.Star); without the wildcard gate the column resolver would report
        # the misleading disallowed_column:*. Both must be wildcard_select.
        "SELECT transactions.* FROM transactions",
        "SELECT t.* FROM transactions t",
        # Wildcard hidden inside a nested subquery: the inner SELECT * projects
        # a star the outer scope's column whitelist cannot see, so the gate
        # walks every SELECT scope, not just the outermost query.
        "SELECT COUNT(*) FROM (SELECT * FROM transactions)",
        # Same for a wildcard hidden inside a CTE body (the CTE name is a local
        # alias, not a schema table, so nothing else flags the inner SELECT *).
        "WITH x AS (SELECT * FROM transactions) SELECT COUNT(*) FROM x",
        # Explicit column lists are unaffected and still pass normally.
        "SELECT quantity, unit_price FROM transactions",
        # Column-level whitelist checks.
        "SELECT quantity FROM transactions",  # real column -> (True, None, LIMIT 500 injected, True)
        # Real column, explicitly qualified -> allowed.
        "SELECT transactions.quantity FROM transactions",
        # Nonexistent column on a real table -> disallowed_column:nonexistent.
        "SELECT transactions.nonexistent FROM transactions",
        # SELECT output alias used in ORDER BY is not a schema column -> allowed.
        "SELECT country, COUNT(*) AS n FROM transactions "
        "GROUP BY country ORDER BY n DESC",
        # Unqualified column in a multi-table query held by both visible tables
        # (country exists in transactions AND country_timezones) -> genuinely
        # ambiguous, so it is rejected rather than guessed.
        "SELECT country FROM transactions t "
        "JOIN country_timezones c ON t.country = c.country",
        # Unqualified column in the same multi-table query that only one table
        # holds (timezone exists only in country_timezones) -> resolves uniquely.
        "SELECT timezone FROM transactions t "
        "JOIN country_timezones c ON t.country = c.country",
        # Function-call whitelist: every permitted function in one query.
        # COUNT/SUM/AVG/MIN/MAX parse to exp.AggFunc subclasses, ROUND/CAST/
        # COALESCE to scalar exp.Func subclasses, STRFTIME to exp.TimeToStr, and
        # DATE/DATETIME to an exp.Date and an exp.Anonymous respectively. All
        # twelve names must resolve to the whitelist and pass together.
        "SELECT COUNT(*), SUM(quantity), AVG(unit_price), "
        "MIN(unit_price), MAX(unit_price), ROUND(AVG(unit_price), 2) AS r, "
        "STRFTIME('%Y', invoice_timestamp) AS yr, "
        "DATE(invoice_timestamp) AS d, DATETIME(invoice_timestamp) AS dt, "
        "CAST(quantity AS REAL) AS q, COALESCE(description, 'n/a') AS descr "
        "FROM transactions GROUP BY STRFTIME('%Y', invoice_timestamp)",
        # Function names are matched case-insensitively, so mixed/lower case
        # still passes.
        "select count(*), round(avg(unit_price), 2), date(invoice_timestamp) "
        "from transactions",
        # CAST edge case: CAST(x AS T) is sqlglot's exp.Cast, whose ``name``
        # property is the *inner* expression (quantity), not "CAST". It must be
        # recognized as the permitted CAST function rather than misidentified as
        # a column or as some non-call construct.
        "SELECT CAST(quantity AS INTEGER) FROM transactions",
        # The :: cast operator also parses to exp.Cast -> same handling.
        "SELECT quantity::INTEGER FROM transactions",
        # Disallowed functions. RANDOM() parses to a typed exp.Rand (sqlite
        # renders/executes it as RANDOM); LOAD_EXTENSION stays exp.Anonymous.
        # Both must be rejected by name.
        "SELECT RANDOM() FROM transactions",
        "SELECT LOAD_EXTENSION('foo') FROM transactions",
        # DATEDIFF edge case: SQLite has no native DATEDIFF, so sqlglot parses
        # this to exp.DateDiff and re-renders it under the sqlite dialect as
        # CAST((JULIANDAY(...) - JULIANDAY(...)) AS INTEGER). A naive read of
        # the leading rendered token would therefore misidentify it as the
        # *permitted* CAST; the reparse type-mismatch check in
        # _function_call_name must instead surface the original DATEDIFF name
        # and reject it as disallowed_function:DATEDIFF.
        "SELECT DATEDIFF(invoice_timestamp, invoice_timestamp) FROM transactions",
        # The whitelist covers every scope, including nested subqueries.
        "SELECT COUNT(*) FROM (SELECT RANDOM() AS r FROM transactions)",
        # JOIN-condition checks. Every JOIN must carry a genuine ON predicate or
        # a USING column list; unconditioned joins are rejected as
        # "unconditioned_join" no matter how they are spelled.
        # A normal JOIN with an ON condition still passes.
        "SELECT t.quantity, c.timezone FROM transactions t "
        "JOIN country_timezones c ON t.country = c.country",  # -> (True, None, LIMIT 500 injected, True)
        # A USING clause conditions the join just like ON -> allowed. (USING's
        # column names are exp.Identifier nodes, not exp.Column references, so
        # the column whitelist never sees them.)
        "SELECT t.quantity, c.timezone FROM transactions t "
        "JOIN country_timezones c USING (country)",  # -> (True, None, LIMIT 500 injected, True)
        # Explicit CROSS JOIN. sqlglot parses it as exp.Join(kind="CROSS") with
        # no on/using argument -> unconditioned_join.
        "SELECT t.quantity, c.timezone FROM transactions t "
        "CROSS JOIN country_timezones c",  # -> (False, 'unconditioned_join', None, False)
        # Implicit comma join: FROM a, b is indistinguishable in the AST from an
        # explicit CROSS JOIN (both are kind="CROSS") -> unconditioned_join.
        "SELECT t.quantity, c.timezone FROM transactions t, country_timezones c",  # -> (False, 'unconditioned_join', None, False)
        # JOIN keyword with no condition. sqlglot's parser patches a synthetic
        # on=TRUE onto the node so the query still parses; that placeholder is
        # not a real condition -> unconditioned_join.
        "SELECT t.quantity, c.timezone FROM transactions t "
        "JOIN country_timezones c",  # -> (False, 'unconditioned_join', None, False)
        # Same for LEFT JOIN (or any JOIN keyword) written without a condition.
        "SELECT t.quantity, c.timezone FROM transactions t "
        "LEFT JOIN country_timezones c",  # -> (False, 'unconditioned_join', None, False)
        # NATURAL JOIN carries neither ON nor USING (only method="NATURAL"), so
        # it falls under the no-ON/USING rule too.
        "SELECT t.quantity, c.timezone FROM transactions t "
        "NATURAL JOIN country_timezones c",  # -> (False, 'unconditioned_join', None, False)
        # The gate reaches nested scopes, consistent with the wildcard gate: a
        # cross/comma join hidden in a subquery body ...
        "SELECT COUNT(*) FROM (SELECT t.quantity FROM transactions t "
        "CROSS JOIN country_timezones c)",  # -> (False, 'unconditioned_join', None, False)
        # ... or in a CTE body is still rejected.
        "WITH x AS (SELECT t.quantity FROM transactions t, country_timezones c) "
        "SELECT COUNT(*) FROM x",  # -> (False, 'unconditioned_join', None, False)
        # And a conditioned JOIN nested in a CTE body still passes.
        "WITH x AS (SELECT t.quantity FROM transactions t "
        "JOIN country_timezones c ON t.country = c.country) "
        "SELECT COUNT(*) FROM x",  # -> (True, None, LIMIT 500 injected, True)
    ]

    # Run every structural-gate case above and print the full 4-tuple
    # (passed, reason, sql, truncated). The trailing comments on the cases
    # document the expected verdict; the row-limit checks below assert theirs.
    print()
    print("=== Structural-gate cases (validate_sql now returns a 4-tuple) ===")
    for case in cases:
        print(f"{case!r} -> {validate_sql(case, schema)}")

    # -------------------------------------------------------------------------
    # Row-limit enforcement: explicit, assertion-based checks. ``check`` runs
    # validate_sql and verifies every element of the return contract:
    #   - passed / reason as expected,
    #   - a passing query must return a non-None sql slot whose top-level LIMIT
    #     equals expected_limit,
    #   - a rejected query must return sql=None (enforcement is never reached),
    #   - truncated must equal expected_truncated,
    #   - when expect_unchanged, the sql slot must equal the input verbatim (a
    #     compliant query is never rewritten).
    # -------------------------------------------------------------------------
    failures = 0

    def check(
        label: str,
        sql: str,
        *,
        passed: bool,
        reason: str | None = None,
        truncated: bool,
        effective_limit: int | None,
        expect_unchanged: bool = False,
        must_contain: tuple[str, ...] = (),
        tier: int = LIMIT_TIER_LLM_CONTEXT_ROWS,
    ) -> None:
        global failures

        got_passed, got_reason, got_sql, got_truncated = validate_sql(
            sql, schema, limit_tier=tier
        )
        problems: list[str] = []
        if got_passed is not passed:
            problems.append(f"passed={got_passed!r}, expected {passed!r}")
        if got_truncated is not truncated:
            problems.append(f"truncated={got_truncated!r}, expected {truncated!r}")
        if got_passed:
            if got_reason is not None:
                problems.append(f"reason={got_reason!r}, expected None on pass")
            if got_sql is None:
                problems.append("sql slot is None on a passing query")
            else:
                top_limit = sqlglot.parse_one(got_sql, read="sqlite").args.get("limit")
                got_limit = _limit_count(top_limit) if top_limit is not None else None
                if got_limit != effective_limit:
                    problems.append(
                        f"enforced top-level LIMIT={got_limit!r}, "
                        f"expected {effective_limit!r}"
                    )
                if expect_unchanged and got_sql != sql:
                    problems.append("sql was rewritten but must be unchanged")
                for fragment in must_contain:
                    if fragment not in got_sql:
                        problems.append(f"returned sql is missing {fragment!r}")
        else:
            if got_reason != reason:
                problems.append(f"reason={got_reason!r}, expected {reason!r}")
            if got_sql is not None:
                problems.append("sql slot is not None on a rejected query")

        status = "ok" if not problems else "FAIL -> " + "; ".join(problems)
        print(f"[{status}] {label}")
        print(f"    sql      : {sql!r}")
        print(f"    returned : {validate_sql(sql, schema, limit_tier=tier)!r}")
        if problems:
            failures += 1

    print()
    print("=== Row-limit enforcement acceptance checks ===")

    # 1. No LIMIT clause -> 500 injected, truncated=True.
    check(
        "no LIMIT gets 500 injected",
        "SELECT quantity FROM transactions",
        passed=True,
        truncated=True,
        effective_limit=LIMIT_TIER_LLM_CONTEXT_ROWS,
    )

    # 2. Existing LIMIT below the ceiling -> left at 10, truncated=False,
    #    original text returned unchanged.
    check(
        "LIMIT 10 stays at 10 (unchanged, not truncated)",
        "SELECT quantity FROM transactions LIMIT 10",
        passed=True,
        truncated=False,
        effective_limit=10,
        expect_unchanged=True,
    )

    # 3. Existing LIMIT above the ceiling -> clamped to 500, truncated=True.
    check(
        "LIMIT 100000 gets clamped to 500",
        "SELECT quantity FROM transactions LIMIT 100000",
        passed=True,
        truncated=True,
        effective_limit=LIMIT_TIER_LLM_CONTEXT_ROWS,
    )

    # 4. Existing LIMIT exactly at the ceiling -> unchanged, truncated=False.
    check(
        "LIMIT 500 (at the ceiling) stays unchanged",
        "SELECT quantity FROM transactions LIMIT 500",
        passed=True,
        truncated=False,
        effective_limit=LIMIT_TIER_LLM_CONTEXT_ROWS,
        expect_unchanged=True,
    )

    # 5. LIMIT 0 is already at-or-below the ceiling -> unchanged, truncated=False.
    check(
        "LIMIT 0 stays at 0",
        "SELECT quantity FROM transactions LIMIT 0",
        passed=True,
        truncated=False,
        effective_limit=0,
        expect_unchanged=True,
    )

    # 6. SQLite treats a negative LIMIT as *no limit*, so -1 is unbounded and
    #    must be clamped like a missing or over-ceiling LIMIT.
    check(
        "LIMIT -1 (unbounded in SQLite) gets clamped to 500",
        "SELECT quantity FROM transactions LIMIT -1",
        passed=True,
        truncated=True,
        effective_limit=LIMIT_TIER_LLM_CONTEXT_ROWS,
    )

    # 7. A LIMIT whose value cannot be verified (LIMIT ALL) is clamped too --
    #    the guardrail may not assume an unprovable bound is compliant.
    check(
        "LIMIT ALL (unverifiable) gets clamped to 500",
        "SELECT quantity FROM transactions LIMIT ALL",
        passed=True,
        truncated=True,
        effective_limit=LIMIT_TIER_LLM_CONTEXT_ROWS,
    )

    # 8. Clamping preserves an OFFSET clause carried by the original query.
    check(
        "LIMIT 100000 OFFSET 20 -> clamped to 500, OFFSET preserved",
        "SELECT quantity FROM transactions LIMIT 100000 OFFSET 20",
        passed=True,
        truncated=True,
        effective_limit=LIMIT_TIER_LLM_CONTEXT_ROWS,
        must_contain=("OFFSET 20",),
    )

    # 9. A LIMIT inside a nested subquery does not satisfy the ceiling: the
    #    outermost SELECT has no LIMIT, so 500 is injected at the top level.
    check(
        "nested LIMIT 10 does not cap the outer query -> 500 injected",
        "SELECT COUNT(*) FROM (SELECT quantity FROM transactions LIMIT 10)",
        passed=True,
        truncated=True,
        effective_limit=LIMIT_TIER_LLM_CONTEXT_ROWS,
    )

    # The higher bulk-export tier is currently unused by any caller, but it is a
    # sanctioned selector: these checks prove the tier plumbing works so the
    # future bulk-data consumer can rely on it without further API changes.
    check(
        "bulk tier: LIMIT 100000 is above 50000 -> clamped to 50000",
        "SELECT quantity FROM transactions LIMIT 100000",
        passed=True,
        truncated=True,
        effective_limit=LIMIT_TIER_BULK_EXPORT_ROWS,
        tier=LIMIT_TIER_BULK_EXPORT_ROWS,
    )
    check(
        "bulk tier: LIMIT 10000 is at/below 50000 -> unchanged",
        "SELECT quantity FROM transactions LIMIT 10000",
        passed=True,
        truncated=False,
        effective_limit=10_000,
        expect_unchanged=True,
        tier=LIMIT_TIER_BULK_EXPORT_ROWS,
    )
    check(
        "bulk tier: no LIMIT -> 50000 injected",
        "SELECT quantity FROM transactions",
        passed=True,
        truncated=True,
        effective_limit=LIMIT_TIER_BULK_EXPORT_ROWS,
        tier=LIMIT_TIER_BULK_EXPORT_ROWS,
    )

    # A rejected query must surface sql=None and truncated=False: enforcement is
    # a post-pass modification step and never runs on a query that failed a gate.
    check(
        "rejected query (wildcard) returns sql=None, truncated=False",
        "SELECT * FROM transactions",
        passed=False,
        reason="wildcard_select",
        truncated=False,
        effective_limit=None,
    )

    # An unsanctioned tier is a caller bug -> fail fast with ValueError.
    try:
        validate_sql(
            "SELECT quantity FROM transactions",
            schema,
            limit_tier=501,  # not a sanctioned tier constant
        )
    except ValueError as exc:
        print(f"[ok] unsanctioned limit_tier raises ValueError: {exc}")
    else:
        print("[FAIL] unsanctioned limit_tier did not raise ValueError")
        failures += 1

    if failures:
        raise SystemExit(f"{failures} row-limit check(s) FAILED")
    print()
    print("All row-limit enforcement checks passed.")

