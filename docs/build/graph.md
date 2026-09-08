<!-- APPEND-ONLY: add new dated entries at the bottom. Never edit or delete existing content above. -->

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


## compile_sql (Node 4) — Complete

`compile_sql` deterministically builds SQL from `state.query_intent` and `state.applicable_rules`, using sqlglot's AST-construction API (never string templates). Core guarantee: main and companion queries are built from one shared, atomically-constructed set of conditions — any condition reused across queries is `.copy()`'d before a second attachment, since sqlglot nodes are mutable and carry parent references.

Implements all four disclosure rules: `AVG_EXCLUDE_ZERO_PRICE`, `CUSTOMER_EXCLUDE_NULL`, and `PRODUCT_EXCLUDE_NONPRODUCT` each add a companion `COUNT(*)` query and AND-compose their negation onto the main query's WHERE clause; `NET_VS_GROSS` (via a new `QueryIntent.net_gross` field: `"net"`/`"gross_of_cancellations"`/`"returns"`) produces no companion query, selecting one of three mutually-exclusive named filter variants instead. All four rules compose correctly in any combination via AND, verified at the AST level, not just by string comparison.

Four early-validation gates run before any SQL construction: aggregation/metric compatibility, group-by field validity (checked against the live schema via `db.connect.connect_readonly`, not a hardcoded list), filter sanity (reversed date ranges, near-miss/no-match filter values — the latter a deliberate v1 hard-failure, no fuzzy correction), and cross-consistency between `applicable_rules` and `query_intent` (currently scoped narrowly to one genuine structural impossibility: non-default `net_gross` without `NET_VS_GROSS` present — rule 2/3/4 semantic applicability is deliberately left to the future `detect_applicable_rules` node, not enforced here).

**Known gap:** date-range filters pass validation but are not yet compiled into SQL (hits a pre-existing unsupported-literal error) — range-based filtering is not yet functional end-to-end.

69 tests total (`tests/test_compile_sql.py`), including live-database verification against independently-computed reference values for every rule and combination.


## detect_applicable_rules (Node 3) — Complete

`detect_applicable_rules` is fully deterministic (no LLM) — mechanically checks `state.query_intent`'s fields against four independent trigger conditions, populating `state.applicable_rules`. All four rules can fire in any combination; they are evaluated independently, not as a single dispatch decision.

- **`NET_VS_GROSS`** — fires when `aggregation == "sum"` on `revenue`/`quantity`, OR whenever `net_gross != "net"` (an explicit non-default request), regardless of aggregation/metric. The second condition exists specifically to satisfy `compile_sql`'s `rule_mismatch` gate.
- **`AVG_EXCLUDE_ZERO_PRICE`** — fires on `avg` over `unit_price` or `revenue` (DESIGN_LOG.md §13: revenue is price-derived, so zero-price rows distort its average the same way). Deliberately does NOT fire on `avg`/`quantity` — a zero-price row still has a real, non-zero quantity.
- **`CUSTOMER_EXCLUDE_NULL`** — fires when `group_by` includes `customer_id`.
- **`PRODUCT_EXCLUDE_NONPRODUCT`** — fires when `group_by` includes `stock_code`. Deliberately keys on grouping, not filtering: a query that filters to a specific `stock_code` (via `query_intent.filters`) without grouping by it does not fire this rule, since a single product code is a homogeneous population — the mixed product/non-product ambiguity this rule resolves doesn't arise there.

All four rules verified to compose correctly in every combination via integration tests against the live `compile_sql` node, including a full four-rule-simultaneous case. 102 tests total (`tests/test_detect_applicable_rules.py` + `tests/test_compile_sql.py`), including live-database verification.

**Also fixed in this work cycle (see `graph/state.py`):** `QueryIntent.metric` tightened from `str` to a `Literal` enum (`revenue`/`quantity`/`unit_price`/`customer_id`), and a new `distinct: bool` field was added to represent distinct-count queries (e.g. unique customer count) — both closing gaps found during this node's design review. See DESIGN_LOG.md §13.


## validate_guardrails (Node 5) — Complete

`validate_guardrails` bridges `compile_sql`'s output to the already-built, independently-tested `db/guardrails.py` module (11 rules) — pure plumbing, no new guardrail logic.

