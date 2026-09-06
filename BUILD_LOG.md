# SentraSQL — BUILD_LOG

Technical record generated from the **actual, current state** of the codebase on disk.
This file is maintained by Cline as a standing responsibility: after every completed
task, the relevant section(s) below are updated to match what was actually built.
It is not a plan and not a restatement of prompts — it reflects real files, real
signatures, and real output.

---

## 1. Environment

| Item | Value |
| --- | --- |
| Python interpreter (read from `.venv`) | `Python 3.12.14` (`.venv\Scripts\python.exe --version`) |
| Virtual environment location | `.venv` (project root) |
| Environment tooling | **uv** (`uv 0.12.9`, installed at `C:\Users\DELL\.local\bin\uv.exe`) |
| Setup method | Environment created and managed **exclusively with uv** (`uv venv --python 3.12`, `uv pip install`, `uv pip freeze`) — **not** `pip`/`venv`. |
| Environment history | Originally created on CPython 3.14.5; `.venv` was later deleted and recreated pinned to CPython 3.12.14 via `uv venv --python 3.12`, then all dependencies reinstalled from `requirements.txt`. |

### `requirements.txt` (exact verbatim contents)

```text
annotated-types==0.8.0
anyio==4.15.0
certifi==2026.7.22
charset-normalizer==3.5.1
distro==1.9.0
et-xmlfile==2.0.0
h11==0.16.0
httpcore==1.0.9
httpcore2==2.12.0
httpx==0.28.1
httpx2==2.12.0
idna==3.19
jsonpatch==1.33
jsonpointer==3.1.1
langchain==1.3.18
langchain-core==1.6.1
langchain-protocol==0.0.19
langgraph==1.2.11
langgraph-checkpoint==4.2.0
langgraph-prebuilt==1.1.0
langgraph-sdk==0.4.4
langsmith==0.12.1
numpy==2.5.2
openpyxl==3.1.5
orjson==3.12.0
ormsgpack==1.12.2
packaging==26.3
pandas==3.0.5
pycountry==26.2.16
pydantic==2.13.5
pydantic-core==2.46.5
python-dateutil==2.9.0.post0
pytz==2026.3.post1
pyyaml==6.0.3
requests==2.34.2
requests-toolbelt==1.0.0
six==1.17.0
sniffio==1.3.1
tenacity==9.1.4
truststore==0.10.4
typing-extensions==4.16.0
typing-inspection==0.4.4
tzdata==2026.3
urllib3==2.7.0
uuid-utils==0.17.0
websockets==16.1.1
xxhash==4.0.1
zstandard==0.25.0
```

> Note: `pycountry==26.2.16` and `pytz==2026.3.post1` were added via
> `uv pip install` for the country-to-timezone layer (see section 9).

---

## 2. File Structure

Directory tree as it actually exists on disk, excluding `.venv`, `__pycache__`, and `.git`.

```text
.
├── .env                     # empty env file (gitignored; placeholder for real secrets)
├── .env.example             # DEEPSEEK_API_KEY=your_key_here
├── .gitignore               # .env, .venv/, __pycache__/, *.pyc, data/raw/* & data/processed/* (!their .gitkeep files)
├── BUILD_LOG.md             # this file
├── DESIGN_LOG.md            # project working log; present on disk, currently UNTRACKED
├── README.md                # "# SentraSQL" + tagline
├── requirements.txt         # pinned dependency list (see Environment section)
├── app/                     # empty placeholder folder (not git-tracked)
├── data/
│   ├── processed/           # processed/derived data (gitignored, except .gitkeep)
│   │   ├── .gitkeep
│   │   └── sentrasql.db     # SQLite DB — transactions populated with 1,067,354 rows; created from db/schema.sql (see sections 6 and 8)
│   └── raw/                 # raw dataset input folder
│       ├── .gitkeep
│       └── online_retail_II.csv   # ~94.8 MB raw dataset (~1,067,371 rows), gitignored
├── db/                      # database layer — DDL + pure transform + loader + timezone map
│   ├── __init__.py          # empty package marker (db is an importable package)
│   ├── load.py              # schema-ready DataFrame → transactions table (see section 8)
│   ├── schema.sql           # SQLite DDL: transactions + country_timezones (see section 6)
│   ├── timezones.py         # pycountry/pytz country → IANA timezone map (see section 9)
│   └── transform.py         # raw DataFrame → schema-ready DataFrame (see section 7)
├── graph/                   # LangGraph pipeline package
│   ├── build.py             # graph construction/wiring + module-level compiled graph
│   ├── nodes.py             # eight node stubs (no logic yet)
│   └── state.py             # Pydantic data models (state schema)
├── reports/                 # generated Markdown reports
│   └── data_profile.md      # output of scripts/profile_dataset.py
├── scripts/                 # reusable utility / profiling scripts
│   ├── profile_anomalies.py # console anomaly scan (Quantity/Price/Invoice/StockCode)
│   ├── profile_dataset.py   # dataset profiling → reports/data_profile.md
│   ├── profile_stockcodes.py# StockCode-structure anomaly scan (console)
│   ├── run_load.py          # load dataset into transactions + verification queries (see section 8)
│   ├── run_load_timezones.py# load pycountry/pytz country → timezone map (see section 9)
│   └── run_transform_check.py# smoke-test of db/transform.py → verification report (see section 7)
└── tests/                   # empty placeholder folder (not git-tracked)
```

