"""Post-parse normalization and consistency validation for ``QueryIntent``.

This module performs the additional checks DESIGN_LOG.md section 16 requires
*beyond* structural Pydantic validation, on an intent freshly returned by the
LLM call. It deliberately lives in its own small module rather than in
``graph/llm.py`` (whose declared scope is only the bound model object and which
explicitly defers "the post-parse normalization step" as a separate task) and
rather than in ``graph/nodes.py`` (pipeline nodes). Its single responsibility:
turn a structurally-valid ``QueryIntent`` into one that is also *internally
consistent* per the project's conventions, or report a machine-readable hard
failure. It is NOT wired into ``extract_query_intent`` yet -- that is a
separate follow-up task.

Contract: ``normalize_and_validate_intent(intent)`` returns
``(normalized_intent, None)`` on success and ``(original_intent, reason)`` on
a hard failure, where ``reason`` follows the project's existing
``<category>:<detail>`` convention (as used by ``invalid_intent:...`` in
``graph.node_compile_sql`` and the guardrail reason codes). The input object is never
mutated: a normalized intent is a copy, and a failed intent is returned
untouched.

Check 1 -- present/value consistency, per DESIGN_LOG.md section 16.
   Each optional scalar filter is a ``*_present`` flag paired with a value
   field whose type default is the empty string. Section 16 records the two
   inconsistent states as a distinct failure mode and explicitly decides that
   BOTH directions hard-fail despite their asymmetric risk -- ``present:
   false`` with a populated value is the dangerous direction (silently
   normalizing it away could drop a real extraction), ``present: true`` with
   an empty/default value is comparatively low-stakes, but neither is silently
   normalized here. Both directions are therefore checked independently for
   each of the three pairs (``filters.country``, ``filters.date_range.start``,
   ``filters.date_range.end``):

   * flag True  + value equal to its type default (``""``)  ->
     ``intent_inconsistency:present_true_empty_value:<field>``
   * flag False + value non-empty (anything other than ``""``) ->
     ``intent_inconsistency:present_false_populated_value:<field>``

   "Empty" means exactly the field's type default, the empty string -- the
   definition section 16 uses when it says the value field "stays at its type
   default". No whitespace-collapsing is applied: this module only checks
   consistency, it never launders a value.

Check 2 -- remaining null risk for list-typed fields (verified against
   ``graph/state.py``, not assumed).
   ``QueryIntent.assumptions`` is typed ``list[Assumption]`` (default ``[]``)
   -- non-nullable, so a ``null`` never survives structural Pydantic
   validation and no coercion is needed there. ``QueryIntent.group_by``,
   however, is typed ``list[str] | None = None`` -- genuinely nullable AND
   optional, so an explicit ``null`` or a simple omission of the key both land
   here as ``group_by=None`` even though the prompt conventions (section 16's
   list-field rule) require ``[]`` and never ``null``/omission. This is the
   one remaining null/empty inconsistency risk, and it is handled here by
   normalizing ``None`` -> ``[]``: an empty list is the canonical "no
   grouping" encoding, and every downstream consumer (``compile_sql``'s gates,
   ``detect_applicable_rules``) already treats an empty list and ``None``
   identically as "not grouped". No other field can be ``None`` after
   structural validation (every remaining field is non-nullable or a
   ``Literal``/``bool`` whose invalid values Pydantic rejects), so this is the
   only normalization this layer performs.
"""

from __future__ import annotations

from graph.state import QueryIntent

# Machine-readable reason codes (following the project's <category>:<detail>
# convention). The category is ``intent_inconsistency``; the detail names the
# failed direction and, after a second colon, the exact field -- matching how
# the guardrail reasons carry their offending value (e.g.
# ``disallowed_function:RANDOM``) and how the pairs are named in DESIGN_LOG
# section 16. Kept as module constants so the graph's error-phrasing layer can
# match on them without string literals.
_REASON_CATEGORY = "intent_inconsistency"
_PRESENT_TRUE_EMPTY_VALUE = "present_true_empty_value"
_PRESENT_FALSE_POPULATED_VALUE = "present_false_populated_value"

