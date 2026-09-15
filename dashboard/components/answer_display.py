"""Answer display component: the clean answer block for one graph outcome.

Renders the outcome of one graph run as a flat, chat-style answer: an accent
"ANSWER" eyebrow label over the main text sitting directly on the page
background -- no card, no bubble -- with any disclosures the graph appended
shown as a visually distinct, slightly muted sub-section beneath the main
answer, separated by a thin divider. A failure -- whether the graph itself
reported an error on its state or the invocation never completed
(``QueryResult.error``) -- is shown with an error-toned eyebrow and the same
flat layout. The module exposes one render function that decides which case
``result`` is and draws the matching block, so ``app.py`` stays thin and every
future place that needs to surface an outcome reuses the same visual
treatment. All copy shown to the user is either the graph's own pre-authored
``final_answer`` or a fixed non-technical message defined here or in
``graph_client`` -- never exception text or internal reason codes.
"""

from __future__ import annotations

import html
from typing import Literal

import streamlit as st

from dashboard import styling
from dashboard.graph_client import QueryResult

# Eyebrow captions shown at the top of each answer block.
_ANSWER_EYEBROW = styling.ANSWER_EYEBROW
_ERROR_EYEBROW = styling.ERROR_EYEBROW
_HINT_EYEBROW = styling.HINT_EYEBROW

# The disclosures heading the answer-assembly node emits before its bullets.
# When present in ``final_answer`` the UI lifts everything after it into the
# muted disclosure sub-section instead of showing it as plain answer prose.
_DISCLOSURES_HEADING = "Disclosures"
_DISCLOSURES_MARKER = f"\n\n{_DISCLOSURES_HEADING}\n"

# Fixed fallbacks for states the graph contract says should never occur (no
# state, no final answer, an empty one) and for the pre-run empty page.
_FALLBACK_FAILURE_MESSAGE = (
    "Something went wrong while processing that question. Please try again."
)
_HINT_MESSAGE = "Ask a question above — SentraSQL's answer will appear here."


def _split_disclosures(answer: str) -> tuple[str, str | None]:
    """Split ``final_answer`` into ``(main_text, disclosures_text_or_None)``.

    The answer-assembly node appends any disclosures as a ``Disclosures``
    heading followed by one ``- `` bullet per disclosure, separated from the
    answer prose by a blank line. When that exact marker exists the text before
    it is the main answer and everything after it (the bullet list) is the
    disclosure sub-section; otherwise the whole string is the main answer.
    """
    index = answer.find(_DISCLOSURES_MARKER)
    if index == -1:
        return answer, None
    main_text = answer[:index].strip()
    disclosures = answer[index + len(_DISCLOSURES_MARKER) :].strip()
    return main_text, disclosures or None


def render_result(result: QueryResult | None) -> None:
    """Draw the answer block for one graph-run outcome, or a hint before any run.

    Args:
        result: The ``QueryResult`` from ``dashboard.graph_client.ask``, or
            ``None`` when no question has been run yet (renders the neutral
            empty-state hint instead of a stale or blank area).
    """
    if result is None:
        _render_answer_block(_HINT_EYEBROW, _HINT_MESSAGE, tone="neutral")
        return

    # The invocation itself failed before producing a state: ``error`` already
    # carries a fixed, non-technical message from graph_client.
    if result.error is not None:
        _render_answer_block(_ERROR_EYEBROW, result.error, tone="error")
        return

    state = result.state
    if state is None:
        _render_answer_block(
            _ERROR_EYEBROW, _FALLBACK_FAILURE_MESSAGE, tone="error"
        )
        return

    # The graph ran but hit an internal failure: it routes those through its
    # ``handle_error`` node, which stores a pre-authored, user-facing message
    # in ``state.final_answer`` -- show that message, error-toned, rather than
    # any internal reason code.
    if state.error is not None:
        message = (state.final_answer or "").strip()
        _render_answer_block(
            _ERROR_EYEBROW,
            message or _FALLBACK_FAILURE_MESSAGE,
            tone="error",
        )
        return

    answer = (state.final_answer or "").strip()
    if not answer:
        _render_answer_block(
            _ERROR_EYEBROW, _FALLBACK_FAILURE_MESSAGE, tone="error"
        )
        return

    _render_answer_block(_ANSWER_EYEBROW, answer, tone="accent")


def _render_answer_block(
    eyebrow: str,
    message: str,
    tone: Literal["accent", "neutral", "error"],
) -> None:
    """Render one answer block for the given tone.

    ``eyebrow`` is a short uppercase caption (accent-coloured for answers,
    error-coloured for failures, muted for the hint); ``message`` is escaped so
    any text the graph produced (numbers, currency symbols, rule bullet lists)
    renders literally, with line breaks preserved via ``white-space: pre-wrap``
    -- never interpreted as HTML. For a successful answer, a trailing
    ``Disclosures`` section is lifted out of the message and rendered as the
    muted sub-section beneath the main text.
    """
    if tone == "accent":
        main_text, disclosures = _split_disclosures(message)
        disclosure_html = ""
        if disclosures is not None:
            disclosure_html = (
                '<div class="sentrasql-disclosures">'
                '<p class="sentrasql-disclosures__eyebrow">'
                f"{html.escape('Disclosures')}</p>"
                '<div class="sentrasql-disclosures__body">'
                f"{html.escape(disclosures)}</div>"
                "</div>"
            )
        block = (
            '<div class="sentrasql-answer">'
            f'<p class="sentrasql-answer__eyebrow">{html.escape(eyebrow)}</p>'
            '<div class="sentrasql-answer__body">'
            f"{html.escape(main_text)}</div>{disclosure_html}</div>"
        )
    elif tone == "error":
        block = (
            '<div class="sentrasql-answer">'
            '<p class="sentrasql-answer__eyebrow '
            'sentrasql-answer__eyebrow--error">'
            f"{html.escape(eyebrow)}</p>"
            '<div class="sentrasql-answer__body">'
            f"{html.escape(message)}</div></div>"
        )
    else:
        block = f'<div class="sentrasql-hint">{html.escape(message)}</div>'

    st.markdown(block, unsafe_allow_html=True)


