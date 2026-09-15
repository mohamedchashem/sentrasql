"""SentraSQL dashboard -- Streamlit entry point (thin wiring only).

Run with ``streamlit run dashboard/app.py`` from the project root (via the
project's ``.venv`` interpreter). This module only assembles single-
responsibility pieces into one page: it delegates the sidebar (brand logo,
tagline, and New Chat button) to ``dashboard.components.sidebar`` and the main
area to the one conversation surface the user is in -- the landing page while
the conversation is empty (``dashboard.components.welcome_screen``, which
composes the hero, the three example-question cards, and the "What can you ask?"
capability card from ``dashboard.components.capability_card``) and otherwise the
transcript of the exchanges so far. There is deliberately no separate branded
page header: the brand identity lives in the sidebar, so nothing competes with
the hero.

One ``(question, result)`` list in ``st.session_state`` is the single source of
truth for both: ``history == []`` renders the landing page and a non-empty list
renders the transcript, so the landing page IS the empty state of the
conversation rather than a second application state beside it -- and clearing
that list (New Chat) simply brings it back, with no separate "go to the landing
page" path. The conversation area is therefore reserved first, as an empty
``st.container()``, and filled only after
``dashboard.components.question_input`` (the pinned question pill) has rendered:
that is what keeps the composer the LAST element on the page while still letting
the question its own widgets produced be drawn ABOVE it. Each submitted question
-- typed or a welcome-screen sample -- is echoed into the conversation area as a
right-aligned bubble followed by the in-transcript processing surface
(``dashboard.components.processing_state``), runs through
``dashboard.graph_client.ask`` while that surface is still on screen (a real run
takes several seconds -- two structured LLM calls plus live SQL execution),
appends its ``(question, result)`` pair to the growing, session-persistent
history list, then reruns once so the finished exchange replaces the processing
surface in the same spot. Nothing about the wait is ever rendered below the
composer.

The landing page and the transcript are mutually exclusive -- exactly one of
them fills the conversation area on every run -- and the in-flight exchange is
never one of both. The transcript is walked oldest-to-newest, echoing each
question as a right-aligned bubble (see ``_render_question``) and handing the
matching outcome to ``dashboard.components.answer_display`` -- called once per
historical entry -- so earlier exchanges stay visible above newer ones, the way
a real chat reads.
No feature logic lives here: widget markup belongs to the components, every
``sentrasql_graph`` call belongs to ``graph_client``, and every color and CSS
rule belongs to ``styling`` -- so this file stays thin as future components
(detail panel, query limit) are added one file at a time.
"""

from __future__ import annotations

import html
import sys
from pathlib import Path

# Make the project root importable no matter how streamlit was launched, so the
# ``dashboard`` package and its ``graph.*`` imports resolve. Mirrors the sys.path
# handling scripts/run_load.py uses for the same reason.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st  # noqa: E402

from dashboard import styling  # noqa: E402
from dashboard.components import (  # noqa: E402
    answer_display,
    processing_state,
    question_input,
    sidebar,
    welcome_screen,
)
from dashboard.graph_client import ask  # noqa: E402

# Session-state key under which the full exchange history is kept: a list of
# ``(question, result)`` pairs in submission order. It starts empty for every
# new browser session and only ever grows within one -- submitting a question
# appends a pair instead of overwriting the previous outcome, so no earlier
# question/answer is ever lost when later runs re-render the page.
_HISTORY_KEY = "sentrasql_chat_history"


def _render_question(question: str) -> None:
    """Echo one historical question as a right-aligned chat bubble.

    Renders the user's exact wording inside the shared bubble markup: a
    right-aligned row with a light-gray, rounded bubble and dark text (all
    visual rules live in ``dashboard.styling.PAGE_CSS`` under the
    ``sentrasql-question`` classes). The text is escaped so it always renders
    literally and never as HTML.
    """
    st.markdown(
        '<div class="sentrasql-question-row">'
        '<div class="sentrasql-question">'
        f"{html.escape(question, quote=False)}"
        "</div></div>",
        unsafe_allow_html=True,
    )


st.set_page_config(
    page_title="SentraSQL",
    page_icon="📊",
    layout="centered",
    # The sidebar carries the brand header and the New Chat button, so it
    # starts expanded instead of hiding behind the collapse arrow.
    initial_sidebar_state="expanded",
)

