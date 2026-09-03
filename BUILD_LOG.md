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
pydantic==2.13.5
pydantic-core==2.46.5
python-dateutil==2.9.0.post0
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
│   │   └── sentrasql.db     # SQLite DB — schema only (no rows yet); created from db/schema.sql (see section 6)
│   └── raw/                 # raw dataset input folder
│       ├── .gitkeep
│       └── online_retail_II.csv   # ~94.8 MB raw dataset (~1,067,371 rows), gitignored
├── db/                      # database layer — DDL + package init
│   ├── __init__.py          # empty package marker (db is an importable package)
│   └── schema.sql           # SQLite DDL: transactions + country_timezones (see section 6)
├── graph/                   # LangGraph pipeline package
│   ├── build.py             # graph construction/wiring + module-level compiled graph
│   ├── nodes.py             # eight node stubs (no logic yet)
│   └── state.py             # Pydantic data models (state schema)
├── reports/                 # generated Markdown reports
│   └── data_profile.md      # output of scripts/profile_dataset.py
├── scripts/                 # reusable utility / profiling scripts
│   ├── profile_anomalies.py # console anomaly scan (Quantity/Price/Invoice/StockCode)
│   ├── profile_dataset.py   # dataset profiling → reports/data_profile.md
│   └── profile_stockcodes.py# StockCode-structure anomaly scan (console)
└── tests/                   # empty placeholder folder (not git-tracked)
```

Folder purposes:

| Folder | Purpose |
| --- | --- |
| `data/` | Holds **only data files**. `raw/` = unmodified source data; `processed/` = cleaned/derived data (currently holds the schema-only `sentrasql.db`). |
| `graph/` | Core application code for the LangGraph pipeline: state schema, node functions, and graph wiring. |
| `scripts/` | Reusable utility and data-profiling scripts (separate from application code). |
| `app/` | Reserved for user-facing application code (currently empty). |
| `db/` | Database layer — `schema.sql` (SQLite DDL for `transactions` + `country_timezones`) and the package init marker. |
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
without error.

Decisions recorded here:

- **No indexes yet** — deliberately deferred until query patterns are known.
- **`country_timezones` is empty by design in this task** — schema only. It backs
  the timezone-handling requirement from `DESIGN_LOG.md` section 1.6 (a country →
  IANA-timezone mapping the agent reasons about at query time).
- **Value-domain invariants documented, not CHECK-constrained** — the "one of
  `product`/`fee`/`adjustment`" rule for `line_item_type`, the whitespace-trimmed
  / case-normalized contract for `stock_code`, and the ISO-8601 string contract
  for `invoice_timestamp` are preprocessing contracts enforced by the load layer
  (the DB is loaded only from already-normalized rows), so they are written as
  comments in the DDL rather than as SQL constraints.
- **`description` and `customer_id` are nullable** (they have `NULL` values in the
  source dataset: 4,382 missing descriptions and 243,007 missing customer IDs);
  everything else is `NOT NULL`.

### 6.1 Table `transactions` (DDL as written)

| Column | Declared type | Nullable | Notes |
| --- | --- | --- | --- |
| `invoice_id` | `TEXT` | no | Invoice number; `"C"` prefix marks a cancelled invoice |
| `is_cancelled_invoice` | `BOOLEAN` | no | `1` when `invoice_id` carries the cancellation flag, else `0` |
| `stock_code` | `TEXT` | no | Whitespace-trimmed, case-normalized (uppercase) |
| `description` | `TEXT` | yes | `NULL` when the source value is missing |
| `line_item_type` | `TEXT` | no | One of `product` / `fee` / `adjustment` |
| `quantity` | `INTEGER` | no | Signed; negative = return |
| `unit_price` | `REAL` | no | `0` is valid (free samples / promotional items) |
| `customer_id` | `REAL` | yes | `NULL` for guest / unknown customers |
| `country` | `TEXT` | no | Matches the dataset's `Country` column values |
| `invoice_timestamp` | `TEXT` | no | ISO-8601 datetime string, e.g. `2009-12-01 07:45:00` |

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

Both tables match the spec exactly: column names, declared types, nullability,
and the `country` primary key are all as required.

---

## 7. Commit History

Full `git log --oneline` output (most recent first):

```text
cde7786 Add BUILD_LOG.md — technical record generated from actual codebase state, maintained by Cline going forward.
2211a35 Wire skeleton LangGraph pipeline with conditional error routing.
d5fd531 Add error-handling node stub.
5634208 Add empty node function stubs for core LangGraph pipeline (Nodes 2-7 + 6.5).
7c41fb9 Add data investigation scripts (anomaly and stockcode profiling).
6737a27 Add graph state schema (Pydantic models).
0943d8e Set up uv environment and add dataset profiling script.
5b96b8f Initial project scaffold
```

