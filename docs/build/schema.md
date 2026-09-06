<!-- APPEND-ONLY: add new dated entries at the bottom. Never edit or delete existing content above. -->

## 6. Database Schema — `db/schema.sql`

The database layer lives in `db/`. `db/schema.sql` is the SQLite DDL source of
truth; `db/__init__.py` makes `db` an importable Python package. The schema was
applied to a new SQLite database at `data/processed/sentrasql.db` using the
project's `.venv` Python (`sqlite3` stdlib, `executescript`), and it executes
without error. `transactions.line_item_type` is restricted to exactly
`'product'`, `'fee'`, or `'adjustment'` by an in-DDL CHECK constraint whose
enforcement was verified with a real INSERT test (see 6.4).

**Current state:** `transactions` is no longer schema-only — it holds the
1,067,354 rows loaded by `db/load.py` (see section 8), and `country_timezones`
holds the 43 country → IANA-timezone rows loaded by
`scripts/run_load_timezones.py` (see section 9).

Decisions recorded here:

- **No indexes yet** — deliberately deferred until query patterns are known.
- **`country_timezones` is populated at load time, not by DDL** — it backs the
  timezone-handling requirement from `DESIGN_LOG.md` section 1.6 (a country →
  IANA-timezone mapping the agent reasons about at query time). See section 9.
- **`line_item_type` is CHECK-constrained in DDL** — restricted to exactly
  `'product'`, `'fee'`, or `'adjustment'` so the load layer can never persist an
  unclassified line type. The other two value-domain invariants (`stock_code`
  whitespace-trimmed / case-normalized and `invoice_timestamp` ISO-8601 string)
  remain documented-as-comments load-layer contracts, since neither maps to a
  declarative SQL constraint.
- **`description` and `customer_id` are nullable** (they have `NULL` values in the
  source dataset: 4,382 missing descriptions and 243,007 missing customer IDs);
  everything else is `NOT NULL`.

### 6.1 Table `transactions` (DDL as written)

| Column | Declared type | Nullable | Constraint | Notes |
| --- | --- | --- | --- | --- |
| `invoice_id` | `TEXT` | no | — | Invoice number; `"C"` prefix marks a cancelled invoice |
| `is_cancelled_invoice` | `BOOLEAN` | no | — | `1` when `invoice_id` carries the cancellation flag, else `0` |
| `stock_code` | `TEXT` | no | — | Whitespace-trimmed, case-normalized (uppercase) |
| `description` | `TEXT` | yes | — | `NULL` when the source value is missing |
| `line_item_type` | `TEXT` | no | `CHECK (line_item_type IN ('product', 'fee', 'adjustment'))` | One of `product` / `fee` / `adjustment` |
| `quantity` | `INTEGER` | no | — | Signed; negative = return |
| `unit_price` | `REAL` | no | — | `0` is valid (free samples / promotional items) |
| `customer_id` | `REAL` | yes | — | `NULL` for guest / unknown customers |
| `country` | `TEXT` | no | — | Matches the dataset's `Country` column values |
| `invoice_timestamp` | `TEXT` | no | — | ISO-8601 datetime string, e.g. `2009-12-01 07:45:00` |

### 6.2 Table `country_timezones` (DDL as written)

| Column | Declared type | Nullable | Constraint | Notes |
| --- | --- | --- | --- | --- |
| `country` | `TEXT` | no | `PRIMARY KEY` | Matches the dataset's `Country` column values |
| `timezone` | `TEXT` | no | — | IANA timezone name, e.g. `Europe/London`, `Asia/Dubai` |

### 6.3 PRAGMA verification (actual output against `data/processed/sentrasql.db`)

