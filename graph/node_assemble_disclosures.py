"""Node 6.5 - ``assemble_disclosures`` (one node per module).

Deterministic, no LLM: converts every disclosure source into one
ordered ``Disclosure`` list in ``state.disclosures`` with a hard-fail
companion cross-consistency check (DESIGN_LOG.md section 19).

This module is the private home of the disclosure-only constants and
helpers ``assemble_disclosures`` alone depends on (the fixed exclusion
rule order, the net/gross variant label map, and the excluded-count
sentence helper)."""

from __future__ import annotations

from graph.state import Disclosure, GraphState, RuleName


# Fixed order in which the three exclusion rules are always processed by
# ``assemble_disclosures`` -- both for the hard-fail consistency check and for
# building rule disclosures -- independent of the order rules appear in
# ``state.applicable_rules`` or companions appear in ``state.sql_companions``,
# so the node's output is deterministic.
_EXCLUSION_RULE_DISCLOSURE_ORDER = (
    RuleName.AVG_EXCLUDE_ZERO_PRICE,
    RuleName.CUSTOMER_EXCLUDE_NULL,
    RuleName.PRODUCT_EXCLUDE_NONPRODUCT,
)


# Rule NET_VS_GROSS (rule 1) disclosure wording: ``query_intent.net_gross``
# carries the machine value; the disclosure sentence uses the human label.
_NET_GROSS_VARIANT_LABELS = {
    "net": "net",
    "gross_of_cancellations": "gross of cancellations",
    "returns": "returns",
}


def _excluded_count_sentence(excluded_count: int, rule_name: str) -> str:
    """Return the sentence stating how many rows one exclusion rule's companion
    counted."""
    if excluded_count == 1:
        return f"1 row was excluded from the result set under rule {rule_name}."
    return (
        f"{excluded_count} rows were excluded from the result set under "
        f"rule {rule_name}."
    )


def assemble_disclosures(state: GraphState) -> GraphState:
    """Node 6.5. Deterministic, no LLM. Converts every disclosure source into
    one ordered ``Disclosure`` list stored in ``state.disclosures``: fired
    exclusion rules (source ``"rule"``, count from their successful companion),
    rule 1's resolved net/gross/returns variant (source ``"direct_filter"``),
    query assumptions (source ``"assumption"``), and main/companion truncation
    (source ``"truncation"``). Output order is fixed and independent of the
    ordering of ``applicable_rules`` and ``sql_companions``.

    Gate: a state with ``state.error`` set, a plan that never passed guardrail
    validation (``guardrail_status != "passed"``), or a main query that never
    produced ``state.main_results`` is returned completely untouched.

    Hard-fail (DESIGN_LOG.md section 19), checked before any disclosure is
    built: every exclusion rule present in ``state.applicable_rules`` must have
    a corresponding ``state.sql_companions`` entry whose status is
    ``"success"``. A rule whose companion is missing entirely, or whose
    companion did not succeed, sets ``state.error`` to
    ``disclosure_assembly_inconsistency:<rule_name>`` and returns immediately --
    no disclosure is built and ``state.disclosures`` is never partially
    populated.
    """

    # Passthrough gate: no error, a plan that passed guardrail validation, and
    # executed main results are all required before there is anything
    # trustworthy to disclose.
    if (
        state.error is not None
        or state.guardrail_status != "passed"
        or state.main_results is None
    ):
        return state

    # Hard-fail check, before any disclosure is built. ``applicable_rules`` and
    # ``sql_companions`` are populated by different nodes several steps apart,
    # so their agreement is verified here, never assumed: a fired exclusion
    # rule whose companion is missing or did not run to success means no
    # trustworthy exclusion count exists to disclose.
    for rule in _EXCLUSION_RULE_DISCLOSURE_ORDER:
        if rule in state.applicable_rules:
            companion = state.sql_companions.get(rule)
            if companion is None or companion.status != "success":
                state.error = f"disclosure_assembly_inconsistency:{rule.value}"
                return state

    disclosures: list[Disclosure] = []

    # 1. One rule disclosure per fired exclusion rule, in fixed rule order. A
    # companion that succeeded with excluded_count == 0 still yields a
    # disclosure ("0 rows were excluded ...") -- the mechanism discloses what
    # was checked, not only what was found excluded.
    for rule in _EXCLUSION_RULE_DISCLOSURE_ORDER:
        if rule not in state.applicable_rules:
            continue
        companion = state.sql_companions[rule]
        disclosures.append(
            Disclosure(
                source="rule",
                label=rule.value,
                detail=_excluded_count_sentence(
                    companion.excluded_count, rule.value
                ),
            )
        )

    # 2. Rule NET_VS_GROSS (rule 1) is direct-filter-based: it produces no
    # companion query at all, so its disclosure states which of the three
    # net/gross/returns variants ``query_intent`` resolved the amounts to.
    if RuleName.NET_VS_GROSS in state.applicable_rules:
        intent = state.query_intent
        variant = _NET_GROSS_VARIANT_LABELS.get(
            intent.net_gross, intent.net_gross
        )
        disclosures.append(
            Disclosure(
                source="direct_filter",
                label="net_vs_gross",
                detail=f"The amounts were reported on a {variant} basis.",
            )
        )

    # 3. One assumption disclosure per structured entry, in intent order.
    for assumption in state.query_intent.assumptions:
        disclosures.append(
            Disclosure(
                source="assumption",
                label=assumption.field,
                detail=(
                    f'The phrase "{assumption.raw_phrase}" was interpreted '
                    f"as: {assumption.resolution}."
                ),
            )
        )

    # 4. Main-query truncation, when the guardrail's row-limit enforcement cut
    # the main result set short.
    if state.main_truncated:
        disclosures.append(
            Disclosure(
                source="truncation",
                label="main_query",
                detail=(
                    "The main result set was limited by the guardrail's "
                    "row-limit enforcement."
                ),
            )
        )

    # 5. One truncation disclosure per companion whose own result set was cut
    # short, in sorted rule order so the output never depends on dict
    # insertion order.
    for rule in sorted(state.sql_companions, key=lambda item: item.value):
        companion = state.sql_companions[rule]
        if companion.truncated:
            disclosures.append(
                Disclosure(
                    source="truncation",
                    label=rule.value,
                    detail=(
                        f"The companion result set for rule {rule.value} was "
                        "limited by the guardrail's row-limit enforcement."
                    ),
                )
            )

    state.disclosures = disclosures
    return state