Gate: `state.error is not None` is the no-op passthrough condition (not `state.sql_main is None`) — `error` is the direct, authoritative upstream-failure signal, not coupled to `compile_sql`'s current implementation detail of always nulling `sql_main` on failure (confirmed via code inspection that the two currently coincide, but `error`-based gating is used regardless, since it states the actual intent).

On any validation failure (main query or any companion): sets `state.error`/`state.guardrail_status = "failed"` and stops immediately. Hard invariant, verified via object-identity assertions in tests, not just equality: on failure, `state.sql_companions` is left byte-for-byte identical to what `compile_sql` produced — no partial write-back, even for companions that individually passed validation before a later one failed. Companion loop also verified (via a call-counting mock) to genuinely stop at the first failure, not validate remaining companions wastefully.

On full success: `guardrail_status = "passed"`, `state.sql_main` and every companion's `sql` field are overwritten with their guardrail-enforced (row-limited) versions, and `state.main_truncated` / each `CompanionQuery.truncated` are set from their own independent `validate_sql` result — write-back is deferred until every statement in the batch has passed, so state transitions atomically from all-original to all-enforced, never a partial mix.

107 tests total across `tests/test_compile_sql.py`, `tests/test_detect_applicable_rules.py`, `tests/test_validate_guardrails.py`.

**Schema additions from this work cycle** (see DESIGN_LOG.md §14): `GraphState.main_truncated: bool` and `CompanionQuery.truncated: bool` — closing the open truncation-disclosure composability question from §12. Decision: main-query and companion-query truncation are independent, composable disclosure sources, same pattern as the four policy rules.


## QueryIntent.filters Restructuring — Complete

`QueryIntent.filters` was restructured from a loose dict into typed sub-objects (`Filters` containing `date_range: DateRangeFilter` and `country: CountryFilter`), each following an explicit `present`-flag convention rather than dict-key existence or sentinel values — required after live-verifying that DeepSeek's strict function-calling mode rejects nullable union types at the API level (see DESIGN_LOG.md §16). `DateRangeFilter` uses independent per-boundary flags (`start_present`/`end_present`), not one flag over the whole range, to correctly represent partial ranges like "since March."

This change propagated to both consumers designed against the old shape: `compile_sql`'s WHERE-clause construction and its early-validation gates (date-range ordering, no-matching-data) now check `.present`/`.*_present` flags explicitly instead of dict iteration; `detect_applicable_rules` required no changes, since its integration tests only invoke `compile_sql` rather than duplicating filter-access logic.

Full regression: 107/107 tests passing across all three test modules (`test_compile_sql.py`, `test_detect_applicable_rules.py`, `test_validate_guardrails.py`), confirming the restructuring introduced no unintended behavior change beyond the filter-shape migration itself.


## extract_query_intent (Node 2) — Complete

The first LLM-dependent node in the graph. Uses DeepSeek's function-calling strict mode via LangChain's `with_structured_output`, bound directly to `QueryIntent`, following extensive live verification of the API's actual behavior (see DESIGN_LOG.md §§16–17) — most importantly, that strict mode guarantees well-formed JSON with required keys present, but does NOT enforce value-level constraints (enum membership, type correctness). Pydantic validation of the parsed response is therefore the sole real enforcement layer, not a redundant backup.

**Architecture:** built from independently-tested modules, each with one responsibility — `graph/schema_prompt.py` (live-introspected database schema description), `graph/conventions_prompt.py` (empty-value and present-flag format rules, informed by measured compliance rates from live API probes), `graph/assumptions_prompt.py` (the two concrete, checklisted ambiguity triggers — relative time references and unspecified ranking size — explicitly excluding zero-match entity references per the §15 scope decision), `graph/system_prompt.py` (deterministic assembly, stable content first for prefix-cache efficiency, query strictly last — verified byte-identical prefix across different queries), `graph/llm.py` (the bound model, including a fix for a `langchain-deepseek` routing defect that silently sent requests to `/v1` instead of `/beta` despite reporting the correct endpoint), and `graph/intent_normalizer.py` (post-parse present/value consistency validation — both directions of inconsistency are hard failures, per the documented asymmetric-risk reasoning in DESIGN_LOG §16).

