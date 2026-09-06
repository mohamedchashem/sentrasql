<!-- APPEND-ONLY: add new dated entries at the bottom. Never edit or delete existing content above. -->

## 7. Data Transformation Layer — `db/transform.py` + `scripts/run_transform_check.py`

The load-layer preprocessing contracts documented in section 6 (stock_code
whitespace-trimmed/case-normalized, line_item_type in the DDL CHECK domain,
invoice_timestamp as an ISO-8601 string) are now implemented in
`db/transform.py`. The module is intentionally pure and DB-free: a DataFrame in
the raw CSV shape produced by `scripts/profile_dataset.py`'s `load_dataset`
goes in and a schema-ready DataFrame comes out — no database connection, no SQL,
no file I/O. `db/__init__.py` makes it importable as `db.transform`.

### 7.1 Public API

| Symbol | Exact signature / value | Notes |
| --- | --- | --- |
| `transform_raw_data` | `def transform_raw_data(raw_df: pd.DataFrame) -> pd.DataFrame:` | Never mutates its input (starts from `raw_df.copy()`). |
| `FEE_STOCK_CODES` | `frozenset({"POST", "DOT", "C2", "BANK CHARGES", "AMAZONFEE", "CRUK"})` | Hardcoded fee list (DESIGN_LOG.md §2.3). |
| `ADJUSTMENT_STOCK_CODES` | `frozenset({"M", "D", "S", "ADJUST", "ADJUST2", "B"})` | Hardcoded adjustment list (DESIGN_LOG.md §2.3). |
| `GIFT_CODE_PREFIX` | `"GIFT_0001"` | Any code starting with this prefix (case-insensitive, tested after uppercasing) → `adjustment`. |

### 7.2 Pipeline (exact order as coded)

1. Drop rows whose whitespace-trimmed, uppercased `StockCode` equals
   `TEST001` / `TEST002`. The raw CSV contains **17 such rows** (15× `TEST001`,
   2× `TEST002`, each on its own invoice) — DESIGN_LOG.md's "the two
   TEST001/TEST002 rows" means the two distinct *codes*, not the row count; all
   17 rows are dropped.
2. Whitespace-trim and uppercase-normalize `StockCode` for every remaining row.
3. Add `line_item_type`: a fee code → `fee`; an adjustment code or any
   `GIFT_0001*` prefix → `adjustment`; every other row → `product`. Values are
   therefore always inside the `transactions.line_item_type` CHECK domain.
4. Add `is_cancelled_invoice` = `True` when `Invoice` (as a string) starts with
   `"C"`, else `False`.
5. Convert `InvoiceDate` to a real pandas datetime
   (`pd.to_datetime` → `datetime64[ns]`) and then to an ISO-8601 string
   `%Y-%m-%dT%H:%M:%S` (e.g. `2009-12-01T07:45:00`) stored as `invoice_timestamp`.
6. Rename raw columns to the `transactions` schema names and return exactly:
   `invoice_id`, `is_cancelled_invoice`, `stock_code`, `description`,
   `line_item_type`, `quantity`, `unit_price`, `customer_id`, `country`,
   `invoice_timestamp`.

`description` and `customer_id` nulls are intentionally preserved (those schema
columns are nullable; the raw dataset has 4,381 / 243,006 nulls respectively in
the transformed output).

### 7.3 `scripts/run_transform_check.py`

Console smoke test. It reuses `profile_dataset.find_dataset_file` and
`profile_dataset.load_dataset` (no duplicated loading logic — same file
detection + UTF-8/latin1 fallback as the profiling scripts), runs the full raw
file through `transform_raw_data`, and prints the verification report below.
Because it is run directly as `python scripts/run_transform_check.py`, the
script prepends both the project root and its own directory to `sys.path` so
the `db` package and the sibling profile modules both resolve.

Actual output (run with the `.venv` Python 3.12.14 interpreter from the project
root via `python scripts/run_transform_check.py`):

