"""Ask-a-question input component: the pinned pill composer.

Renders the single text input where the user types their natural-language
question, the small muted example question beneath it, and the circular
send-icon button that submits it, side by side inside one rounded "pill". The
visual pill (white surface, hairline border, pill radius, focus glow, icon-only
send button, example-hint typography) is applied entirely by
``dashboard.styling.PAGE_CSS`` to the framework wrappers Streamlit renders for
this component's widgets -- the component itself only lays out the pair as two
columns (the input column stacking its field above the hint) and keeps the same
widget keys the rest of the codebase (and the AppTest suite) rely on. The send
button keeps its accessible "Ask" label in the DOM; the stylesheet hides the
text and shows the send glyph instead.

The module exposes one render function that draws the piece and returns
``(question, submitted)`` -- the raw typed text and whether the send button was
pressed on this run -- so ``app.py`` owns only the wiring (the exchange's
processing state, the graph call, result display) and never the widget markup.
No graph logic lives here: pressing send is this component's only job.
"""

from __future__ import annotations

import html

import streamlit as st

# The input's placeholder is the passive prompt; the smaller hint underneath it
# is the concrete example question. The clickable sample questions live on the
# landing page (dashboard.components.welcome_screen).
_PLACEHOLDER = "Ask a question about your sales data..."
_EXAMPLE_HINT = 'e.g. "What was total revenue in the United Kingdom last year?"'
# Accessible label kept in the DOM for the circular send button (the pill's
# visible affordance is the send glyph drawn by PAGE_CSS).
_ASK_LABEL = "Ask"
_INPUT_KEY = "question_input"
_ASK_BUTTON_KEY = "ask_button"


def render_question_input() -> tuple[str, bool]:
    """Draw the pinned question pill; return ``(question, submitted)``.

    The pill is two side-by-side columns: a full-width borderless text input
    (its label collapsed; the placeholder and the small example hint carry the
    guidance) stacked over that hint, and the circular accent send button at the
    right. The typed value survives reruns through its own widget key, so
    unrelated interactions do not wipe what the user has typed.

    Returns:
        A ``(question, submitted)`` pair: the raw text currently in the input
        box, and ``True`` exactly when the send button was pressed on this run.
    """
    input_col, button_col = st.columns(
        [7, 1], vertical_alignment="center", gap="small"
    )
    question = input_col.text_input(
        "Your question",
        key=_INPUT_KEY,
        label_visibility="collapsed",
        placeholder=_PLACEHOLDER,
    )
    # The secondary example hint sits inside the pill, under the field, so the
    # composer stays one cohesive surface rather than a control plus loose text.
    input_col.markdown(
        '<p class="sentrasql-composer-hint">'
        f"{html.escape(_EXAMPLE_HINT, quote=False)}</p>",
        unsafe_allow_html=True,
    )
    submitted = button_col.button(
        _ASK_LABEL,
        type="primary",
        key=_ASK_BUTTON_KEY,
        width="stretch",
    )
    return question, submitted