```
PRAGMA table_info(transactions);
  cid=0  name='invoice_id'  type='TEXT'  notnull=1  dflt_value=None  pk=0
  cid=1  name='is_cancelled_invoice'  type='BOOLEAN'  notnull=1  dflt_value=None  pk=0
  cid=2  name='stock_code'  type='TEXT'  notnull=1  dflt_value=None  pk=0
  cid=3  name='description'  type='TEXT'  notnull=0  dflt_value=None  pk=0
  cid=4  name='line_item_type'  type='TEXT'  notnull=1  dflt_value=None  pk=0
  cid=5  name='quantity'  type='INTEGER'  notnull=1  dflt_value=None  pk=0
  cid=6  name='unit_price'  type='REAL'  notnull=1  dflt_value=None  pk=0
  cid=7  name='customer_id'  type='REAL'  notnull=0  dflt_value=None  pk=0
  cid=8  name='country'  type='TEXT'  notnull=1  dflt_value=None  pk=0
  cid=9  name='invoice_timestamp'  type='TEXT'  notnull=1  dflt_value=None  pk=0
PRAGMA table_info(country_timezones);
  cid=0  name='country'  type='TEXT'  notnull=1  dflt_value=None  pk=1
  cid=1  name='timezone'  type='TEXT'  notnull=1  dflt_value=None  pk=0
```

`PRAGMA table_info` reports column structure only and does not surface CHECK
constraints, so the constraint is confirmed two ways: the `CREATE TABLE` text
stored in `sqlite_master` (which retains the column-level CHECK), and the
behavioral INSERT test in 6.4. Both tables match the spec exactly: column names,
declared types, nullability, and the `country` primary key are all as required.

### 6.4 CHECK-constraint verification (real INSERT test)

The database was deleted and rebuilt from the updated `db/schema.sql` (same
delete-and-recreate procedure as the original build), and then a manual INSERT
with an invalid `line_item_type` value (`'invalid_type'`) was attempted via the
Python `sqlite3` module. The insert was rejected — exact exception observed:

```text
sqlite3.IntegrityError: CHECK constraint failed: line_item_type IN ('product', 'fee', 'adjustment')
```

(SQLite embeds the failing expression in the column-level CHECK error message.)

As a positive control, one INSERT per valid value (`'product'`, `'fee'`,
`'adjustment'`) was then accepted (the test DB reached 3 rows before being
discarded).

**Final state:** the test database was deleted and rebuilt clean from
`db/schema.sql` only — zero rows in both tables, schema applied without error,
and the stored `transactions` DDL retains the CHECK constraint. (That was the
state of `data/processed/sentrasql.db` before the load-layer task in section 8
populated `transactions` with the full dataset.)


## Guardrail Validation Layer — `db/guardrails.py`

`validate_sql(sql: str) -> tuple[bool, str | None]` — first implementation, checks 1–3 of the full guardrail design (see DESIGN_LOG.md for the full planned rule set; further rules — table/column/function whitelisting, JOIN validation, SELECT * rejection, row limits — are separate, later tasks).

Current checks: rejects empty/no real statement (`no_statement`), rejects unparseable SQL (`unparseable_sql`), rejects more than one real statement (`multiple_statements`), rejects any non-SELECT statement type (`not_a_select`). Uses `sqlglot` with SQLite as the parse dialect.

Known, deliberate scope limitation: compound set operations (`UNION`, `INTERSECT`, `EXCEPT`) currently fall under `not_a_select` since they parse to a different root node type than a plain `SELECT`. This is intentional for now, not an oversight — revisit if a future query pattern genuinely needs compound queries.

Implementation detail worth preserving: `sqlglot.parse()` (not `parse_one()`) is used for statement counting, since `parse_one()` silently wraps stacked statements into a single block rather than exposing them as separate statements. Empty/whitespace/comment-only input and trailing-comment-after-semicolon cases were specifically tested to confirm they don't produce false rejections.

---


## Guardrail Validation Layer — Update (Rules 4–10 complete)

`db/guardrails.py`'s `validate_sql` now implements the full AST-structural rule set from the v2 guardrail design (DESIGN_LOG.md §4 architecture note, §12):