```text
Dataset file: online_retail_II.csv
Raw shape: 1,067,371 rows x 8 columns

=== Resulting shape ===
1,067,354 rows x 10 columns

=== line_item_type value counts ===
  product         1,061,460
  fee                 4,011
  adjustment          1,883

=== TEST001/TEST002 removal check ===
  Rows matching TEST001/TEST002 in raw data: 17
  Rows matching TEST001/TEST002 after transform: 0
  Confirmed: no TEST001/TEST002 rows remain -> True

=== First 5 transformed rows ===
invoice_id  is_cancelled_invoice stock_code                         description line_item_type  quantity  unit_price  customer_id        country   invoice_timestamp
    489434                 False      85048 15CM CHRISTMAS GLASS BALL 20 LIGHTS        product        12        6.95      13085.0 United Kingdom 2009-12-01T07:45:00
    489434                 False     79323P                  PINK CHERRY LIGHTS        product        12        6.75      13085.0 United Kingdom 2009-12-01T07:45:00
    489434                 False     79323W                 WHITE CHERRY LIGHTS        product        12        6.75      13085.0 United Kingdom 2009-12-01T07:45:00
    489434                 False      22041        RECORD FRAME 7" SINGLE SIZE         product        48        2.10      13085.0 United Kingdom 2009-12-01T07:45:00
    489434                 False      21232      STRAWBERRY CERAMIC TRINKET BOX        product        24        1.25      13085.0 United Kingdom 2009-12-01T07:45:00
```

(The `INFO: Read CSV 'online_retail_II.csv' using encoding: utf-8` line is
emitted on stderr by `profile_dataset.load_dataset`'s logger; stdout/stderr are
merged here as captured from the console.)

---

## 8. Data Loading Layer — `db/load.py` + `scripts/run_load.py`

`db/load.py` is the persistence counterpart to `db/transform.py` (section 7):
a DataFrame in the exact schema-ready shape returned by `transform_raw_data`
is inserted into the `transactions` table of the SQLite database at
`data/processed/sentrasql.db`. `db/__init__.py` makes it importable as
`db.load`.

### 8.1 Public API

| Symbol | Exact signature / value | Notes |
| --- | --- | --- |
| `load_transactions` | `def load_transactions(db_path: str \| Path, df: pd.DataFrame) -> None:` | Inserts every row of `df` into `transactions` in one transaction. |
| `TRANSACTION_COLUMNS` | `("invoice_id", "is_cancelled_invoice", "stock_code", "description", "line_item_type", "quantity", "unit_price", "customer_id", "country", "invoice_timestamp")` | Exact schema-ready column set/order (mirrors `db/transform.py` output and `db/schema.sql`). |

### 8.2 Load guarantees (as coded)

1. **Parameterized SQL only.** One `INSERT INTO transactions (...) VALUES (?, ?, ...)` statement is prepared once and executed with `executemany`; every value is bound through `?` placeholders — values are never string-formatted into SQL.
2. **Single transaction / all-or-nothing.** The connection context manager (`with conn:`) commits only when every insert succeeded; any exception rolls the whole batch back, so either all 1,067,354 rows persist or none do.
3. **Errors propagate.** `load_transactions` catches nothing around the insert; a `sqlite3.IntegrityError` (e.g. CHECK / NOT NULL violation) or any other error surfaces to the caller after rollback.
4. **Native Python parameter values.** Columns are re-selected in `TRANSACTION_COLUMNS` order, missing values (`NaN` / `NaT` / `pd.NA`) are replaced with `None`, and `Series.tolist()` normalizes pandas' `numpy` scalars to plain Python `bool`/`int`/`float`/`str`. This matters because CPython's `sqlite3` binds `numpy.bool_` / `numpy.int64` scalars as BLOBs rather than as SQLite integers.
5. **Shape guard.** A `ValueError` is raised up front if any of the ten required columns is missing from the DataFrame.

Behavior was verified on a temporary test database before the real load: a
valid 5,000-row subset inserted cleanly, and an insert containing one invalid
`line_item_type` value raised `sqlite3.IntegrityError` (`CHECK constraint
failed: line_item_type IN ('product', 'fee', 'adjustment')`) and rolled back to
zero rows, confirming the all-or-nothing transaction.

### 8.3 `scripts/run_load.py`

End-to-end loader script. It reuses `profile_dataset.find_dataset_file` and
`profile_dataset.load_dataset` (no duplicated loading logic — same file
detection + UTF-8/latin1 fallback as every other script), runs the full raw
file through `transform_raw_data`, calls `db.load.load_transactions` against
`data/processed/sentrasql.db` (or the database path given as the first CLI
argument), and then runs + prints three verification queries against the
now-populated database:

1. `SELECT COUNT(*) FROM transactions` — total row count;
2. `SELECT line_item_type, COUNT(*) FROM transactions GROUP BY line_item_type`;
3. `SELECT * FROM transactions LIMIT 1` — one full sample row.