# Render the sidebar first so its New Chat trigger is captured on this run; the
# component owns every piece of sidebar markup (brand header, button).
new_chat = sidebar.render_sidebar()

# Inject the single global stylesheet once per run (it carries every visual
# rule: the canvas and its decorative layers, the landing page, the pill
# composer, sample cards, capability card, chat bubbles, and the sidebar).
st.markdown(styling.PAGE_CSS, unsafe_allow_html=True)

# A fresh browser session starts with no exchanges; the guard keeps this list
# alive (and growing) across widget-triggered reruns instead of re-creating it.
if _HISTORY_KEY not in st.session_state:
    st.session_state[_HISTORY_KEY] = []

# "New Chat": start a fresh, empty conversation in this same session -- the
# history list is simply reset to empty (a single-conversation reset, not a
# save/switch-between-multiple-chats feature), so the main area below
# re-renders with no transcript on this very run.
if new_chat:
    st.session_state[_HISTORY_KEY] = []

history = st.session_state[_HISTORY_KEY]

# ---------------------------------------------------------------------------
# Main content area, read top to bottom like a normal chat:
#   (1) the conversation area -- the transcript of the exchanges so far, or the
#       landing page while the conversation is still empty -- reserved here as
#       an empty container and filled further down, once this turn's question is
#       known;
#   (2) the pinned question pill, rendered after that container so it is always
#       the LAST element on the page and stays fixed at the bottom of the
#       viewport. This order is what puts the in-flight question and its
#       processing state ABOVE the composer: the pill's own widgets are what
#       report a submission, so the area they are drawn into has to be reserved
#       before them and filled after them.
#   (3) the graph run itself, started once the exchange it belongs to is already
#       on screen.
# ---------------------------------------------------------------------------
conversation = st.container()

# The question pill renders after (below) the whole conversation, never above
# it; PAGE_CSS pins the wrapper to the bottom of the viewport.
question, submitted = question_input.render_question_input()

# The question actually run this turn -- either a manually submitted question or
# a sample-question button clicked on the landing page. Only one of them can
# fire per run: the landing page is rendered only when nothing was submitted.
question_to_run = None

if submitted:
    typed_question = (question or "").strip()
    if not typed_question:
        # Ask with an empty box: reject up front with a friendly message rather
        # than sending a blank query to the graph. This is a transient notice
        # beside the input, not an exchange -- there is no question and no
        # graph result, so nothing is appended to the history.
        st.warning("Please type a question before pressing Ask.")
    else:
        question_to_run = typed_question

with conversation:
    if history:
        # Walk the whole transcript oldest-first: every historical question is
        # echoed as a bubble, then its own answer is rendered by calling the
        # answer_display component once for that entry -- never for the page as
        # a whole.
        for asked_question, result in history:
            _render_question(asked_question)
            answer_display.render_result(result)
    elif question_to_run is None:
        # Empty conversation and nothing submitted on this run: the landing page
        # IS the empty state of the conversation. It is drawn inside a
        # placeholder so a sample question clicked on it can replace it with the
        # first transcript entry on this very run, instead of leaving the hero,
        # the example cards, and the capability card sitting above the exchange
        # they just started.
        landing = st.empty()
        with landing.container():
            clicked_sample = welcome_screen.render_welcome_screen()
        if clicked_sample is not None:
            question_to_run = clicked_sample
            landing.empty()

    # The exchange submitted this turn, drawn into the transcript it belongs to:
    # its question bubble (the same shared bubble markup every historical
    # question uses) followed by the processing surface -- so the wait appears
    # where the answer will land, above the composer, never below it.
    if question_to_run is not None:
        _render_question(question_to_run)
        processing_state.render_processing_state()

if question_to_run is not None:
    # No spinner is drawn here: the processing surface above IS the indicator for
    # this wait, and it is already on screen -- Streamlit has streamed every
    # element rendered so far to the browser while this call blocks.
    result = ask(question_to_run)
    # Grow, never replace: each submitted question appends its own outcome, so
    # earlier exchanges stay in the session and are re-rendered on the rerun.
    history.append((question_to_run, result))
    # Rerun once: the conversation area above was drawn before this outcome
    # existed, so the finished exchange would otherwise be missing. The script
    # re-renders the whole conversation (now including this pair) in exactly the
    # same spot, replacing the processing surface with the answer. Widget state
    # survives the rerun, so nothing the user typed or clicked is lost.
    st.rerun()