Folder purposes:

| Folder | Purpose |
| --- | --- |
| `data/` | Holds **only data files**. `raw/` = unmodified source data; `processed/` = cleaned/derived data (currently holds the populated `sentrasql.db`). |
| `graph/` | Core application code for the LangGraph pipeline: state schema, node functions, and graph wiring. |
| `scripts/` | Reusable utility, data-profiling, and verification scripts (separate from application code); `run_transform_check.py` smoke-tests `db/transform.py`, and `run_load.py` / `run_load_timezones.py` load + verify the populated DB. |
| `app/` | Reserved for user-facing application code (currently empty). |
| `db/` | Database layer — `schema.sql` (SQLite DDL for `transactions` + `country_timezones`), `transform.py` (raw CSV → schema-ready DataFrame), `load.py` (schema-ready DataFrame → `transactions` rows), `timezones.py` (pycountry/pytz country → timezone map), and the package init marker. |
| `tests/` | Reserved for tests (currently empty). |
| `reports/` | Generated analysis output (currently holds the dataset profile). |

> Note: `app/` and `tests/` exist on disk but contain no files, so git does not
> track them (git ignores empty directories). `data/raw/*` and `data/processed/*`
> are gitignored with exceptions for their `.gitkeep` files — so the raw CSV and
> the derived `data/processed/sentrasql.db` are present on disk but not tracked.

---

## 3. Data Models — `graph/state.py`

File imports: `from __future__ import annotations`, `from enum import Enum`,
`from typing import Literal`, `from pydantic import BaseModel, Field`.
Module docstring: *"Typed data models for the analytics-query graph state."* —
it defines Pydantic shapes only (no functions, no node logic, no graph imports).

### 3.1 `RuleName(str, Enum)`

Class docstring (verbatim):

```text
Name of a business/analytics rule that may apply to a query.

Because it subclasses ``str``, each member serializes to its own name
string, which keeps rule identity stable across JSON boundaries.

Members:
    NET_VS_GROSS: Query amounts may be net or gross; the rule resolves which.
    AVG_EXCLUDE_ZERO_PRICE: Averages should ignore rows whose price is zero.
    CUSTOMER_EXCLUDE_NULL: Rows with no customer should be excluded from results.
    PRODUCT_EXCLUDE_NONPRODUCT: Non-product stock codes (postage, fees, etc.)
        should be excluded from product-level answers.
```

Members:

| Member | Value (str) |
| --- | --- |
| `NET_VS_GROSS` | `"NET_VS_GROSS"` |
| `AVG_EXCLUDE_ZERO_PRICE` | `"AVG_EXCLUDE_ZERO_PRICE"` |
| `CUSTOMER_EXCLUDE_NULL` | `"CUSTOMER_EXCLUDE_NULL"` |
| `PRODUCT_EXCLUDE_NONPRODUCT` | `"PRODUCT_EXCLUDE_NONPRODUCT"` |

### 3.2 `Assumption(BaseModel)`

Class docstring (verbatim):

```text
A single assumption recorded while interpreting an ambiguous query.

Attributes:
    field: Which part of the query was ambiguous (e.g. "metric", "group_by",
        "filters").
    raw_phrase: What the user actually said that needed interpretation.
    resolution: What that phrase was resolved to for execution.
```

Fields:

| Field | Type | Default |
| --- | --- | --- |
| `field` | `str` | — (required) |
| `raw_phrase` | `str` | — (required) |
| `resolution` | `str` | — (required) |

### 3.3 `QueryIntent(BaseModel)`

Class docstring (verbatim):