**Node logic:** error passthrough on pre-existing `state.error`; one retry on any failure (model exception, parse failure, or normalization rejection) with identical call configuration, visibly recorded via `state.intent_extraction_retried`; final failure sets a specific `intent_extraction_failed:<reason>` error rather than a generic one.

**Verified end-to-end against the real DeepSeek API** (not just mocked): a query requiring interpretive ambiguity resolution ("revenue in United Kingdom last month") correctly resolved the relative date reference to concrete ISO-8601 boundaries, populated a correctly-structured assumption disclosing that resolution, and extracted the country filter with consistent present/value pairing — all on the first attempt, no retry needed.

114 tests total (107 prior + 7 new offline tests for this node), plus a separate, deliberately-not-in-suite real-API smoke script (`scripts/smoke_extract_query_intent.py`) to keep routine test runs free of API cost.


## execute_queries (Node 6) — Complete

Runs `state.sql_main` and every `state.sql_companions` entry against the real database (one read-only connection per invocation, reused across all queries). Gate: `state.error is not None` OR `state.guardrail_status != "passed"` — passthrough, no-op. Main query executes first; companions run only after it succeeds, and — per explicit design decision — still run even when the main query returns zero rows (disclosures describe query behavior, not result size, so "zero exclusions on a zero-row query" remains a meaningful, correctly-computed disclosure).

Main query results accept any well-formed row shape (multiple rows valid, zero rows explicitly valid and non-error). Companion results are strictly validated: exactly one row, one numeric non-negative column — a count of `0` is a normal, valid result; the failure condition is zero rows returned or extra rows/columns, never the count value itself. On any companion failure (execution error or shape violation), remaining companions never execute — verified via a recording-connection wrapper proving genuine non-execution, not inferred from final state alone.

Write-back is atomic: main results and every companion's count/status accumulate in local variables and are only written to `state` after full success. On any failure, `state.main_results` and every `CompanionQuery`'s fields remain exactly as they arrived — including companions that individually succeeded before a later one failed — verified via the same object-identity assertion rigor established in `validate_guardrails`.

**Accepted, explicitly documented tradeoff:** a companion query failure discards the entire answer via the hard-stop policy, even though the main query executed successfully with real results — a real UX cost (a well-formed question can return a generic error because a side verification query broke), accepted as correct per this project's standing "disclosure integrity is non-negotiable, present-but-wrong is worse than absent" principle.

123 tests total across all node/module test files.


## assemble_disclosures (Node 6.5) — Complete

Deterministic (no LLM) normalization step converting all five disclosure sources into a unified `list[Disclosure]`, written to `state.disclosures`. Gate: `state.error is not None` OR `guardrail_status != "passed"` OR `main_results is None` — three-condition passthrough, stating the node's full actual precondition rather than the nearest single upstream signal.

**Hard-fail check runs before any disclosure is built:** for each of the three exclusion rules (`AVG_EXCLUDE_ZERO_PRICE`, `CUSTOMER_EXCLUDE_NULL`, `PRODUCT_EXCLUDE_NONPRODUCT`) present in `applicable_rules`, a corresponding `sql_companions` entry with `status == "success"` must exist — otherwise `state.error = "disclosure_assembly_inconsistency:<rule>"` and no disclosures are built at all. This defends against `detect_applicable_rules` and `compile_sql` (populated several steps apart) silently disagreeing — treated as evidence of an upstream bug, never silently worked around, per this project's standing "present-but-wrong is worse than absent" principle applied to the disclosure mechanism itself.

**Five disclosure sources, built in a fixed, deterministic order** (verified via shuffled input ordering in tests, not just fixed-order inputs): rule-based exclusions (including zero-count companions, which still produce a disclosure — the mechanism reports what was checked, not only what was found), the `NET_VS_GROSS` direct-filter statement, assumption-based disclosures, main-query truncation, and per-companion truncation.

**Schema addition from this work cycle** (DESIGN_LOG.md §18): `Disclosure.source` extended to a fourth literal, `"truncation"` — kept distinct from `"rule"` rather than overloaded, so consumers of `state.disclosures` can rely on `source` as a real category signal rather than needing to inspect `label` as an implicit proxy.

130 tests total across all node/module test files.