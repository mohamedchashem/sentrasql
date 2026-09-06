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

---