```text
Structured, validated interpretation of the user's analytics question.

Attributes:
    aggregation: Aggregate function to apply. Restricted to one of
        "sum", "avg", "count", "min", "max".
    metric: The business metric being aggregated (e.g. "quantity", "price").
    group_by: Optional columns to group results by. ``None`` (the default)
        means the query is not grouped.
    filters: Canonical filter constraints to apply, mapping filter/column
        names to their values. Empty by default (no filters).
    output_format: How the answer should be presented. Restricted to
        "chat" or "report"; defaults to "chat".
    assumptions: Assumptions made while parsing the query. The default
        empty list is a normal, expected, and fully valid result: it simply
        means the query needed no assumptions. An empty list here is NOT a
        missing value and should never be treated as an error.
```

Fields:

| Field | Type | Default |
| --- | --- | --- |
| `aggregation` | `Literal["sum", "avg", "count", "min", "max"]` | — (required) |
| `metric` | `str` | — (required) |
| `group_by` | `list[str] \| None` | `None` |
| `filters` | `dict` | `Field(default_factory=dict)` → `{}` |
| `output_format` | `Literal["chat", "report"]` | `"chat"` |
| `assumptions` | `list[Assumption]` | `Field(default_factory=list)` → `[]` |


### 3.4 `CompanionQuery(BaseModel)`

Class docstring (verbatim):

```text
A supplementary SQL query generated to satisfy one applicable rule.

Attributes:
    rule: The rule this companion query was generated for.
    sql: The companion SQL statement itself.
    status: Execution lifecycle state. Restricted to "pending", "success",
        or "failed"; defaults to "pending".
    excluded_count: Number of rows the companion query excluded. Trustworthy
        only when ``status`` is "success" -- before the query runs this is
        ``None``, and after a failure it must not be used as a real count.
```

Fields:

| Field | Type | Default |
| --- | --- | --- |
| `rule` | `RuleName` | — (required) |
| `sql` | `str` | — (required) |
| `status` | `Literal["pending", "success", "failed"]` | `"pending"` |
| `excluded_count` | `int \| None` | `None` |

### 3.5 `Disclosure(BaseModel)`

Class docstring (verbatim):

```text
A statement surfaced to the user about how the answer was produced.

Attributes:
    source: What generated this disclosure. Restricted to exactly three
        values: "rule" (an applicable rule fired), "assumption" (the query
        required an assumption), or "direct_filter" (the user's filters
        were applied directly).
    label: Short human-readable headline for the disclosure.
    detail: Longer explanation, e.g. the raw phrase and its resolution.
```

Fields:

| Field | Type | Default |
| --- | --- | --- |
| `source` | `Literal["rule", "assumption", "direct_filter"]` | — (required) |
| `label` | `str` | — (required) |
| `detail` | `str` | — (required) |

### 3.6 `GraphState(BaseModel)`

Class docstring (verbatim):

```text
Full working state passed between graph steps for one user query.

Attributes:
    raw_query: The user's original question, verbatim.
    detected_language: Language detected for the query; defaults to "en".
    normalized_query: Normalized version of the query (spelling, canonical
        terms); empty string by default when no normalization was needed.
    query_intent: Parsed intent of the query, or ``None`` until parsing
        completes (or if parsing fails).
    applicable_rules: Ordered list of rules that should be applied to this
        query. Empty by default.
    sql_main: The primary SQL statement answering the query; ``None`` until
        it has been generated.
    sql_companions: Companion SQL queries keyed by the rule they serve.
        Empty by default when no companion queries are needed.
    guardrail_status: Whether the plan passed safety checks. Restricted to
        "pending", "passed", or "failed"; defaults to "pending".
    main_results: Rows returned by the main query as a list of records;
        ``None`` until execution produces them.
    error: Error message if a step failed; ``None`` when all is well.
    disclosures: Explanations (rule firings, assumptions, direct filters)
        to surface to the user. Empty by default.
    final_answer: The final user-facing answer text; ``None`` until the
        answer has been assembled.
```

Fields:

| Field | Type | Default |
| --- | --- | --- |
| `raw_query` | `str` | — (required) |
| `detected_language` | `str` | `"en"` |
| `normalized_query` | `str` | `""` |
| `query_intent` | `QueryIntent \| None` | `None` |
| `applicable_rules` | `list[RuleName]` | `Field(default_factory=list)` → `[]` |
| `sql_main` | `str \| None` | `None` |
| `sql_companions` | `dict[RuleName, CompanionQuery]` | `Field(default_factory=dict)` → `{}` |
| `guardrail_status` | `Literal["pending", "passed", "failed"]` | `"pending"` |
| `main_results` | `list[dict] \| None` | `None` |
| `error` | `str \| None` | `None` |
| `disclosures` | `list[Disclosure]` | `Field(default_factory=list)` → `[]` |
| `final_answer` | `str \| None` | `None` |


