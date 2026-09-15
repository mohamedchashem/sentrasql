"""SentraSQL dashboard -- Streamlit entry point (thin wiring only).

Run with ``streamlit run dashboard/app.py`` from the project root (via the
project's ``.venv`` interpreter). This module only assembles single-
responsibility pieces into one page: it delegates the sidebar (brand logo,
tagline, and New Chat button) to ``dashboard.components.sidebar`` and the main
area to either the landing page (``dashboard.components.welcome_screen``, which
composes the hero, the three example-question cards, and the "What can you ask?"
capability card from ``dashboard.components.capability_card``) or the
conversation transcript, with ``dashboard.components.question_input`` (the
pinned question pill) rendered last so it always sits at the bottom of the
viewport. There is deliberately no separate branded page header: the brand
identity lives in the sidebar, so nothing competes with the hero. Each
submitted question -- typed or a welcome-screen sample -- runs through
``dashboard.graph_client.ask`` under a loading spinner (a real run takes
several seconds -- two structured LLM calls plus live SQL execution), appends
its ``(question, result)`` pair to a growing, session-persistent history list,
then reruns once so the fresh exchange renders up in the transcript. A New
Chat click from the sidebar wipes that list so the user gets a fresh, empty
conversation in the same session; an empty history instead shows the landing
page (hero, sample cards, capability card, "What can I ask about?"), and a
clicked sample question runs through the exact same ask-and-append path as a
typed one.

The welcome screen and the transcript are mutually exclusive -- exactly one of
them fills the main area on every run. The transcript is walked oldest-to-
newest, echoing each question as a right-aligned bubble (see
``_render_question``) and handing the matching outcome to
``dashboard.components.answer_display`` -- called once per historical entry --
so earlier exchanges stay visible above newer ones, the way a real chat reads.
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
import os

if "DEEPSEEK_API_KEY" in st.secrets:
    os.environ["DEEPSEEK_API_KEY"] = st.secrets["DEEPSEEK_API_KEY"]

st.write(f"DEBUG: key found in st.secrets = {'DEEPSEEK_API_KEY' in st.secrets}")
st.write(f"DEBUG: key found in os.environ = {'DEEPSEEK_API_KEY' in os.environ}")
if "DEEPSEEK_API_KEY" in os.environ:
    key = os.environ["DEEPSEEK_API_KEY"]
    st.write(f"DEBUG: key starts with = {key[:6]}, length = {len(key)}")
    
from dashboard import styling  # noqa: E402
from dashboard.components import (  # noqa: E402
    answer_display,
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

# Spinner copy is explicit about the wait because a live run is genuinely
# slow (structured LLM calls plus SQL execution against the real database).
_RUNNING_COPY = (
    "Running your question through SentraSQL — this can take several seconds…"
)


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
#   (1) either the welcome screen (history empty) or the full conversation
#       transcript, oldest exchange first -- the two are mutually exclusive;
#   (2) the pinned question pill, rendered last so it always sits below every
#       message and stays fixed at the bottom of the viewport.
# ---------------------------------------------------------------------------
if history:
    # Walk the whole transcript oldest-first: every historical question is
    # echoed as a bubble, then its own answer is rendered by calling the
    # answer_display component once for that entry -- never for the page as a
    # whole.
    for question, result in history:
        _render_question(question)
        answer_display.render_result(result)
    clicked_sample = None
else:
    # No conversation yet (fresh session or after New Chat): the welcome
    # screen replaces the transcript area. A clicked sample question is a
    # real submission -- treated exactly like a typed one below.
    clicked_sample = welcome_screen.render_welcome_screen()

# The question pill renders after (below) the whole conversation, never above
# it; PAGE_CSS pins the wrapper to the bottom of the viewport.
question, submitted = question_input.render_question_input()

# The question actually run this turn -- either a manually submitted question
# or a sample-question button clicked on the welcome screen. Only one can fire
# per run: the input sits below the welcome screen, so Ask and a sample never
# both report a submission.
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
elif clicked_sample is not None:
    question_to_run = clicked_sample

if question_to_run is not None:
    with st.spinner(_RUNNING_COPY):
        result = ask(question_to_run)
    # Grow, never replace: each submitted question appends its own outcome, so
    # earlier exchanges stay in the session and are re-rendered on the rerun.
    history.append((question_to_run, result))
    # The transcript above was already drawn from the pre-submission history,
    # so this fresh exchange would otherwise land below the input box. Rerun
    # once: the script re-renders the whole conversation (now including this
    # pair) above the input, exactly where chat messages belong. Widget state
    # survives the rerun, so nothing the user typed or clicked is lost.
    st.rerun()


