"""Hard-fail validation for Node 7's answer-template segments.

This module owns one responsibility: deciding whether a list of answer
template segments may be rendered against a reference dictionary. It is a pure
deterministic gate -- no formatting, no LLM, no prompt content -- so the
later answer-assembly logic can hard-stop on an invalid template instead of
ever rendering prose with a hole in it.

Segment shape (the contract this validator checks):

* Each segment is a dict with a ``"type"`` field.
* ``{"type": "text", "content": str}`` -- literal prose. After all ``ref``
  segments in the template are substituted, ``content`` will appear in the
  final answer verbatim.
* ``{"type": "ref", "key": str}`` -- a substitution hole whose value is taken
  from the reference dictionary. The dictionary is the flat ``dict[str, str]``
  produced by ``graph.reference_dict.build_reference_dict``.

Two independent hard-fail conditions are checked, in this order, each with
zero exceptions by design:

(a) Every ``ref`` segment's ``key`` must exist in the reference dictionary. A
    missing key is a hard failure (reason ``missing_ref_key:<key>``) -- the
    template would otherwise render a hole or require the composer to
    fabricate a value.

(b) No ``text`` segment's ``content`` may contain any digit character. This is
    absolute -- there are no exceptions by design. A literal digit in prose
    means a value was hard-coded into the template instead of being routed
    through the reference dictionary, which is exactly the "numbers must
    originate from computed results, never invented in prose" architectural
    constraint (DESIGN_LOG section 4.2, disclosure-count sourcing principle,
    applied to the whole answer). Any fixed display parameter that would
    otherwise appear as a digit in text -- e.g. the ranking phrase "top 5" --
    MUST be represented as its own reference key (``display.top_n``), added by
    the dictionary builder, and referenced as a ``ref`` segment; it is never
    special-cased as a permitted digit here. Condition (b) therefore applies
    to the literal ``content`` only: digits arriving later through a ``ref``
    value are checked against the dictionary by condition (a), not by (b).

The function never partially validates: it returns ``None`` only when every
segment passes both conditions, and otherwise returns the exact reason string
of the first failing check so callers can set a machine-readable error (the
project's first-reason gate convention, as in ``compile_sql``'s intent gates).

This module deliberately contains no reference-dictionary construction
(``graph/reference_dict.py``) and no LLM prompt or invocation logic (a
separate follow-up task).
"""

from __future__ import annotations

# Exact reason prefix for a ref segment whose key is absent from the dict.
_MISSING_REF_KEY_PREFIX = "missing_ref_key:"
# Exact reason prefix for a text segment whose literal content holds a digit.
_DIGIT_IN_TEXT_PREFIX = "digit_in_text_segment:"


def validate_segments(
    segments: list[dict],
    references: dict[str, str],
) -> str | None:
    """Hard-fail validate ``segments`` against ``references``.

    Args:
        segments: The answer-template segment list (see module docstring for
            the exact per-segment shape).
        references: The flat substitution dictionary built by
            ``graph.reference_dict.build_reference_dict``.

    Returns:
        ``None`` when every segment passes both conditions. Otherwise the
        exact machine-readable reason of the first violation, checked in the
        fixed order ``(a)`` missing ref keys (in segment order) then ``(b)``
        digits in text (in segment order) -- so the two conditions are
        independent and one passing can never mask the other's failure.

    Raises:
        ValueError: On a malformed segment (an unknown ``"type"``, a ref
            segment without a ``"key"``, or a text segment without a
            ``"content"``). Malformed segments are a template-author bug, not
            a validation verdict, and are failed loudly rather than guessed at.
    """
    for index, segment in enumerate(segments):
        kind = _segment_type(segment, index)
        if kind == "ref":
            key = segment.get("key")
            if not isinstance(key, str):
                raise ValueError(
                    f"Ref segment at index {index} carries no string 'key'."
                )
            if key not in references:
                return f"{_MISSING_REF_KEY_PREFIX}{key}"
        else:
            content = segment.get("content")
            if not isinstance(content, str):
                raise ValueError(
                    f"Text segment at index {index} carries no string 'content'."
                )

    for index, segment in enumerate(segments):
        if _segment_type(segment, index) == "text":
            content = segment["content"]
            if any(character.isdigit() for character in content):
                return f"{_DIGIT_IN_TEXT_PREFIX}{index}"

    return None


def _segment_type(segment: dict, index: int) -> str:
    """Return a segment's validated ``"type"`` or fail loudly on garbage."""
    kind = segment.get("type")
    if kind not in ("text", "ref"):
        raise ValueError(
            f"Segment at index {index} has unsupported type {kind!r}; only "
            "'text' and 'ref' segments are valid."
        )
    return kind