---

## 4. Node Functions — `graph/nodes.py`

Module docstring (verbatim): *"Empty node stubs for the core pipeline (Nodes 2-7 plus 6.5)."*
Import: `from graph.state import GraphState`.
**Every function is currently a stub**: its body is only a docstring,
`# TODO: implement`, and `return state` (the state is returned unchanged — no real logic).

### 4.1 `def extract_query_intent(state: GraphState) -> GraphState:`

Docstring (verbatim):

```text
Node 2. LLM call. Takes state.normalized_query and the live DB schema, produces a QueryIntent (aggregation, metric, group_by, filters, output_format, assumptions) and stores it in state.query_intent. Does not generate SQL. Populates assumptions only when genuine interpretive ambiguity was resolved; an empty list is a normal, valid result and must not be treated as a field that always needs content.
```

### 4.2 `def detect_applicable_rules(state: GraphState) -> GraphState:`

Docstring (verbatim):

```text
Node 3. Deterministic, no LLM. Reads state.query_intent and mechanically checks it against the four policy rules (net/gross/returns disclosure, average-excludes-zero-price, customer-grouping-excludes-null, product-ranking-excludes-nonproduct). Populates state.applicable_rules. Contains no interpretive judgment — pure structural checks against query_intent's fields.
```

### 4.3 `def compile_sql(state: GraphState) -> GraphState:`

Docstring (verbatim):

```text
Node 4. Deterministic, no LLM. Builds state.sql_main and state.sql_companions from state.query_intent and state.applicable_rules, using shared filter parameters so main and companion queries cannot structurally drift apart. Rule NET_VS_GROSS produces no companion query, per policy — its disclosure is sourced directly from query_intent's resolved filter, not from a CompanionQuery.
```

### 4.4 `def validate_guardrails(state: GraphState) -> GraphState:`

Docstring (verbatim):

```text
Node 5. Deterministic, AST-based. Parses state.sql_main and every entry in state.sql_companions before execution. Enforces SELECT-only, whitelisted tables/columns, no destructive keywords, bounded result size. Sets state.guardrail_status to 'passed' or 'failed'. On failure, must set state.error and the graph must route to an error path — never to execution.
```

### 4.5 `def execute_queries(state: GraphState) -> GraphState:`

Docstring (verbatim):

```text
Node 6. Runs state.sql_main and every state.sql_companions entry against the database. Populates state.main_results and updates each CompanionQuery's status and excluded_count. If any companion query required by state.applicable_rules fails, this is a hard stop: state.error must be set and the graph must not proceed to disclosure assembly or answer assembly with partial results.
```

### 4.6 `def assemble_disclosures(state: GraphState) -> GraphState:`

Docstring (verbatim):

```text
Node 6.5. Deterministic, no LLM. Runs only when state.error is None. Converts all three disclosure sources into a unified list of Disclosure objects in state.disclosures: (1) each entry in state.query_intent.assumptions, (2) each successful CompanionQuery result for rules other than NET_VS_GROSS, (3) a direct-filter-based disclosure for rule NET_VS_GROSS sourced from query_intent's resolved filter, not from a companion query.
```

### 4.7 `def assemble_answer(state: GraphState) -> GraphState:`

Docstring (verbatim):

```text
Node 7. LLM call, constrained. Takes state.main_results and state.disclosures as structured input and composes state.final_answer. Only responsible for phrasing — every number or exclusion count in the output must originate from state.disclosures or state.main_results, never invented in prose.
```

### 4.8 `def handle_error(state: GraphState) -> GraphState:`

Docstring (verbatim):

```text
Error path. Deterministic, no LLM. Runs when state.error is set (guardrail validation failure or companion query execution failure). Composes a user-facing message from state.error into state.final_answer, explicitly stating that the system could not produce a reliable answer rather than guessing or returning a partial result. Must never be bypassed when state.error is set.
```


---

## 5. Graph Wiring — `graph/build.py`

Module docstring (verbatim):

```text
Wire the skeleton LangGraph pipeline (Nodes 2-7 + 6.5 + error path).

The node functions are still stubs that return state unchanged; this module
only defines the graph topology: a linear sequence with two conditional
branches that route to ``handle_error`` on guardrail failure or execution
error, then always terminate at ``END``.
```