Run protocol followed before touching the real database: the loader was first
run against a throwaway copy of the (then empty) schema-only database
(`data/processed/sentrasql_dryrun.db`); that dry run loaded all 1,067,354 rows
and passed all three verifications cleanly. Only then was it run for real
against `data/processed/sentrasql.db`, and the throwaway copy was deleted.

### 8.4 Real-run verification output (verbatim)

Run with the `.venv` Python 3.12.14 interpreter from the project root via
`python scripts/run_load.py`. The `INFO: Read CSV ... using encoding: utf-8`
line is emitted on stderr by `profile_dataset.load_dataset` (not shown here);
everything below is the unmodified stdout of the real run:

```text
Dataset file: online_retail_II.csv
Transformed rows: 1,067,354 rows x 10 columns
Target database: C:\Users\DELL\Desktop\NLP Task 1 SentraSQL\data\processed\sentrasql.db
Loading transactions (single transaction) ...
Load complete: 1,067,354 rows inserted.

=== Verification 1: total row count in transactions ===
SQL: SELECT COUNT(*) FROM transactions
  1,067,354

=== Verification 2: line_item_type value counts ===
SQL: SELECT line_item_type, COUNT(*) FROM transactions GROUP BY line_item_type
  adjustment          1,883
  fee                 4,011
  product         1,061,460

=== Verification 3: one full sample row ===
SQL: SELECT * FROM transactions LIMIT 1
  invoice_id = 489434
  is_cancelled_invoice = 0
  stock_code = 85048
  description = 15CM CHRISTMAS GLASS BALL 20 LIGHTS
  line_item_type = product
  quantity = 12
  unit_price = 6.95
  customer_id = 13085.0
  country = United Kingdom
  invoice_timestamp = 2009-12-01T07:45:00
```

The verification counts match the transformation layer's expectations exactly
(product 1,061,460 / fee 4,011 / adjustment 1,883 — see section 7.3), and the
`is_cancelled_invoice = 0` integer confirms `BOOLEAN` values were stored as
SQLite integers, not BLOBs.

---

## 9. Country-to-Timezone Mapping Layer — `db/timezones.py` + `scripts/run_load_timezones.py`

The country-to-timezone layer populates the ``country_timezones`` table that
section 6 left empty. The 43 mappings are derived from the real ISO/timezone
libraries :mod:`pycountry` and :mod:`pytz` (installed into the project venv
with uv and added to ``requirements.txt`` — see section 1) plus one small
hardcoded override list for three multi-zone countries where pytz's zone.tab
first entry is not the expected primary zone (see section 9.1).
``db/timezones.py`` resolves each dataset country name to an ISO 3166 country
and derives its IANA timezone from pytz or that override list;
``scripts/run_load_timezones.py`` persists the result.

### 9.1 Public API

| Symbol | Exact signature / value | Notes |
| --- | --- | --- |
| `build_country_timezone_map` | `def build_country_timezone_map(country_names: list[str]) -> dict[str, str]` | Maps every input name to an IANA timezone string (or `UTC`); honors `_COUNTRY_TIMEZONE_OVERRIDES` for Australia/Canada/Brazil, logs each resolved mapping (`override` vs `pytz default`) with `logger.debug`, and logs each UTC fallback with `logger.warning`. |

Resolution is per-name and runs in this order:

1. **Exact pycountry match** — case-insensitive comparison against `name`,
   `common_name`, `official_name`, `alpha_2` and `alpha_3`. Resolves 36 of the
   43 names, including `USA` (via its `alpha_3` code) and `Czech Republic`
   (via `official_name`).
2. **Dataset alias** — `_DATASET_COUNTRY_ALIASES = {"EIRE": "IE", "Korea":
   "KR"}`. These two labels are real countries this dataset writes
   non-standardly and that pycountry cannot resolve correctly: `EIRE`
   (Ireland) is unknown to pycountry — its fuzzy search raises `LookupError` —
   and pycountry's fuzzy search resolves `Korea` to `KP` (North Korea) rather
   than the dataset's intended `KR` (South Korea). Both behaviors were verified
   empirically against pycountry 26.2.16 before coding.
3. **pycountry fuzzy search** — `pycountry.countries.search_fuzzy`, which
   resolves `RSA` to South Africa (`ZA`).
4. **UTC fallback** — anything still unresolved maps to `UTC` and is logged.
   For this dataset that is exactly the four ambiguous/non-country values
   `Channel Islands`, `European Community`, `Unspecified` and `West Indies`.

