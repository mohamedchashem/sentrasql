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

