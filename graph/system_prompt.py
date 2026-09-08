"""Assemble the full system prompt: stable blocks, then the user's query.

This module is the *assembly layer* of the eventual system prompt for
``extract_query_intent`` (Node 2). It owns exactly one responsibility --
deterministic concatenation in the fixed order -- and deliberately contains no
prompt content of its own. Every piece of content it joins lives in one of the
three block modules, each of which owns one logically separated concern:

1. ``graph.schema_prompt`` -- ``build_schema_block()``: the live database
   schema (tables, columns, meanings, closed value sets).
2. ``graph.conventions_prompt`` -- ``build_output_conventions_block()``: the
   output-format rules (empty-list and present-flag conventions).
3. ``graph.assumptions_prompt`` -- ``build_assumptions_checklist_block()``: the
   ambiguity triggers that justify populating the ``assumptions`` list.

Ordering and the cache-efficiency requirement (DESIGN_LOG.md section 16): the
full prompt is assembled as all stable content first -- identically, every
call -- with the variable content (the user's query) strictly last, so
repeated calls within a session present the model with a byte-identical
prefix that a provider-side prompt cache (DeepSeek's prefix cache) can serve
without re-processing. This module never reorders, edits, re-wraps, or
duplicates block content; it joins the exact strings the block functions
return, separated by constant, content-free section breaks.

Adding or changing system-prompt content therefore never happens here -- it
belongs in the block module that owns that content. Any change to this file
that is not (a) a new block added to the ordered composition or (b) a change
to the constant separators would be a change to assembly semantics, not to
prompt content.
"""

from __future__ import annotations

from graph.assumptions_prompt import build_assumptions_checklist_block
from graph.conventions_prompt import build_output_conventions_block
from graph.schema_prompt import build_schema_block

# Content-free separator between stable blocks: two blank lines, matching the
# intra-block blank-line style each block already uses, but visually louder so
# the section boundaries survive in the assembled text.
_BLOCK_SEPARATOR = "\n\n\n"

# Header placed directly before the user's query so the variable tail of the
# prompt is clearly delimited from the stable prefix. This is a structural
# label (mirroring each block's own ALL-CAPS heading style), not instructional
# content.
_QUERY_HEADING = "USER QUERY"
_QUERY_HEADER = f"{_QUERY_HEADING}\n{'=' * len(_QUERY_HEADING)}"


def build_system_prompt(user_query: str) -> str:
    """Assemble the full system prompt for one user query.

    Args:
        user_query: The user's question, verbatim. It is appended unchanged as
            the final section of the prompt; no trimming, wrapping, or other
            mutation is applied.

    Returns:
        The complete prompt string::

            [schema block]
            [output-conventions block]
            [assumptions-checklist block]
            USER QUERY
            <user_query>

        The first three sections are the exact byte output of their owning
        block functions (never edited or duplicated here), joined by constant
        blank-line separators. All stable content therefore precedes the query
        and is byte-identical across calls; only the final query section ever
        varies, which is the property the prompt-cache argument depends on.
    """
    stable_prefix = _BLOCK_SEPARATOR.join(
        [
            build_schema_block(),
            build_output_conventions_block(),
            build_assumptions_checklist_block(),
        ]
    )
    return f"{stable_prefix}{_BLOCK_SEPARATOR}{_QUERY_HEADER}\n\n{user_query}"


if __name__ == "__main__":
    print(build_system_prompt("total revenue"))
