"""Deterministic system-prompt block: empty-value and present-flag conventions.

This module owns the second stable segment of the eventual full system prompt
for ``extract_query_intent`` (Node 2): the instructions that govern how the
structured output's *list fields* and *_present/flag-value pairs must be
populated. It is deliberately a sibling of ``graph/schema_prompt.py`` rather
than a second function in that module: that module describes *database
content* (tables/columns/closed value sets), while this content describes
*output-format rules* for the ``QueryIntent`` object itself. The two stay
logically separated even though both are part of the same eventual stable
prefix.

Block ordering and the cache-efficiency note (DESIGN_LOG.md section 16): once
the full system prompt exists it is assembled as [stable blocks] followed by
[the user's query], so the identical prefix can be served from a provider-side
prompt cache. Like the schema block, this block is therefore query-independent
and deterministic (a pure function of no per-request state, byte-identical
across calls). The schema block remains the first block; this conventions block
is written to follow it, and both precede any query-specific content.

Content provenance (every instruction below traces to a live-measured finding
in DESIGN_LOG.md section 16, not to assumed model behavior):

* Empty-list rule for the list-type fields ``group_by`` and ``assumptions``.
  Section 16's Finding 3 measured that the model fabricates content to avoid an
  empty ``assumptions`` list unless the prompt explicitly states that an empty
  list is a normal, valid result -- and that a prominent statement of that rule
  moved compliance to a clean 12/12 (and the finding records a bare 1/6 -> 6/6
  style swing for the same class of instruction). The rule must therefore be
  stated directly and prominently (never buried in a longer paragraph), name
  the exact fields, and forbid ``null``, omission, and invented filler alike.

* Present-flag consistency rule for ``Filters.country`` (``present``/``value``)
  and ``Filters.date_range`` (``start_present``/``start``,
  ``end_present``/``end``). Section 16 records the two inconsistent states --
  ``*_present`` false with a populated value, and ``*_present`` true with an
  empty/default value -- as a distinct failure mode requiring its own explicit
  instruction covering *both* directions; an empty value must never be treated
  as a "not provided" sentinel, because the ``*_present`` flag is the source of
  truth. Both directions are stated explicitly below, for every flag/value
  pair in the schema.

The module deliberately contains no other system-prompt content; the
assumptions ambiguity-trigger checklist lives in the sibling module
``graph/assumptions_prompt.py``.
"""

from __future__ import annotations


def build_output_conventions_block() -> str:
    """Build the stable output-conventions block for the system prompt.

    Returns a single deterministic string: the empty-value / present-flag
    instruction block, to be placed after the schema block and before any
    query-specific content in the eventual full system prompt. The text is a
    pure function of nothing -- no database access, no per-request state -- so
    repeated calls return byte-identical output.
    """
    lines = [
        "OUTPUT FORMAT CONVENTIONS",
        "=========================",
        "The rules below govern how you populate your structured reply. Each "
        "one exists because its absence produced a real, measured failure; "
        "treat them as load-bearing requirements, not style guidance.",
        "",
        "1. EMPTY-LIST RULE -- group_by AND assumptions",
        "   When a list-type field has nothing to include, it MUST be an empty "
        "list []. An empty list is a normal, valid, expected result -- never "
        "null, never omitted, and never a cue to invent content.",
        "   - group_by: when the question requests no grouping, emit "
        "group_by = [].",
        "   - assumptions: when the query needed no interpretive assumptions, "
        "emit assumptions = [].",
        "   Do not fabricate entries to make a list look non-empty, and do not "
        "treat an empty list as a failure to populate. An empty "
        "assumptions list is the correct, common outcome, not a value to "
        "avoid or fill.",
        "",
        "2. PRESENT-FLAG CONSISTENCY RULE -- Filters.country AND "
        "Filters.date_range",
        "   A *_present flag and its paired value field are one unit and must "
        "agree. The flag is the source of truth; an empty-string value does "
        "NOT mean \"not provided\", so neither side may be inferred from the "
        "other. Enforce both directions explicitly:",
        "   - *_present = false: leave the paired value field at its type "
        "default (the empty string \"\"). Do NOT populate it with any value.",
        "   - *_present = true: fill the paired value field with the real "
        "value. Do NOT leave it as the empty string \"\".",
        "   This applies to every flag/value pair you emit:",
        "     - Filters.country.present with Filters.country.value",
        "     - Filters.date_range.start_present with Filters.date_range.start",
        "     - Filters.date_range.end_present with Filters.date_range.end",
        "   Concretely: no country filter -> country.present = false and "
        "country.value = \"\"; a country filter -> country.present = true and "
        "country.value holds the exact country name from the schema block. No "
        "start boundary -> start_present = false and start = \"\"; a start "
        "boundary -> start_present = true and start holds the ISO-8601 "
        "boundary. The same applies to end_present with end.",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(build_output_conventions_block())