- **Rule 4 — Table whitelist:** every FROM/JOIN/subquery table reference checked against a required `schema: dict[str, set[str]]` parameter (no default — a missing schema is a loud error, not a silent permissive mode). CTE aliases are correctly excluded from this check (they're not real tables); tables referenced inside a CTE body are still validated.
- **Rule 5 — Column whitelist:** scope-aware column resolution (each column resolves against its nearest enclosing SELECT's visible tables). Qualified columns (`t.quantity`) resolve directly; unqualified columns resolve only if exactly one visible table has that column name — genuinely ambiguous unqualified columns in a multi-table JOIN (e.g. `country`, present in both tables) are rejected as `disallowed_column`, matching how SQLite itself would treat the ambiguity.
- **Rule 6 — Function whitelist:** only `COUNT, SUM, AVG, MIN, MAX, ROUND, STRFTIME, DATE, DATETIME, CAST, COALESCE` are permitted. Required real investigation of sqlglot's internal function-node representations (aggregates, scalar builtins, `CAST`, and sqlglot's own function-rewriting behavior for unsupported functions like `DATEDIFF`) to avoid both false rejections and false approvals.
- **Rule 7 — Wildcard rejection:** bare `SELECT *` and `table.*` are rejected (`wildcard_select`), including when hidden inside a subquery or CTE. `COUNT(*)` and similar aggregate usages are correctly unaffected, since `*` there is a function argument, not a projection wildcard.
- **Rule 8 — Unconditioned JOIN rejection:** any JOIN without a real ON/USING condition is rejected (`unconditioned_join`), covering explicit CROSS JOIN, implicit comma joins (indistinguishable from CROSS JOIN in the AST), bare JOIN with no condition, and NATURAL JOIN. **Deliberate scope limitation:** NATURAL JOIN is valid SQLite syntax with real join semantics, but is blanket-rejected here since it structurally lacks ON/USING — acceptable because our two-table schema has no current need for it, not because it's unsafe in principle.
- **Rule 10 — Row-limit enforcement:** `validate_sql`'s return contract is now `tuple[bool, str | None, str | None, bool]` — (passed, reason, enforced SQL, truncated flag). Two named provisional tiers exist (500 rows for LLM-context queries, 50,000 for a not-yet-built bulk/report consumer) per DESIGN_LOG.md §12 — both numbers remain provisional, not finalized. Missing or over-ceiling LIMIT clauses are clamped, never rejected; the `truncated` flag is intended to eventually feed Node 6.5's disclosure mechanism as a fifth disclosure source (not yet wired — Node 6.5 is still a stub).
- **Not yet implemented:** Rule 11 (opening the SQLite connection itself in read-only mode as a second, independent safety layer).

All rules verified via an extensive `__main__` test harness covering both required cases and additional adversarial cases discovered through investigation of sqlglot's actual behavior (not assumed) — several genuine edge-case bugs were found and fixed during this process (e.g., `LIMIT ALL` initially being misidentified as a disallowed column reference).


## Guardrail Layer — Complete (Rule 11: read-only connection)

`db/connect.py` — new module, `connect_readonly(db_path: str | Path) -> sqlite3.Connection`. Opens the database using SQLite's URI filename facility with `mode=ro`, which opens the underlying file handle without write permission at the OS level — a real enforcement layer, not a naming convention or a soft per-connection flag. `PRAGMA query_only=ON` was considered and rejected as a weaker alternative, since it only toggles a flag on an already-writable handle rather than removing write capability at the driver/OS level.

This is a deliberately separate, independent safety layer from the AST-based rules 1–10 in `db/guardrails.py` — if `sqlglot` ever mis-parses a SQLite-dialect-specific destructive statement and a bad query slips past validation, the read-only connection still blocks the actual write. Two independent layers that must both fail is a stronger guarantee than either alone.

Verified via `scripts/verify_readonly.py`: reads succeed normally on a read-only connection; INSERT and CREATE TABLE attempts are both rejected at the driver level (`OperationalError: attempt to write a readonly database`); and the database file is confirmed byte-for-byte unmodified after a rejected write attempt (row counts, file size, and SHA-256 hash all identical before and after).

**This completes the full v2 guardrail design (DESIGN_LOG.md §4 architecture note, §12) — all 11 rules are implemented, tested, and independently verified: parse validation, single-statement enforcement, SELECT-only enforcement, table whitelist, column whitelist, function whitelist, wildcard rejection, unconditioned-join rejection, row-limit enforcement (provisional numbers), and the read-only connection layer.**