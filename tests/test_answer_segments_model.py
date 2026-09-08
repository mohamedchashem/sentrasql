"""Parse-matrix tests for graph.state.AnswerSegments (Fix-1 schema contract).

These tests pin the exact per-segment parse contract the ``AnswerSegments``
schema is now built around (graph/state.py Fix-1): the ``AnswerSegment`` union
is a plain ``TextSegment | RefSegment`` union -- no JSON-Schema
``discriminator``/``oneOf`` construct -- whose two members each carry a
*required* ``type`` literal (no default) and ``model_config =
ConfigDict(extra=\"forbid\")``.

The matrix, exercised end to end through ``AnswerSegments(segments=[...])``
(the same validation path the strict structured-output response is parsed
through):

1. Exactly two shapes parse: ``{\"type\": \"text\", \"content\": str}`` ->
   ``TextSegment`` and ``{\"type\": \"ref\", \"key\": str}`` -> ``RefSegment``.
2. Every other shape raises ``pydantic.ValidationError``:
   - text shape carrying the ref field as well (``key``),
   - ref shape carrying the text field as well (``content``),
   - text shape missing ``content``,
   - ref shape missing ``key``,
   - a segment with no ``type`` at all,
   - a segment with an unknown ``type`` value,
   - a valid text shape with an arbitrary extra key.

Second contract (transmission, not parsing): the schema actually transmitted to
DeepSeek's strict endpoint must be fully inlined. Live verification showed the
``/beta`` strict endpoint rejects ``anyOf`` members that are ``$ref`` pointers
into ``$defs`` (HTTP 400) and requires each union branch to be a
self-contained object schema. Pydantic v2's ``model_json_schema()`` offers no
option to inline ``$defs``; the inlining is performed by langchain-core's tool
conversion (``convert_to_openai_tool`` -> ``dereference_refs`` + the strict
``additionalProperties``/``required`` pass), which is the path
``graph.llm.get_answer_model`` uses by passing the model class to
``with_structured_output(..., strict=True)``. The tests below pin that
transmission contract so the Pydantic models stay the single source of truth.
"""

import json
import unittest

from pydantic import ValidationError

from graph.state import AnswerSegments, RefSegment, TextSegment
from langchain_core.utils.function_calling import convert_to_openai_tool


def _parse_segment(segment: dict):
    """Validate a single segment through the real AnswerSegments schema."""
    return AnswerSegments(segments=[segment]).segments[0]


class AnswerSegmentsParseMatrixTest(unittest.TestCase):
    """Only the two exact shapes parse; every malformed shape is rejected."""

    def test_valid_text_segment_parses(self):
        parsed = _parse_segment({"type": "text", "content": "Total was "})

        self.assertIsInstance(parsed, TextSegment)
        self.assertEqual(parsed.type, "text")
        self.assertEqual(parsed.content, "Total was ")

    def test_valid_ref_segment_parses(self):
        parsed = _parse_segment({"type": "ref", "key": "result.revenue"})

        self.assertIsInstance(parsed, RefSegment)
        self.assertEqual(parsed.type, "ref")
        self.assertEqual(parsed.key, "result.revenue")

    def test_text_segment_with_ref_field_is_rejected(self):
        # The text shape carrying the ref-only field too: a segment must be
        # exactly one of the two shapes, never a mixture.
        with self.assertRaises(ValidationError):
            _parse_segment(
                {"type": "text", "content": "Total was ", "key": "result.revenue"}
            )

    def test_ref_segment_with_text_field_is_rejected(self):
        # The ref shape carrying the text-only field too.
        with self.assertRaises(ValidationError):
            _parse_segment(
                {"type": "ref", "key": "result.revenue", "content": "Total was "}
            )

    def test_text_segment_missing_content_is_rejected(self):
        with self.assertRaises(ValidationError):
            _parse_segment({"type": "text"})

    def test_ref_segment_missing_key_is_rejected(self):
        with self.assertRaises(ValidationError):
            _parse_segment({"type": "ref"})

    def test_segment_without_type_is_rejected(self):
        # No ``type`` tag: neither union member can be selected.
        with self.assertRaises(ValidationError):
            _parse_segment({"content": "Total was "})

    def test_segment_with_unknown_type_is_rejected(self):
        # A ``type`` value matching neither literal can never resolve.
        with self.assertRaises(ValidationError):
            _parse_segment({"type": "image", "content": "Total was "})

    def test_valid_segment_with_arbitrary_extra_key_is_rejected(self):
        # ``extra="forbid"``: an undeclared key fails even on an otherwise
        # valid text shape, so the model can never smuggle in an extra field.
        with self.assertRaises(ValidationError):
            _parse_segment(
                {"type": "text", "content": "Total was ", "note": "must not parse"}
            )


class AnswerSegmentsStrictToolSchemaTest(unittest.TestCase):
    """The schema transmitted to the strict endpoint is fully inlined (no $ref)."""

    @staticmethod
    def _tool() -> dict:
        """The OpenAI-function dict langchain binds for the AnswerSegments schema."""
        return convert_to_openai_tool(AnswerSegments, strict=True)

    @staticmethod
    def _parameters() -> dict:
        return AnswerSegmentsStrictToolSchemaTest._tool()["function"]["parameters"]

    def test_tool_function_is_strict(self):
        self.assertIs(self._tool()["function"]["strict"], True)

    def test_parameters_contain_no_ref_defs_or_union_combinators(self):
        # DeepSeek's strict endpoint rejects anyOf members that are $ref
        # pointers into $defs; the transmitted parameters must be self-contained.
        text = json.dumps(self._parameters())
        for artifact in ("$ref", "$defs", "oneOf", "allOf"):
            self.assertNotIn(artifact, text)

    def test_segment_union_members_are_inlined_self_contained_objects(self):
        items = self._parameters()["properties"]["segments"]["items"]
        members = items["anyOf"]
        self.assertEqual(len(members), 2)
        by_kind = {member["properties"]["type"]["const"]: member for member in members}

        text_segment = by_kind["text"]
        self.assertEqual(text_segment["type"], "object")
        self.assertIs(text_segment["additionalProperties"], False)
        self.assertEqual(text_segment["required"], ["type", "content"])
        self.assertIn("content", text_segment["properties"])
        self.assertNotIn("key", text_segment["properties"])

        ref_segment = by_kind["ref"]
        self.assertEqual(ref_segment["type"], "object")
        self.assertIs(ref_segment["additionalProperties"], False)
        self.assertEqual(ref_segment["required"], ["type", "key"])
        self.assertIn("key", ref_segment["properties"])
        self.assertNotIn("content", ref_segment["properties"])

    def test_root_object_is_closed_and_requires_segments(self):
        parameters = self._parameters()
        self.assertEqual(parameters["type"], "object")
        self.assertIs(parameters["additionalProperties"], False)
        self.assertEqual(parameters["required"], ["segments"])


if __name__ == "__main__":
    unittest.main()
