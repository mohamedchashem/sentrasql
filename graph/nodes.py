"""Empty node stubs for the core pipeline (Nodes 2-7 plus 6.5).

Each stub documents the future responsibility of its node. There is no real
logic yet -- every function returns ``state`` unchanged.
"""

from __future__ import annotations

from graph.state import GraphState


def extract_query_intent(state: GraphState) -> GraphState:
    """Node 2. LLM call. Takes state.normalized_query and the live DB schema, produces a QueryIntent (aggregation, metric, group_by, filters, output_format, assumptions) and stores it in state.query_intent. Does not generate SQL. Populates assumptions only when genuine interpretive ambiguity was resolved; an empty list is a normal, valid result and must not be treated as a field that always needs content."""

    # TODO: implement
    return state


def detect_applicable_rules(state: GraphState) -> GraphState:
    """Node 3. Deterministic, no LLM. Reads state.query_intent and mechanically checks it against the four policy rules (net/gross/returns disclosure, average-excludes-zero-price, customer-grouping-excludes-null, product-ranking-excludes-nonproduct). Populates state.applicable_rules. Contains no interpretive judgment — pure structural checks against query_intent's fields."""

    # TODO: implement
    return state


def compile_sql(state: GraphState) -> GraphState:
    """Node 4. Deterministic, no LLM. Builds state.sql_main and state.sql_companions from state.query_intent and state.applicable_rules, using shared filter parameters so main and companion queries cannot structurally drift apart. Rule NET_VS_GROSS produces no companion query, per policy — its disclosure is sourced directly from query_intent's resolved filter, not from a CompanionQuery."""

    # TODO: implement
    return state


def validate_guardrails(state: GraphState) -> GraphState:
    """Node 5. Deterministic, AST-based. Parses state.sql_main and every entry in state.sql_companions before execution. Enforces SELECT-only, whitelisted tables/columns, no destructive keywords, bounded result size. Sets state.guardrail_status to 'passed' or 'failed'. On failure, must set state.error and the graph must route to an error path — never to execution."""

    # TODO: implement
    return state


def execute_queries(state: GraphState) -> GraphState:
    """Node 6. Runs state.sql_main and every state.sql_companions entry against the database. Populates state.main_results and updates each CompanionQuery's status and excluded_count. If any companion query required by state.applicable_rules fails, this is a hard stop: state.error must be set and the graph must not proceed to disclosure assembly or answer assembly with partial results."""

    # TODO: implement
    return state


def assemble_disclosures(state: GraphState) -> GraphState:
    """Node 6.5. Deterministic, no LLM. Runs only when state.error is None. Converts all three disclosure sources into a unified list of Disclosure objects in state.disclosures: (1) each entry in state.query_intent.assumptions, (2) each successful CompanionQuery result for rules other than NET_VS_GROSS, (3) a direct-filter-based disclosure for rule NET_VS_GROSS sourced from query_intent's resolved filter, not from a companion query."""

    # TODO: implement
    return state


def assemble_answer(state: GraphState) -> GraphState:
    """Node 7. LLM call, constrained. Takes state.main_results and state.disclosures as structured input and composes state.final_answer. Only responsible for phrasing — every number or exclusion count in the output must originate from state.disclosures or state.main_results, never invented in prose."""

    # TODO: implement
    return state
