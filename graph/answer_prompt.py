"""Deterministic, per-call prompt content for ``assemble_answer`` (Node 7).

This module owns the answer-composition prompt: the model-facing text that
tells ``assemble_answer``'s structured-output call what it may state and in
what shape. It is a sibling of the Node 2 prompt modules
(``graph/schema_prompt.py``, ``graph/conventions_prompt.py``,
``graph/assumptions_prompt.py``) and follows the same conventions -- a single
public ``build_*`` function returning one string built from an ALL-CAPS
heading, an ``=`` underline, and a plain ``lines`` list -- with one deliberate
difference: unlike those stable, query-independent blocks, this content is
*per call*, because the set of values the model may cite is different for
every query. Each section below traces to a decided contract, not to assumed
model behavior:

1. AVAILABLE REFERENCE VALUES -- the exact flat ``dict[str, str]`` produced by
   ``graph.reference_dict.build_reference_dict`` for the current
   ``state.main_results`` / ``state.query_intent``. The dictionary is the
   model's whitelist of stateable numbers and fixed display parameters:
   listing every key that actually exists for this call (sorted, each with its
   pre-formatted value) is what makes the no-invented-numbers rule below
   enforceable -- a key the model cannot see is a key it cannot correctly
   reference, and a value the model cannot see is a value it would have to
   guess or recompute.

2. ANSWER SEGMENT STRUCTURE -- the ``{type: "text"}`` / ``{type: "ref"}``
   segment contract that ``graph/state.py``'s ``AnswerSegments`` schema and
   ``graph/segment_validator.py`` both define: ordered items that are either
   literal prose or a substitution hole keyed into the reference dictionary.

3. NO-DIGITS-IN-TEXT RULE -- condition (b) of
   ``graph/segment_validator.validate_segments`` stated to the model in plain
   terms: a digit in literal ``text`` content is a hard failure, and every
   number must arrive through a ``ref`` segment instead. The rule has zero
   exceptions, and a number with no available key may not be stated at all.

The module deliberately contains no reference-dictionary construction (that is
``graph/reference_dict.py``), no validation (``graph/segment_validator.py``),
and no model configuration or invocation (``graph/llm.py``).
"""

from __future__ import annotations


def build_answer_prompt(references: dict[str, str]) -> str:
    """Build the per-call answer-composition prompt for ``references``.

    Args:
        references: The flat substitution dictionary built by
            ``graph.reference_dict.build_reference_dict`` for the current call.
            Every key actually present is listed with its value; no other key
            may be referenced in the answer.

    Returns:
        A single deterministic string: for a given ``references`` dict the
        output is byte-identical across calls (keys are always listed in
        sorted order). It contains three sections: the available reference
        values, the answer segment structure, and the no-digits-in-text rule.
    """
    lines = [
        "AVAILABLE REFERENCE VALUES",
        "==========================",
        "The lines below are the ONLY values you may cite in this answer. "
        "Each line names one reference key and its exact, pre-formatted "
        "value. Never recompute, reformat, round, or rephrase a value, and "
        "never cite a key that is not listed here.",
        "",
    ]
    if references:
        lines.extend(
            f"- {key}: {value}" for key, value in sorted(references.items())
        )
    else:
        lines.append(
            "- (none -- this query produced no reference values, so no ref "
            "segment is possible and the answer must state no number at all)"
        )

    lines.extend(
        [
            "",
            "ANSWER SEGMENT STRUCTURE",
            "========================",
            "Your reply is an ordered list of segments. Each segment is "
            "exactly one of two shapes:",
            '  {"type": "text", "content": "<prose>"}',
            '  {"type": "ref", "key": "<key>"}',
            "A text segment carries literal prose. A ref segment is a "
            "placeholder for one value from the AVAILABLE REFERENCE VALUES "
            "section above.",
            "Place a ref segment wherever a number or a fixed display "
            "parameter must appear, and write the surrounding words as text "
            "segments. The key of every ref segment must match one of the "
            "listed keys EXACTLY -- a key that is not listed above does not "
            "exist for this call and must never be referenced.",
            "",
            "NO-DIGITS-IN-TEXT RULE",
            "======================",
            "Absolute, with no exceptions: a text segment's content must not "
            "contain any digit character (0-9) -- not in a number, a year, a "
            "price, an ordinal, or inside a word. Digits are allowed ONLY "
            "inside the value of a ref segment, because that value came from "
            "the reference dictionary rather than being typed into prose.",
            "Every number you state must be routed through a ref segment "
            "whose key is listed in AVAILABLE REFERENCE VALUES. If the number "
            "you want to state has no listed key, do not state it: rephrase "
            "so the sentence needs no number. Writing a digit in a text "
            "segment is a hard failure.",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    print(build_answer_prompt({}))
