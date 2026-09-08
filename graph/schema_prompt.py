"""Deterministic, live-introspected system-prompt block describing the DB schema.

This module owns the *first, stable segment* of the eventual full system prompt
for ``extract_query_intent`` (Node 2): a plain-text description of the live
database schema -- table names, column names and their meanings, and the closed
value sets for ``country`` and ``line_item_type``.

Per DESIGN_LOG.md section 16's cache-efficiency note, once the full system
prompt exists it must be assembled as the stable schema block followed by the
variable, query-specific content (the user's question), so that the identical
prefix can be served from a provider-side prompt cache across calls. Two
consequences are load-bearing here, not polish:

* The block must be **query-independent** -- it never references the user's
  query or any per-request state, so it can be generated once and reused
  verbatim.
* The block must be **deterministic** -- repeated builds against an unchanged
  database produce byte-identical text: tables are ordered by name
  (``sqlite_master``), columns keep their ``PRAGMA table_info`` ordinal order,
  and every value set is sorted. Nothing in the output depends on set or dict
  iteration order.

Live-introspection contract (never a hardcoded duplicate of the schema, per the
pattern used everywhere else in this project -- e.g. ``graph.nodes._live_schema``
/ ``_live_transactions_columns`` and the ``compile_sql`` no-matching-data gate):

* Table names are read from ``sqlite_master`` over a read-only connection
  (``db.connect.connect_readonly``); internal ``sqlite_%`` tables are skipped.
* Each table's columns -- names, declared types, NOT-NULL status, primary-key
  status, in ordinal order -- are read from ``PRAGMA table_info``.
* ``line_item_type``'s closed value set is read from the live DDL itself: the
  ``CHECK (line_item_type IN (...))`` constraint stored in ``sqlite_master.sql``,
  parsed with ``sqlglot`` (the same DDL-parsing dependency the test suite already
  uses on ``db/schema.sql``). The schema's constraint is authoritative for the
  closed set even for a value that happens to have zero rows today.
* ``country``'s closed value set is read from the live rows
  (``SELECT DISTINCT country FROM transactions``) -- the exact value source the
  ``compile_sql`` no-matching-data gate validates country filters against, so the
  block only advertises values a filter could actually match.

One part of the content cannot be introspected: SQLite stores no semantic
metadata, so each column's *meaning* is curated prose held in
``_COLUMN_MEANINGS`` below (keyed by the live ``(table, column)`` pairs, aligned
with the ``db/schema.sql`` column comments and DESIGN_LOG.md semantics). To stop
schema drift from silently producing an under-described prompt, the builder
fails loudly whenever an introspected table or column has no meaning entry --
it never quietly emits a nameless column or a column the model would have to
guess about.

The module deliberately contains no query-specific content and no other
system-prompt sections; the output-format conventions block (empty lists and
present-flag consistency) lives in the sibling module
``graph/conventions_prompt.py``, and the ambiguity-trigger checklist remains a
separate follow-up block.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlglot import exp, parse_one

from db.connect import connect_readonly

# Default live database path, derived exactly like the rest of the repository
# (e.g. ``graph.nodes`` / ``scripts/run_load.py``): project root / data /
# processed / sentrasql.db. Callers may override via ``build_schema_block``'s
# ``db_path`` argument.
_DEFAULT_DB_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "processed" / "sentrasql.db"
)

# Purpose line rendered under each introspected table name. Keyed by the real
# table name; a live table without an entry is a hard error (see module
# docstring), never silently described as "unknown".
_TABLE_PURPOSES: dict[str, str] = {
    "transactions": (
        "Retail fact table: one row per invoice line item (one row per "
        "(invoice, stock_code) line). A row's line amount is quantity * "
        "unit_price."
    ),
    "country_timezones": (
        "Country -> IANA-timezone lookup. One row per dataset country name; "
        "backs timezone-aware interpretation of invoice_timestamp."
    ),
}

# Meaning gloss rendered for every introspected column. Keyed by the live
# (table, column) pairs and aligned with the db/schema.sql column comments and
# the business semantics recorded in DESIGN_LOG.md. A live column without an
# entry is a hard error (see module docstring).
_COLUMN_MEANINGS: dict[tuple[str, str], str] = {
    (
        "transactions",
        "invoice_id",
    ): (
        'Invoice number, e.g. "536365". An invoice_id starting with "C" '
        "denotes a cancelled invoice (see is_cancelled_invoice)."
    ),
    (
        "transactions",
        "is_cancelled_invoice",
    ): (
        'Derived flag: 1 when the row\'s invoice_id starts with "C" (a '
        "cancelled invoice), else 0. It carries no information beyond the "
        "invoice_id prefix but is directly filterable."
    ),
    (
        "transactions",
        "stock_code",
    ): (
        "Stock/product code identifying the line item, stored "
        "whitespace-trimmed and uppercase-normalized. Real products and "
        "non-product line items (fees, adjustments) share this column, so a "
        "stock_code is not in itself a product-only identifier."
    ),
    (
        "transactions",
        "description",
    ): (
        "Free-text product description; NULL when the source dataset had no "
        "value for it."
    ),
    (
        "transactions",
        "line_item_type",
    ): (
        'Line-item classification. Closed value set (see below): "product" '
        "= genuine sellable item; \"fee\" = charge such as postage or bank "
        'charges; "adjustment" = correction such as a discount, manual '
        "entry, or gift voucher."
    ),
    (
        "transactions",
        "quantity",
    ): (
        "Signed integer quantity of the line. Negative = a return line item. "
        "A return (quantity < 0) is a distinct concept from a cancelled "
        "invoice (is_cancelled_invoice = 1): a return can appear on a "
        "non-cancelled invoice."
    ),
    (
        "transactions",
        "unit_price",
    ): (
        "Per-unit price of the line. 0 is a legitimate real value (free "
        "samples / promotional items), not missing data."
    ),
    (
        "transactions",
        "customer_id",
    ): (
        "Numeric customer identifier. NULL denotes a guest / unknown "
        "customer; NULL is a real, meaningful state, not missing data."
    ),
    (
        "transactions",
        "country",
    ): (
        'Exact customer-country name string from the dataset, e.g. "United '
        'Kingdom", "USA", "EIRE", "RSA", "Korea". Closed value set: see the '
        "closed value sets section below."
    ),
    (
        "transactions",
        "invoice_timestamp",
    ): (
        'Invoice date/time stored as an ISO-8601 text string, e.g. '
        '"2009-12-01 07:45:00".'
    ),
    (
        "country_timezones",
        "country",
    ): (
        "Exact dataset country name (primary key). Shares the closed value "
        "set listed for transactions.country below."
    ),
    (
        "country_timezones",
        "timezone",
    ): (
        'IANA timezone name for the country, e.g. "Europe/London"; the value '
        "to use when interpreting invoice_timestamp in that country's local "
        "time."
    ),
}

# How each closed value set is introspected live. Keys are (table, column)
# pairs; ``"distinct"`` reads the values present in live rows (the same source
# compile_sql's no-matching-data gate validates country filters against),
# ``"check"`` reads the column's CHECK-constraint literal list from the stored
# DDL. A spec whose column does not exist in the live schema is simply not
# rendered -- the missing-gloss hard error above already fired if the whole
# table/column disappeared.
_VALUE_SET_KINDS: dict[tuple[str, str], str] = {
    ("transactions", "country"): "distinct",
    ("transactions", "line_item_type"): "check",
}


def _introspect_tables(conn: sqlite3.Connection) -> list[dict]:
    """Return every live non-internal table and its columns, in stable order.

    Tables come from ``sqlite_master`` ordered by name (deterministic); each
    table maps to its ``PRAGMA table_info`` columns in ordinal order with
    ``name``, ``type``, ``notnull`` (bool) and ``pk`` (bool) fields.
    """
    names = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    tables: list[dict] = []
    for name in names:
        columns = []
        for _cid, col_name, col_type, notnull, _default, pk in conn.execute(
            f'PRAGMA table_info("{name}")'
        ):
            columns.append(
                {
                    "name": col_name,
                    "type": col_type,
                    "notnull": bool(notnull),
                    "pk": bool(pk),
                }
            )
        tables.append({"name": name, "columns": columns})
    return tables


def _check_constraint_values(
    conn: sqlite3.Connection, table: str, column: str
) -> list[str]:
    """Read a column's CHECK-constraint literal list from the live stored DDL.

    The authoritative closed value set for a schema-constrained column is the
    ``CHECK (column IN (...))`` clause of its CREATE TABLE statement as stored
    in ``sqlite_master.sql`` -- not a hardcoded copy of the value list and not
    merely the values that happen to have rows today. The DDL is parsed with
    ``sqlglot``; any ``IN`` whose left side is the target column and whose
    right side is a literal list is collected, matching both column-level
    CHECKs and table-level CHECKs. A failure to find the set is a hard error:
    a missing constraint means this module's contract with the schema has
    broken and must be fixed loudly, not papered over.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    if row is None or not row[0]:
        raise RuntimeError(
            f"Cannot introspect CHECK constraint for {table}.{column}: no "
            f"CREATE TABLE statement for {table!r} found in sqlite_master."
        )
    create = parse_one(row[0], read="sqlite")
    if not isinstance(create, exp.Create):
        raise RuntimeError(
            f"Cannot introspect CHECK constraint for {table}.{column}: the "
            f"stored DDL for {table!r} did not parse as a CREATE statement."
        )

    values: list[str] = []
    for in_expr in create.find_all(exp.In):
        if not isinstance(in_expr.this, exp.Column):
            continue
        if in_expr.this.name != column:
            continue
        for literal in in_expr.expressions:
            if not isinstance(literal, exp.Literal) or not literal.is_string:
                raise RuntimeError(
                    f"CHECK constraint for {table}.{column} contains a "
                    "non-string-literal member; the closed value set cannot "
                    "be described reliably."
                )
            values.append(literal.name)
    if not values:
        raise RuntimeError(
            f"No CHECK-constraint literal list found for {table}.{column} in "
            "the live stored DDL."
        )
    return sorted(set(values))