Imports:

```python
from langgraph.graph import END, StateGraph
from graph.nodes import (assemble_answer, assemble_disclosures, compile_sql,
                         detect_applicable_rules, execute_queries,
                         extract_query_intent, handle_error, validate_guardrails)
from graph.state import GraphState
```

### Functions defined in `build.py`

| Function | Exact signature | Exact docstring (verbatim) |
| --- | --- | --- |
| `_route_after_guardrails` | `def _route_after_guardrails(state: GraphState) -> str:` | `Return the branch after guardrail validation.` |
| `_route_after_execution` | `def _route_after_execution(state: GraphState) -> str:` | `Return the branch after query execution.` |
| `build_graph` | `def build_graph():` | `Construct, wire, and compile the SentraSQL state graph.` |

Module-level variable (created at import time): **`sentrasql_graph = build_graph()`** —
an already-compiled LangGraph state graph.

### Actual wiring as coded

Nodes are added under the same names as the node functions:
`extract_query_intent`, `detect_applicable_rules`, `compile_sql`,
`validate_guardrails`, `execute_queries`, `assemble_disclosures`,
`assemble_answer`, `handle_error`.

- Entry point: `graph.set_entry_point("extract_query_intent")`.
- Static edges (`graph.add_edge`):
  - `extract_query_intent` → `detect_applicable_rules`
  - `detect_applicable_rules` → `compile_sql`
  - `compile_sql` → `validate_guardrails`
  - `assemble_disclosures` → `assemble_answer`
  - `assemble_answer` → `END`
  - `handle_error` → `END`
- Conditional edge from `validate_guardrails`
  (`graph.add_conditional_edges("validate_guardrails", _route_after_guardrails, ...)`):
  - `_route_after_guardrails` returns `"error"` if `state.guardrail_status == "failed"`, else `"execute"`.
  - Mapping: `{"error": "handle_error", "execute": "execute_queries"}`.
- Conditional edge from `execute_queries`
  (`graph.add_conditional_edges("execute_queries", _route_after_execution, ...)`):
  - `_route_after_execution` returns `"error"` if `state.error is not None`, else `"continue"`.
  - Mapping: `{"error": "handle_error", "continue": "assemble_disclosures"}`.
- The graph is compiled with `graph.compile()` and returned by `build_graph()`.

So `handle_error` is reachable from two sources (failed guardrails, or an
execution error) and always routes to `END`; the happy path runs
`extract_query_intent → ... → assemble_answer → END`.

### `__main__` behavior (actual code)

```python
if __name__ == "__main__":
    initial_state = GraphState(raw_query="test query")
    result = sentrasql_graph.invoke(initial_state)
    print(f"final_answer: {result.get('final_answer')}")
    print("full state:")
    print(result)
```

Last observed output (run with the `.venv` Python 3.12.14 interpreter via
`python -m graph.build` from the project root):

```text
final_answer: None
full state:
{'raw_query': 'test query', 'detected_language': 'en', 'normalized_query': '', 'applicable_rules': [], 'sql_companions': {}, 'guardrail_status': 'pending', 'disclosures': []}
```

> Runtime quirk (documented from actual behavior): invoking the file directly as
> `python graph/build.py` raises `ModuleNotFoundError: No module named 'graph'`
> because executing a script puts the script's own directory (`graph/`) on
> `sys.path` rather than the project root. Running it as a module
> (`python -m graph.build`) from the project root works. This is why the
> `graph` package needs to be importable from the project root.

---

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

## 10. Commit History

Full `git log --oneline` output (most recent first):

```text
431b1e3 Add library-based country-to-timezone mapping (pycountry/pytz) and populate country_timezones table.
966c445 Add data loading script and populate transactions table.
107b518 Add data transformation logic (raw CSV to schema-ready DataFrame).
7b83881 Add CHECK constraint enforcing line_item_type domain.
25dde1d Add transactions and country_timezones table schemas.
cde7786 Add BUILD_LOG.md — technical record generated from actual codebase state, maintained by Cline going forward.
2211a35 Wire skeleton LangGraph pipeline with conditional error routing.
d5fd531 Add error-handling node stub.
5634208 Add empty node function stubs for core LangGraph pipeline (Nodes 2-7 + 6.5).
7c41fb9 Add data investigation scripts (anomaly and stockcode profiling).
6737a27 Add graph state schema (Pydantic models).
0943d8e Set up uv environment and add dataset profiling script.
5b96b8f Initial project scaffold
```