The timezone choice for a resolved country runs in this order:

1. **Explicit override (checked before pytz).** A module-level dict,
   ``_COUNTRY_TIMEZONE_OVERRIDES``, maps the dataset country name directly to
   an IANA zone once that name has resolved to a real country. It contains
   exactly:

   ```python
   _COUNTRY_TIMEZONE_OVERRIDES = {
       "Australia": "Australia/Sydney",
       "Canada": "America/Toronto",
       "Brazil": "America/Sao_Paulo",
   }
   ```

   **Why it exists:** the original "first entry of
   ``pytz.country_timezones``" contract assumed that list is ordered by
   population/business relevance, but pytz actually follows IANA zone.tab
   ordering, which is not population-based (a wrong assumption found during
   implementation — recorded in DESIGN_LOG.md §11). For those three multi-zone
   countries zone.tab's first entry is an outlier region —
   `Australia -> Australia/Lord_Howe` (Lord Howe Island),
   `Canada -> America/St_Johns` (St. John's, Newfoundland) and
   `Brazil -> America/Noronha` (Fernando de Noronha) — not the country's
   primary business zone (Sydney, Toronto, São Paulo). The override makes those
   three map to the expected zone and leaves every other resolved country on
   the pytz path.
2. **pytz first entry (default).** Every resolved country not in the override
   map uses the first entry of ``pytz.country_timezones[alpha_2]`` (pytz's IANA
   zone.tab ordering), per the original load-layer contract. For single-zone
   countries this is the obvious zone (e.g. `Japan -> Asia/Tokyo`,
   `United Kingdom -> Europe/London`); for multi-zone countries that are not
   overridden, zone.tab's first entry is kept (e.g. `USA -> America/New_York`
   already matches the expected primary zone).
3. **UTC fallback (unchanged).** Names that never resolve to a country map to
   `UTC` and are logged at `logger.warning` (resolution step 4 above).

Each resolved country is logged at `logger.debug` in the same "Mapped ..."
style as before, now tagged with the branch that produced it: override
mappings log `Mapped 'Australia' -> 'Australia/Sydney' (override, exact,
alpha_2=AU).` and pytz-default mappings log `Mapped 'USA' ->
'America/New_York' (pytz default, exact, alpha_2=US).` The UTC-fallback
`logger.warning` lines are unchanged.

### 9.2 `scripts/run_load_timezones.py`

Pipeline (see the module docstring for full detail): it derives the 43 actual
country names from the **raw dataset** rather than from
`reports/data_profile.md`, because that report is a generated artifact that can
drift from the raw file; re-deriving from the raw `Country` column reads the
source of truth and reuses `profile_dataset.find_dataset_file` /
`profile_dataset.load_dataset` (no duplicated loading logic). The derived name
count is validated to be exactly 43. The script then calls
`build_country_timezone_map`, prints a resolved-vs-fallback resolution report,
explicitly verifies that every value to be bound is a plain Python `str`
(mirroring the numpy-scalar handling that `db/load.py` needed for
transactions), and inserts the mapping into `country_timezones` using the same
pattern as `db.load`: one parameterized `executemany` inside a single
`with conn:` transaction — all rows commit together, and any insert error
propagates to the caller after rollback (nothing is caught or swallowed).

**Numpy-scalar check outcome:** 86 values were checked (43 country names + 43
timezone names); **0** were non-plain-`str`, so the coercion was a no-op. The
check was *not* strictly necessary for this data: `build_country_timezone_map`
returns a plain Python dict whose keys/values are Python `str` literals or
pytz strings, so the numpy-scalar problem was avoided by construction rather
than by the coercion. The explicit check/coercion is kept anyway so that a
future refactor that routes the values through a DataFrame or numpy
intermediate cannot silently regress the bound types (CPython's `sqlite3`
binds e.g. `numpy.bool_` / `numpy.int64` as BLOBs, and `numpy.str_` values
would be equally wrong for TEXT columns).

As with the transactions load (section 8.3), the script was first run against a
throwaway copy of the database; after that dry run passed cleanly it was run
for real against `data/processed/sentrasql.db`, and the throwaway copy was
deleted.

### 9.3 Real-run output (verbatim)