def _distinct_column_values(
    conn: sqlite3.Connection, table: str, column: str
) -> list[str]:
    """Return the distinct live values of ``table.column``, sorted.

    Data-defined value sets (notably ``country``) are read from the live rows
    rather than a hardcoded list, matching the value source the ``compile_sql``
    no-matching-data gate uses to accept country filters.
    """
    rows = conn.execute(
        f'SELECT DISTINCT "{column}" FROM "{table}" ORDER BY "{column}"'
    )
    return [row[0] for row in rows if row[0] is not None]


def _column_declaration(column: dict) -> str:
    """Render a column's parenthesized type/nullability/PK declaration."""
    parts = [column["type"]] if column["type"] else []
    parts.append("NOT NULL" if column["notnull"] else "nullable")
    if column["pk"]:
        parts.append("PRIMARY KEY")
    return f"({', '.join(parts)})"


def _render_block(tables: list[dict], value_sets: dict[str, list[str]]) -> str:
    """Render the deterministic schema-description block from introspected data."""
    lines = [
        "DATABASE SCHEMA",
        "===============",
        "This system answers analytics questions against a SQLite database. "
        "The schema below is the authoritative reference for every table, "
        "column, and closed value set available when interpreting a question. "
        "All names and values are exact and case-sensitive; match them "
        "verbatim.",
    ]
    for table in tables:
        lines.append("")
        lines.append(f"Table: {table['name']}")
        lines.append(f"  Purpose: {_TABLE_PURPOSES[table['name']]}")
        lines.append("  Columns:")
        for column in table["columns"]:
            meaning = _COLUMN_MEANINGS[(table["name"], column["name"])]
            lines.append(
                f"    - {column['name']} {_column_declaration(column)}: "
                f"{meaning}"
            )

    if value_sets:
        lines.append("")
        lines.append(
            "Closed value sets (match these exactly; they are the only "
            "accepted values):"
        )
        for key in sorted(value_sets):
            lines.append(f"  {key}: {', '.join(value_sets[key])}")

    return "\n".join(lines)


