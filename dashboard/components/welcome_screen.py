"""Welcome screen component: the empty-state landing page.

Rendered only while the chat history is empty (fresh session or right after
"New Chat"), this is what a first-time visitor sees instead of a blank area:
the hero (eyebrow, two-line headline, supporting line), three clickable
sample-question cards, the "What can you ask?" capability card
(``dashboard.components.capability_card`` -- its own component, composed in
here), and the collapsed "What can I ask about?" scope/limitations explainer.
The component exposes one render function that draws all of it and returns the
sample question whose button was pressed this run (``None`` when none was), so
``app.py`` can run that question through ``dashboard.graph_client.ask`` exactly
like a manually typed one -- sample questions ask a real question, they are
only a shortcut into the same pipeline.

The greeting/tagline are semantic ``sentrasql-welcome`` markup and the sample
questions are real ``st.button`` widgets (styled into cards by
``dashboard.styling.PAGE_CSS``, which also draws each card's icon chip and
chevron). The buttons keep their full question text as their label, which is
exactly what the AppTest suite clicks and what the pipeline runs.

The three sample questions are chosen to demo three real graph behaviors, and
each uses the exact natural-language text of a live-verified ``eval/cases``
case so its behaviour is known good:

* ``_SAMPLE_AGGREGATE`` -- plain scalar aggregate (eval case
  ``net_scalar_uk_2011``).
* ``_SAMPLE_RULE`` -- fires the AVG_EXCLUDE_ZERO_PRICE policy rule with its
  companion-count disclosure (eval case ``avg_unit_price``).
* ``_SAMPLE_GUARDRAIL`` -- a per-customer breakdown whose result the
  guardrail's row-limit enforcement genuinely clamps (3,835 live customers to
  the 500-row ceiling) and discloses (eval case ``customer_grouped_uk_2011``).
"""

from __future__ import annotations

import html

import streamlit as st

from dashboard.components import capability_card

# Hero content shown above the sample questions: a small uppercase eyebrow, a
# two-line headline, and a one-line description. The eyebrow's "ANSWERS" and the
# headline's second line carry the shared gradient via the
# ``sentrasql-gradient-text`` class (see ``dashboard.styling.PAGE_CSS``).
_EYEBROW_LEAD = "TURN DATA INTO "
_EYEBROW_GRADIENT = "ANSWERS"
_HEADLINE_LINE_1 = "Ask your sales data"
_HEADLINE_LINE_2 = "in plain English"
_DESCRIPTION = (
    "SentraSQL turns your questions into verified answers, with the SQL and "
    "source data behind them."
)

# The three sample questions, in display order, and the widget-key prefix for
# their buttons (keys are ``sample_question_0`` .. ``sample_question_2``).
_SAMPLE_AGGREGATE = (
    "What was the total revenue in the United Kingdom during 2011?"
)
_SAMPLE_RULE = "What is the average unit price across all order lines?"
_SAMPLE_GUARDRAIL = (
    "How much did each customer spend in the United Kingdom in 2011?"
)
_SAMPLE_QUESTIONS = (
    _SAMPLE_AGGREGATE,
    _SAMPLE_RULE,
    _SAMPLE_GUARDRAIL,
)
_SAMPLE_BUTTON_KEY_PREFIX = "sample_question"

# The landing page deliberately renders NO second, legacy disclosure here: the
# dataset scope and limitation facts that used to live in the "What can I ask
# about?" expander now live inside the capability card's "View details" panel
# (see ``dashboard/components/capability_card.py``, which owns them so they are
# presented exactly once).


def render_welcome_screen() -> str | None:
    """Draw the empty-state landing content; return the clicked sample question.

    Renders, top to bottom: the hero (uppercase eyebrow, two-line headline,
    one-line description), one sample-question card per sample laid out in a
    responsive row (each card is a real button carrying the question as its
    label), then the unified "What can you ask?" capability card -- which also
    owns the dataset scope and limitation details, behind its "View details"
    disclosure. When a sample button is pressed on this run its full question
    text is returned so the caller can run it through the graph exactly like a
    typed submission; ``None`` means no sample was clicked.

    The landing page ends there on purpose: there is no legacy second
    "What can I ask about?" section between the capability card and the
    composer, so the capability card's information is never presented twice.
    """
    st.markdown(
        '<div class="sentrasql-welcome">'
        f'<p class="sentrasql-welcome__eyebrow">{_EYEBROW_LEAD}'
        f'<span class="sentrasql-gradient-text">{_EYEBROW_GRADIENT}</span></p>'
        f'<h1 class="sentrasql-welcome__title">{html.escape(_HEADLINE_LINE_1)}<br>'
        f'<span class="sentrasql-gradient-text">{html.escape(_HEADLINE_LINE_2)}</span></h1>'
        f'<p class="sentrasql-welcome__tagline">{html.escape(_DESCRIPTION)}</p>'
        "</div>",
        unsafe_allow_html=True,
    )

    selected = None
    # The three sample cards sit side by side in one equal-width row (three
    # columns on desktop, two on medium widths, one on small screens -- the
    # reflow is pure CSS, see styling.PAGE_CSS section 9).
    columns = st.columns(3)
    for index, question in enumerate(_SAMPLE_QUESTIONS):
        with columns[index]:
            if st.button(
                question,
                key=f"{_SAMPLE_BUTTON_KEY_PREFIX}_{index}",
                width="stretch",
            ):
                selected = question

    # The capability card closes the landing page: it answers "what is this and
    # what can you ask?" and owns the dataset-scope details behind its own
    # "View details" disclosure. It is static markup, so it adds no widget, and
    # nothing else is rendered after it -- the composer is the next thing on the
    # page, so the same information is never shown twice.
    capability_card.render_capability_card()

    return selected