Run with the `.venv` Python 3.12.14 interpreter from the project root via
`python scripts/run_load_timezones.py`. Because the script INSERTs the rows and
`country_timezones.country` is the PRIMARY KEY, the previously loaded 43 rows
were removed first with `DELETE FROM country_timezones` (truncate-and-reinsert:
43 → 0 → 43) so this is a clean rerun against the real database
`data/processed/sentrasql.db`. The `INFO: Read CSV ... using encoding: utf-8`
line and the four `WARNING: Country ...` fallback lines are emitted on stderr
by the loggers; everything below is the unmodified stdout of the real run:

```text
Dataset file: online_retail_II.csv
Country names derived from raw dataset (Country column): 43

=== Country-name resolution report ===
Resolved to a country + IANA timezone (39):
   1. Australia
   2. Austria
   3. Bahrain
   4. Belgium
   5. Bermuda
   6. Brazil
   7. Canada
   8. Cyprus
   9. Czech Republic
  10. Denmark
  11. EIRE
  12. Finland
  13. France
  14. Germany
  15. Greece
  16. Hong Kong
  17. Iceland
  18. Israel
  19. Italy
  20. Japan
  21. Korea
  22. Lebanon
  23. Lithuania
  24. Malta
  25. Netherlands
  26. Nigeria
  27. Norway
  28. Poland
  29. Portugal
  30. RSA
  31. Saudi Arabia
  32. Singapore
  33. Spain
  34. Sweden
  35. Switzerland
  36. Thailand
  37. USA
  38. United Arab Emirates
  39. United Kingdom
Fell back to UTC (4):
   1. Channel Islands
   2. European Community
   3. Unspecified
   4. West Indies

=== Numpy-scalar check on values to bind ===
  values checked: 86
  non-plain-str values found (coerced): 0
  all values plain str after coercion: True

Target database: C:\Users\DELL\Desktop\NLP Task 1 SentraSQL\data\processed\sentrasql.db
Inserting country_timezones rows (single transaction) ...
Insert complete: 43 rows inserted.

=== Verification 1: row count in country_timezones ===
SQL: SELECT COUNT(*) FROM country_timezones
  43

=== Verification 2: full contents (ORDER BY country) ===
SQL: SELECT * FROM country_timezones ORDER BY country
  Australia = Australia/Sydney
  Austria = Europe/Vienna
  Bahrain = Asia/Bahrain
  Belgium = Europe/Brussels
  Bermuda = Atlantic/Bermuda
  Brazil = America/Sao_Paulo
  Canada = America/Toronto
  Channel Islands = UTC
  Cyprus = Asia/Nicosia
  Czech Republic = Europe/Prague
  Denmark = Europe/Copenhagen
  EIRE = Europe/Dublin
  European Community = UTC
  Finland = Europe/Helsinki
  France = Europe/Paris
  Germany = Europe/Berlin
  Greece = Europe/Athens
  Hong Kong = Asia/Hong_Kong
  Iceland = Atlantic/Reykjavik
  Israel = Asia/Jerusalem
  Italy = Europe/Rome
  Japan = Asia/Tokyo
  Korea = Asia/Seoul
  Lebanon = Asia/Beirut
  Lithuania = Europe/Vilnius
  Malta = Europe/Malta
  Netherlands = Europe/Amsterdam
  Nigeria = Africa/Lagos
  Norway = Europe/Oslo
  Poland = Europe/Warsaw
  Portugal = Europe/Lisbon
  RSA = Africa/Johannesburg
  Saudi Arabia = Asia/Riyadh
  Singapore = Asia/Singapore
  Spain = Europe/Madrid
  Sweden = Europe/Stockholm
  Switzerland = Europe/Zurich
  Thailand = Asia/Bangkok
  USA = America/New_York
  United Arab Emirates = Asia/Dubai
  United Kingdom = Europe/London
  Unspecified = UTC
  West Indies = UTC
```

The resolution report confirms the fallback list is exactly the four expected
non-country values (`Channel Islands`, `European Community`, `Unspecified`,
`West Indies`) and no more, and Verification 1 confirms all 43 rows landed.
The four UTC fallback names are exactly the dataset rows whose `Country` value
is not a real country.

Relative to the pre-override run of this same script, **exactly three rows
changed**, all produced by `_COUNTRY_TIMEZONE_OVERRIDES`:
`Australia = Australia/Lord_Howe` → `Australia/Sydney`,
`Canada = America/St_Johns` → `America/Toronto` and
`Brazil = America/Noronha` → `America/Sao_Paulo`. The other 40 rows are
byte-for-byte identical to the earlier output above, so nothing else in the
mapping moved.

---