_COUNTRY_FIELD = "filters.country"
_DATE_RANGE_START_FIELD = "filters.date_range.start"
_DATE_RANGE_END_FIELD = "filters.date_range.end"


def _inconsistency_reason(direction: str, field: str) -> str:
    """Compose ``intent_inconsistency:<direction>:<field>``."""
    return f"{_REASON_CATEGORY}:{direction}:{field}"


def _check_flag_value_pair(
    present: bool, value: str, field: str
) -> str | None:
    """Return the inconsistency reason for one flag/value pair, or ``None``.

    Both directions hard-fail per DESIGN_LOG.md section 16 -- never silently
    normalized. ``present=True`` requires a non-default value; ``present=False``
    requires the value to stay at its type default (the empty string).
    """
    if present and value == "":
        return _inconsistency_reason(_PRESENT_TRUE_EMPTY_VALUE, field)
    if not present and value != "":
        return _inconsistency_reason(_PRESENT_FALSE_POPULATED_VALUE, field)
    return None


def normalize_and_validate_intent(
    intent: QueryIntent,
) -> tuple[QueryIntent, str | None]:
    """Validate present/value consistency and normalize a parsed intent.

    Args:
        intent: A freshly-parsed ``QueryIntent``, already Pydantic-validated at
            the structural level (e.g. returned by the LLM call).

    Returns:
        ``(normalized_intent, None)`` on success -- ``normalized_intent`` is
        the input unchanged when nothing needed normalization, or a copy with
        ``group_by`` coerced from ``None`` to ``[]`` (the one remaining
        null-risk field; see the module docstring). ``(original_intent,
        reason)`` on a hard failure -- the original object, untouched, with a
        machine-readable ``intent_inconsistency:...`` reason.
    """
    filters = intent.filters

    # Check 1: present/value consistency, independently for each of the three
    # flag/value pairs (country, date_range.start, date_range.end). The first
    # failing pair wins; the input object is returned unchanged.
    for present, value, field in (
        (filters.country.present, filters.country.value, _COUNTRY_FIELD),
        (
            filters.date_range.start_present,
            filters.date_range.start,
            _DATE_RANGE_START_FIELD,
        ),
        (
            filters.date_range.end_present,
            filters.date_range.end,
            _DATE_RANGE_END_FIELD,
        ),
    ):
        reason = _check_flag_value_pair(present, value, field)
        if reason is not None:
            return intent, reason

    # Check 2: the one remaining null risk. group_by is typed
    # ``list[str] | None`` (verified in graph/state.py), so an explicit null
    # or an omitted optional key reaches this layer as None despite the
    # prompt's "[] never null, never omitted" convention. Normalize None -> []
    # (the canonical "not grouped" encoding). Every other list-typed field
    # (assumptions) is non-nullable and cannot reach here as null.
    if intent.group_by is None:
        return intent.model_copy(update={"group_by": []}), None

    return intent, None


if __name__ == "__main__":
    # Minimal wiring probe: a consistent intent passes, an inconsistent one
    # hard-fails with its reason.
    from graph.state import CountryFilter, DateRangeFilter, Filters

    consistent = QueryIntent(
        aggregation="sum",
        metric="revenue",
        filters=Filters(
            date_range=DateRangeFilter(),
            country=CountryFilter(),
        ),
    )
    normalized, reason = normalize_and_validate_intent(consistent)
    print(f"consistent -> reason={reason!r}, unchanged={normalized is consistent}")

    inconsistent = QueryIntent(
        aggregation="sum",
        metric="revenue",
        filters=Filters(
            date_range=DateRangeFilter(),
            country=CountryFilter(present=True, value=""),
        ),
    )
    _, reason = normalize_and_validate_intent(inconsistent)
    print(f"country present=True value='' -> reason={reason!r}")

