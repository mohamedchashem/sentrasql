"""Deterministic system-prompt block: the assumptions ambiguity-trigger checklist.

This module owns the third stable segment of the eventual full system prompt
for ``extract_query_intent`` (Node 2): the checklist telling the model exactly
when it MUST populate an ``assumptions`` entry. It is a sibling of
``graph/schema_prompt.py`` (database content) and ``graph/conventions_prompt.py``
(empty-value/present-flag output rules): the schema block describes what exists
in the database, the conventions block describes how the output object is
shaped, and this block describes the *one situation* -- genuine interpretive
ambiguity -- that justifies adding content to the ``assumptions`` list. The
three stay logically separated even though all are part of the same eventual
stable prefix (assembled as [schema block] + [conventions block] + [this
checklist] before any query-specific content, per DESIGN_LOG.md section 16's
cache-efficiency note). Like its siblings, this block is query-independent and
deterministic -- a pure function of no per-request state, byte-identical
across calls.

Content provenance (every trigger traces to a decided project behavior, not to
assumed model behavior):

* Trigger 1 -- relative time references resolved to a concrete date range.
  DESIGN_LOG.md section 5.2 records exactly this as the motivating example for
  creating the ``assumptions`` field at all ("resolving 'last month' to a
  concrete date range" is an interpretive ambiguity only the LLM node can ever
  notice, because it is not an enumerable fixed rule). Section 5.4 further
  fixed the ``Assumption`` shape as the structured ``{field, raw_phrase,
  resolution}`` triple -- so the example phrasing below populates exactly that
  triple, never a free-text sentence.

* Trigger 2 -- ranking ("top N") without an explicit size. The project's
  earlier design decision fixes an implicit top-N to the default size 5; when
  that default is used the assumption must record it, so the user can see that
  "top products" meant exactly five rows and not some other silent choice.

* Scope boundary -- zero-match entity/product references are DELIBERATELY NOT a
  trigger here. Per DESIGN_LOG.md section 15 (Option B), a product/entity
  reference that maps to no exact known value in the schema is a HARD-FAILURE
  case, routed through the same validation-gap path as ``compile_sql``'s
  ``no_matching_data`` gate -- the system refuses and asks the user to be more
  specific. It is NOT a disclose-and-proceed case, so including a trigger that
  told the model to populate an assumption for it would actively contradict the
  decided behavior. The omission is deliberate, not forgotten. (Multi-match
  disambiguation -- e.g. several catalog products matching "the mug" -- is
  likewise out of scope per the same section.) The model-facing block therefore
  contains no instruction resembling "record an assumption for an unresolvable
  entity reference"; instead its closing line states that ONLY the two listed
  triggers ever populate ``assumptions``, which is what keeps the model from
  manufacturing an assumption entry for an entity it could not resolve.
"""

from __future__ import annotations


def build_assumptions_checklist_block() -> str:
    """Build the stable assumptions ambiguity-trigger checklist block.

    Returns a single deterministic string: the trigger checklist for the
    ``assumptions`` list, to be placed after the schema and output-conventions
    blocks and before any query-specific content in the eventual full system
    prompt. The text is a pure function of nothing -- no database access, no
    per-request state -- so repeated calls return byte-identical output.

    The block states each trigger with a concrete reason and a worked
    ``{field, raw_phrase, resolution}`` example, because DESIGN_LOG.md section
    5.4 fixed the Assumption shape to that structured triple and checklist-style
    concreteness has proven to steer this model better than abstract
    description. Zero-match entity/product references are deliberately absent
    as a trigger (hard-fail case per DESIGN_LOG.md section 15); the closing
    line restricts the list to exactly the two triggers so the model never
    manufactures an assumption for an unresolved entity.
    """
    lines = [
        "AMBIGUITY-TRIGGER CHECKLIST -- assumptions",
        "===========================================",
        "The assumptions list is not a general commentary field: it is "
        "populated ONLY when one of the two triggers below fires, and each "
        "trigger exists for a stated, load-bearing reason. If neither trigger "
        "fires, assumptions MUST remain the empty list [] (see the output "
        "conventions). When a trigger fires, add exactly one structured "
        "assumption and fill all three fields: field = the part of the query "
        "the resolution affects; raw_phrase = the user's words verbatim; "
        "resolution = the concrete choice you made.",
        "",
        "TRIGGER 1: RELATIVE TIME WITHOUT EXPLICIT DATES",
        "  Fires when the question bounds time relatively instead of giving "
        "explicit dates: e.g. \"last month\", \"recently\", \"this year\", \"in "
        "the past 90 days\", \"since March\". Such phrases have no fixed "
        "meaning, so silently choosing a range would hide a real interpretive "
        "choice. Resolve the phrase to concrete ISO-8601 boundaries in "
        "filters.date_range AND record the assumption stating the exact range "
        "you resolved.",
        "  Example entry:",
        "    field:       filters.date_range",
        "    raw_phrase:  \"last month\"",
        "    resolution:  \"resolved 'last month' to 2026-08-01T00:00:00 "
        "through 2026-08-31T23:59:59\"",
        "  Boundary: explicit dates (\"between 2026-01-01 and 2026-06-30\") "
        "are not relative references and fire nothing.",
        "",
        "TRIGGER 2: RANKING WITHOUT AN EXPLICIT SIZE",
        "  Fires when the question asks for a ranked or top list without a "
        "count: e.g. \"top products\", \"best-selling items\", \"most popular "
        "product\". An implicit top-N is an interpretation -- without "
        "recording it, \"top products\" could silently mean any number. Use "
        "the default size of 5 and record the assumption stating that the "
        "default was applied.",
        "  Example entry:",
        "    field:       ranking_size",
        "    raw_phrase:  \"top products\"",
        "    resolution:  \"no explicit count was given; used the default of 5\"",
        "  Boundary: an explicit count (\"top 10 products\") is not ambiguous "
        "and fires nothing.",
        "",
        "If neither trigger fires, assumptions MUST be [] -- do not add "
        "assumption entries for anything outside this checklist.",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(build_assumptions_checklist_block())