def build_schema_block(db_path: str | Path | None = None) -> str:
    """Build the stable schema-description block for the system prompt.

    Args:
        db_path: Path to the live SQLite database. Defaults to the repository's
            ``data/processed/sentrasql.db`` when omitted.

    Returns:
        A single deterministic string -- the schema block to be placed before
        any query-specific content in the eventual full system prompt.

    Raises:
        RuntimeError: when the live schema drifts from the curated meaning
            entries (a table or column with no purpose/meaning, or a
            CHECK-constrained value set that cannot be introspected), so an
            under-described schema can never reach the model silently.
    """
    resolved_path = _DEFAULT_DB_PATH if db_path is None else Path(db_path)
    with connect_readonly(resolved_path) as conn:
        tables = _introspect_tables(conn)

        # Fail loudly (never silently under-describe) when the live schema has
        # drifted past the curated meaning entries.
        missing_purposes = [
            t["name"] for t in tables if t["name"] not in _TABLE_PURPOSES
        ]
        missing_meanings = [
            (t["name"], c["name"])
            for t in tables
            for c in t["columns"]
            if (t["name"], c["name"]) not in _COLUMN_MEANINGS
        ]
        if missing_purposes or missing_meanings:
            problems = []
            if missing_purposes:
                problems.append(
                    "no purpose entry: " + ", ".join(sorted(missing_purposes))
                )
            if missing_meanings:
                problems.append(
                    "no meaning entry: "
                    + ", ".join(
                        f"{table}.{column}"
                        for table, column in sorted(missing_meanings)
                    )
                )
            raise RuntimeError(
                "Live schema drifted past the curated meaning entries "
                f"({'; '.join(problems)}). Add purpose/meaning entries in "
                "graph/schema_prompt.py before building the schema block."
            )

        # Introspect each closed value set whose column exists in the live
        # schema, keyed as "table.column" in the rendered block.
        live_columns = {
            (t["name"], c["name"]) for t in tables for c in t["columns"]
        }
        value_sets: dict[str, list[str]] = {}
        for (table, column), kind in _VALUE_SET_KINDS.items():
            if (table, column) not in live_columns:
                continue
            if kind == "distinct":
                values = _distinct_column_values(conn, table, column)
            elif kind == "check":
                values = _check_constraint_values(conn, table, column)
            else:  # pragma: no cover - guarded by the fixed module dict
                raise RuntimeError(
                    f"Unknown value-set kind {kind!r} for {table}.{column}."
                )
            value_sets[f"{table}.{column}"] = values

    return _render_block(tables, value_sets)


if __name__ == "__main__":
    print(build_schema_block())


