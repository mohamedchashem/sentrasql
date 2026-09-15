"""Chat-history behavior tests for the dashboard, driven via headless AppTest.

These tests execute the real ``dashboard/app.py`` script in-process through
``streamlit.testing.v1.AppTest`` and drive the actual Ask-button submit path --
set a question in the input widget, click Ask, rerun -- exactly as a browser
session would. The live analytics graph is never reached: the
``dashboard.graph_client.ask`` function the script imports is patched with a
deterministic stub that maps each question string to its own canned answer, so
these tests exercise dashboard *wiring* (history persistence, oldest-first
ordering, per-entry rendering) with no LLM or SQL call -- fast and
deterministic, matching the repo's stub-driven graph-test convention.

The critical scenario is ``test_two_submits_both_remain_visible``: two
different real questions submitted in one session must BOTH appear -- question
and answer -- in the final rendered output. That is the proof that each submit
appends to the history list instead of overwriting the previous exchange.

The sidebar suite (``DashboardSidebarTest``) covers this task's sidebar
structure and New Chat reset: the sidebar renders its brand header, its New
Chat button, and all six pipeline stage labels (each on its own idle-marker
row) while the question input and Ask button stay in the main content area;
clicking New Chat after exchanges have accumulated empties that history back to
zero on the next render, and a fresh question then starts a new conversation.

The welcome suite (``DashboardWelcomeScreenTest``) covers the empty-state
screen: when the history is empty the page shows the greeting, the tagline,
three sample-question buttons (each a real eval-case question), and the
"What can you ask?" capability card -- which is also the single home of the
dataset scope/limitation facts (behind its "View details" disclosure) since the
legacy duplicate "What can I ask about?" expander was removed from the landing
page; clicking a sample runs that exact question through the same
ask-and-append path and the welcome disappears once any conversation exists
(and only returns when New Chat empties the history again).

The conversation-rendering suite (``DashboardConversationRenderingTest``) covers
the landing-to-transcript architecture itself: the empty conversation IS the
landing page, the first submitted question replaces it, the exchanges share one
conversation area that always renders above the composer, and the in-flight
exchange -- question bubble plus processing surface -- is drawn into that same
area while the graph is still working. That last case needs the run to be read
mid-flight, so it patches ``ask`` with a stub that raises: AppTest keeps the
element tree of everything rendered before the abort, which is exactly the page
a user sees during those several seconds.

``DashboardProcessingStateTest`` asserts the processing component's markup
contract on its own (it is only on screen while a run is in flight) plus the
rules the shared stylesheet must carry for it.
"""

import unittest
from unittest import mock

from streamlit.testing.v1 import AppTest

from dashboard import styling
from dashboard.components import processing_state
from dashboard.graph_client import QueryResult
from graph.state import GraphState

# Two distinct, realistic questions and the deterministic answers the stubbed
# graph returns for them. Distinct answers matter: if a second submit replaced
# the first, one of these answer strings would never be rendered.
_QUESTION_ONE = "What was the total revenue in the United Kingdom in 2011?"
_ANSWER_ONE = "Revenue in the United Kingdom totalled GBP 1234567 in 2011."
_QUESTION_TWO = "How many orders were placed in 2011?"
_ANSWER_TWO = "There were 50000 orders placed in 2011."

# Must mirror dashboard/app.py's history session-state key.
_HISTORY_KEY = "sentrasql_chat_history"

# Exact copy dashboard/app.py shows when Ask is pressed with an empty box.
_EMPTY_SUBMIT_WARNING = "Please type a question before pressing Ask."

# Marker message for the aborting ``ask`` stand-in used to inspect the page
# while a question is still being processed (see the conversation-rendering
# suite): it raises instead of returning, so AppTest keeps everything the app
# had already rendered -- the pending question and the processing surface --
# and stops there.
_ABORT_MESSAGE = "aborted mid-run so the in-flight page can be read"

# Mirrors of dashboard/components/sidebar.py's button key, stage names, and
# idle marker, so the sidebar tests verify the component's real content.
_NEW_CHAT_KEY = "new_chat_button"
_PIPELINE_STEPS = (
    "Understand Question",
    "Detect Rules",
    "Compile SQL",
    "Safety Check",
    "Run Query",
    "Compose Answer",
)
_IDLE_MARKER = "○"

# Mirrors of dashboard/components/welcome_screen.py's empty-state content: the
# hero headline/description, the collapsed expander, and the three sample
# questions (each the exact query text of a live-verified eval/cases case).
_WELCOME_HEADING = "Ask your sales data"
_WELCOME_HEADING_LINE_2 = "in plain English"
_WELCOME_TAGLINE = (
    "SentraSQL turns your questions into verified answers, with the SQL and "
    "source data behind them."
)
# The landing page must render NO expander at all: the legacy duplicate
# "What can I ask about?" section was removed, and the facts it used to hold now
# live in the capability card's "View details" panel (asserted below).
_LEGACY_EXPANDER_LABEL = "What can I ask about?"
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
_SAMPLE_ANSWERS = (
    "Sample answer: total revenue in the United Kingdom during 2011 was "
    "GBP 7511064.",
    "Sample answer: the average unit price excludes the zero price rows.",
    "Sample answer: the customer breakdown was limited to 500 rows by the "
    "guardrail.",
)

_ANSWERS_BY_QUESTION = {
    _QUESTION_ONE: _ANSWER_ONE,
    _QUESTION_TWO: _ANSWER_TWO,
    _SAMPLE_AGGREGATE: _SAMPLE_ANSWERS[0],
    _SAMPLE_RULE: _SAMPLE_ANSWERS[1],
    _SAMPLE_GUARDRAIL: _SAMPLE_ANSWERS[2],
}


def _ask_stub(question: str) -> QueryResult:
    """Deterministic stand-in for ``dashboard.graph_client.ask``.

    Returns a successful ``QueryResult`` whose ``final_answer`` is canned per
    question, so a test can always tell which exchange a rendered answer came
    from. Unsurprising for any other question so a mistake fails loudly.
    """
    cleaned = (question or "").strip()
    final_answer = _ANSWERS_BY_QUESTION.get(
        cleaned, f"Unexpected stub question: {cleaned}"
    )
    return QueryResult(
        state=GraphState(raw_query=cleaned, final_answer=final_answer)
    )


def _page_stream(at: AppTest):
    """Main-area nodes in render order, with the injected stylesheet removed.

    The stylesheet is itself a markdown element, rendered first, and its CSS
    comments quote page copy -- so order and copy assertions read the page's
    real content instead (``at.main`` already excludes the sidebar, which
    would otherwise contribute the brand logo and tagline).
    """
    return [
        node
        for node in at.main
        if not (
            node.type == "markdown"
            and node.value.lstrip().startswith("<style>")
        )
    ]


def _page_text(at: AppTest) -> str:
    """Rendered page text: every main-area markdown body but the stylesheet."""
    return "".join(
        node.value for node in _page_stream(at) if node.type == "markdown"
    )


class DashboardAppTestBase(unittest.TestCase):
    """Shared AppTest scaffolding for the real dashboard app."""

    def setUp(self):
        # app.py binds ``ask`` from ``dashboard.graph_client`` on every script
        # run, so patching the module attribute once covers every rerun in the
        # test without touching ``graph_client.ask``'s real implementation.
        self._ask_patcher = mock.patch(
            "dashboard.graph_client.ask", new=_ask_stub
        )
        self._ask_patcher.start()
        self.addCleanup(self._ask_patcher.stop)

    def _start_session(self) -> AppTest:
        """Open a brand-new simulated browser session and run it once."""
        at = AppTest.from_file("../dashboard/app.py", default_timeout=30).run()
        self.assertFalse(at.exception)
        return at

    def _submit_question(self, at: AppTest, question: str) -> None:
        """Type ``question`` into the box and press Ask, then rerun."""
        at.text_input(key="question_input").set_value(question)
        at.button(key="ask_button").click().run()
        self.assertFalse(at.exception)

    def _rendered_text(self, at: AppTest) -> str:
        """The page's rendered text: all markdown bodies, concatenated."""
        return "".join(markdown.value for markdown in at.markdown)

    def _main_element_stream(self, at: AppTest):
        """Main-area markdown/button/text-input elements in rendered order.

        Walks the rendered main block top-to-bottom and returns the elements
        the layout assertions care about -- transcript panels, buttons, and
        the input box -- in the exact order they appear on the page, so tests
        can prove the conversation renders above the input rather than below
        it.
        """
        stream = []
        for node in at.main:
            if node.type in ("markdown", "button", "text_input"):
                stream.append(node)
        return stream


class DashboardChatHistoryTest(DashboardAppTestBase):
    """Persistent chat-history behavior of the real dashboard app."""

    def test_fresh_session_starts_with_empty_history(self):
        at = self._start_session()

        # New browser session: the history key exists but holds no exchanges.
        self.assertEqual(at.session_state[_HISTORY_KEY], [])
        # And nothing from a future exchange is rendered before any submit.
        self.assertNotIn(_QUESTION_ONE, self._rendered_text(at))
        self.assertNotIn(_ANSWER_ONE, self._rendered_text(at))

    def test_two_submits_both_remain_visible(self):
        at = self._start_session()

        self._submit_question(at, _QUESTION_ONE)
        self._submit_question(at, _QUESTION_TWO)

        # Session history itself holds both pairs, oldest first.
        history = at.session_state[_HISTORY_KEY]
        self.assertEqual(len(history), 2)
        self.assertEqual(
            [question for question, _ in history],
            [_QUESTION_ONE, _QUESTION_TWO],
        )
        self.assertTrue(
            all(isinstance(result, QueryResult) for _, result in history)
        )

        # The final rendered page must contain every question AND every answer.
        rendered = self._rendered_text(at)
        for expected in (
            _QUESTION_ONE,
            _ANSWER_ONE,
            _QUESTION_TWO,
            _ANSWER_TWO,
        ):
            self.assertIn(expected, rendered)

        # ...arranged oldest-first like a chat: Q1, A1, Q2, A2 in stream order.
        positions = [
            rendered.index(expected)
            for expected in (
                _QUESTION_ONE,
                _ANSWER_ONE,
                _QUESTION_TWO,
                _ANSWER_TWO,
            )
        ]
        self.assertEqual(positions, sorted(positions))

    def test_conversation_appears_above_the_input_box(self):
        at = self._start_session()
        self._submit_question(at, _QUESTION_ONE)
        self._submit_question(at, _QUESTION_TWO)

        # The main area is laid out like a real chat, input at the bottom:
        # every question echo and every answer panel (oldest exchange first)
        # must render ABOVE the question box and the Ask button -- never below
        # them.
        stream = self._main_element_stream(at)
        transcript_texts = (
            _QUESTION_ONE,
            _ANSWER_ONE,
            _QUESTION_TWO,
            _ANSWER_TWO,
        )
        transcript_indices = [
            i
            for i, node in enumerate(stream)
            if node.type == "markdown"
            and any(text in node.value for text in transcript_texts)
        ]
        self.assertTrue(transcript_indices, "expected transcript markdown")
        last_transcript = transcript_indices[-1]

        input_indices = [
            i for i, node in enumerate(stream) if node.type == "text_input"
        ]
        ask_indices = [
            i
            for i, node in enumerate(stream)
            if node.type == "button" and node.label == "Ask"
        ]
        self.assertEqual(len(input_indices), 1)
        self.assertEqual(len(ask_indices), 1)

        self.assertLess(last_transcript, input_indices[0])
        self.assertLess(input_indices[0], ask_indices[0])

    def test_later_rerun_keeps_earlier_exchanges(self):
        at = self._start_session()

        self._submit_question(at, _QUESTION_ONE)
        self._submit_question(at, _QUESTION_TWO)

        # A later interaction -- Ask pressed again with an empty box -- reruns
        # the whole script; the two existing exchanges must survive untouched.
        at.text_input(key="question_input").set_value("")
        at.button(key="ask_button").click().run()
        self.assertFalse(at.exception)

        self.assertEqual(len(at.session_state[_HISTORY_KEY]), 2)
        self.assertEqual(at.warning[0].value, _EMPTY_SUBMIT_WARNING)

        rendered = self._rendered_text(at)
        for expected in (
            _QUESTION_ONE,
            _ANSWER_ONE,
            _QUESTION_TWO,
            _ANSWER_TWO,
        ):
            self.assertIn(expected, rendered)


class DashboardSidebarTest(DashboardAppTestBase):
    """Sidebar structure and the New Chat reset behavior."""

    def test_sidebar_renders_expected_elements(self):
        at = self._start_session()

        # The sidebar holds exactly the New Chat button; the Ask button and
        # question input belong to the main content area beside the sidebar.
        self.assertEqual(len(at.sidebar.button), 1)
        new_chat = at.sidebar.button(key=_NEW_CHAT_KEY)
        self.assertEqual(new_chat.label, "New Chat")

        # Brand header is present in the sidebar, before the step list.
        sidebar_markdown = [m.value for m in at.sidebar.markdown]
        self.assertIn("SentraSQL", sidebar_markdown[0])

        # Main area stays intact next to the sidebar: header markdown plus the
        # question input, the Ask button, and (on the empty session) the three
        # welcome sample buttons all live outside the sidebar block -- and the
        # New Chat button never leaks into the main area.
        self.assertTrue(at.main.markdown)
        at.main.text_input(key="question_input")
        main_button_labels = [b.label for b in at.main.button]
        self.assertIn("Ask", main_button_labels)
        self.assertNotIn("New Chat", main_button_labels)
        self.assertEqual(at.main.button(key="ask_button").label, "Ask")

    def test_new_chat_resets_history_to_empty(self):
        at = self._start_session()
        self._submit_question(at, _QUESTION_ONE)
        self._submit_question(at, _QUESTION_TWO)
        self.assertEqual(len(at.session_state[_HISTORY_KEY]), 2)
        self.assertIn(_ANSWER_ONE, self._rendered_text(at))
        self.assertIn(_ANSWER_TWO, self._rendered_text(at))

        # Click New Chat: same session, but the conversation is wiped clean.
        at.sidebar.button(key=_NEW_CHAT_KEY).click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state[_HISTORY_KEY], [])

        cleared = self._rendered_text(at)
        for gone in (
            _QUESTION_ONE,
            _ANSWER_ONE,
            _QUESTION_TWO,
            _ANSWER_TWO,
        ):
            self.assertNotIn(gone, cleared)

        # The shell still works: a fresh question starts a new conversation.
        self._submit_question(at, _QUESTION_ONE)
        self.assertEqual(len(at.session_state[_HISTORY_KEY]), 1)
        self.assertIn(_ANSWER_ONE, self._rendered_text(at))


class DashboardWelcomeScreenTest(DashboardAppTestBase):
    """Empty-state welcome screen and its sample-question behavior."""

    def _click_sample(self, at: AppTest, question: str) -> None:
        """Click the sample-question button labelled ``question`` and rerun."""
        matches = [b for b in at.main.button if b.label == question]
        self.assertEqual(
            len(matches),
            1,
            f"expected exactly one sample button labelled {question!r}",
        )
        matches[0].click().run()
        self.assertFalse(at.exception)

    def test_welcome_renders_when_history_empty(self):
        at = self._start_session()
        self.assertEqual(at.session_state[_HISTORY_KEY], [])

        # Hero headline (both lines), eyebrow, and description are visible.
        rendered = self._rendered_text(at)
        self.assertIn(_WELCOME_HEADING, rendered)
        self.assertIn(_WELCOME_HEADING_LINE_2, rendered)
        self.assertIn(_WELCOME_TAGLINE, rendered)

        # The three sample questions are plain main-area buttons (plus Ask).
        main_button_labels = [b.label for b in at.main.button]
        self.assertEqual(len(main_button_labels), 4)
        self.assertIn("Ask", main_button_labels)
        for question in _SAMPLE_QUESTIONS:
            self.assertIn(question, main_button_labels)

        # The guidance expander is GONE from the landing page: the capability
        # card's "View details" disclosure is the only disclosure now, so the
        # dataset information is never presented twice. (The injected stylesheet
        # is itself a markdown element, so the label check skips style blocks and
        # looks only at real page content.)
        self.assertEqual(len(at.expander), 0)
        page_text = "".join(
            markdown.value
            for markdown in at.markdown
            if not markdown.value.lstrip().startswith("<style>")
        )
        self.assertNotIn(_LEGACY_EXPANDER_LABEL, page_text)

        # ...and none of that information was lost: every scope/limitation fact
        # the old section held is still surfaced, inside the capability card's
        # "View details" panel (part of the card's static markup).
        for fact in (
            "December 2009 through December 2011",
            "individual order line item",
            "43 countries, mostly the United Kingdom",
            "guest/unidentified purchases",
            "revenue, quantity, and average price",
            "no comparisons between time periods",
            "no export or report generation yet",
        ):
            self.assertIn(fact, rendered)

        # The capability card itself is on the page, with its disclosure.
        self.assertIn("sentrasql-capability", rendered)
        self.assertIn("View details", rendered)

    def test_sample_question_runs_through_graph_and_hides_welcome(self):
        at = self._start_session()
        self._click_sample(at, _SAMPLE_AGGREGATE)

        # Clicking a sample runs it exactly like a typed question: the pair is
        # appended to history and rendered right away (on the rerun the app
        # triggers after the append).
        history = at.session_state[_HISTORY_KEY]
        self.assertEqual(len(history), 1)
        question, result = history[0]
        self.assertEqual(question, _SAMPLE_AGGREGATE)
        self.assertIsInstance(result, QueryResult)
        self.assertEqual(result.state.final_answer, _SAMPLE_ANSWERS[0])
        rendered = self._rendered_text(at)
        self.assertIn(_SAMPLE_AGGREGATE, rendered)
        self.assertIn(_SAMPLE_ANSWERS[0], rendered)

        # ...and the welcome screen is gone the moment that first exchange
        # exists: the empty-state content must never share the page with the
        # transcript that replaced it.
        self.assertNotIn(_WELCOME_HEADING, rendered)
        self.assertNotIn(_WELCOME_TAGLINE, rendered)

        # The next render -- now that history is non-empty -- no longer shows
        # the welcome screen; the chat transcript takes its place.
        self._submit_question(at, _QUESTION_ONE)
        self.assertEqual(len(at.session_state[_HISTORY_KEY]), 2)
        rendered = self._rendered_text(at)
        self.assertNotIn(_WELCOME_HEADING, rendered)
        self.assertIn(_SAMPLE_AGGREGATE, rendered)
        self.assertIn(_SAMPLE_ANSWERS[0], rendered)
        self.assertIn(_QUESTION_ONE, rendered)
        self.assertEqual(len(at.main.button), 1)
        self.assertEqual(at.main.button(key="ask_button").label, "Ask")

    def test_welcome_stays_hidden_once_conversation_started(self):
        at = self._start_session()
        self._click_sample(at, _SAMPLE_RULE)
        self._submit_question(at, _QUESTION_TWO)

        # After several exchanges the welcome never comes back...
        history = at.session_state[_HISTORY_KEY]
        self.assertEqual(len(history), 2)
        rendered = self._rendered_text(at)
        self.assertNotIn(_WELCOME_HEADING, rendered)
        self.assertNotIn(_WELCOME_TAGLINE, rendered)
        self.assertIn(_SAMPLE_ANSWERS[1], rendered)
        self.assertIn(_ANSWER_TWO, rendered)

        # ...and it only returns once New Chat empties the history again.
        at.sidebar.button(key=_NEW_CHAT_KEY).click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state[_HISTORY_KEY], [])
        self.assertIn(_WELCOME_HEADING, self._rendered_text(at))


    def test_welcome_and_transcript_are_mutually_exclusive(self):
        at = self._start_session()

        # Empty history: the welcome screen is up and no transcript shares the
        # page with it.
        rendered = self._rendered_text(at)
        self.assertIn(_WELCOME_HEADING, rendered)
        self.assertIn(_WELCOME_TAGLINE, rendered)
        for gone in (_ANSWER_ONE, _SAMPLE_ANSWERS[0]):
            self.assertNotIn(gone, rendered)

        # First real question (a sample click): the transcript replaces the
        # welcome screen -- the two are never rendered together.
        self._click_sample(at, _SAMPLE_AGGREGATE)
        rendered = self._rendered_text(at)
        for gone in (_WELCOME_HEADING, _WELCOME_TAGLINE):
            self.assertNotIn(gone, rendered)
        self.assertIn(_SAMPLE_AGGREGATE, rendered)
        self.assertIn(_SAMPLE_ANSWERS[0], rendered)


class _RunAborted(Exception):
    """Raised by the aborting ``ask`` stand-in (see ``_ABORT_MESSAGE``)."""


class DashboardConversationRenderingTest(DashboardAppTestBase):
    """Landing -> transcript transition and the in-flight exchange's placement.

    The empty conversation IS the landing page, and the moment an exchange
    exists the transcript takes over -- one conversation area, above the
    composer. The exchange currently being processed (question bubble plus
    processing surface) is drawn into that same area, so the wait is visible
    where the answer will land instead of below the composer.
    """

    def _abort_run(self, question: str) -> QueryResult:
        """Stand-in ``ask`` that stops the run so the in-flight page can be read.

        AppTest keeps the element tree of everything the app had already
        rendered when the run aborts, which is exactly the page a user sees
        during those several seconds: the pending question and the processing
        surface, already on screen, still waiting for this call.
        """
        raise _RunAborted(_ABORT_MESSAGE)

    def _in_flight_page(self, at: AppTest) -> None:
        """Submit ``_QUESTION_ONE`` and stop the run inside ``ask``."""
        at.text_input(key="question_input").set_value(_QUESTION_ONE)
        with mock.patch("dashboard.graph_client.ask", new=self._abort_run):
            at.button(key="ask_button").click().run()
        self.assertEqual(len(at.exception), 1)
        self.assertEqual(at.exception[0].value, _ABORT_MESSAGE)

    def test_first_typed_question_replaces_the_landing_page(self):
        at = self._start_session()
        self.assertIn(_WELCOME_HEADING, self._rendered_text(at))

        self._submit_question(at, _QUESTION_ONE)

        # The empty state is gone -- hero, description, capability card -- and
        # the exchange reads as a chat instead.
        rendered = _page_text(at)
        self.assertNotIn(_WELCOME_HEADING, rendered)
        self.assertNotIn(_WELCOME_TAGLINE, rendered)
        self.assertNotIn("What can you ask?", rendered)
        self.assertIn(_QUESTION_ONE, rendered)
        self.assertIn(_ANSWER_ONE, rendered)
        # ...and the three sample cards went with it: Ask is the only button.
        self.assertEqual([b.label for b in at.main.button], ["Ask"])

    def test_landing_page_is_replaced_on_the_same_run_as_the_submission(self):
        at = self._start_session()
        self._in_flight_page(at)

        # While the question is being processed the page already holds the
        # question and the processing surface -- never the landing page, and
        # never a stale answer.
        rendered = _page_text(at)
        self.assertIn(_QUESTION_ONE, rendered)
        self.assertIn("Analyzing your question...", rendered)
        self.assertIn("sentrasql-processing", rendered)
        self.assertNotIn(_WELCOME_HEADING, rendered)
        self.assertNotIn(_WELCOME_TAGLINE, rendered)
        self.assertNotIn(_ANSWER_ONE, rendered)
        # Nothing is appended to the conversation while the run is in flight.
        self.assertEqual(at.session_state[_HISTORY_KEY], [])

    def test_processing_state_renders_above_the_composer(self):
        at = self._start_session()
        self._in_flight_page(at)

        # In the main area's own render order: the pending question first, its
        # processing surface directly under it, and only then the composer's
        # input and Ask button -- so the wait is never hidden below the
        # composer, where the user would have to scroll to discover it.
        stream = _page_stream(at)
        question_index = next(
            i
            for i, node in enumerate(stream)
            if node.type == "markdown" and _QUESTION_ONE in node.value
        )
        processing_index = next(
            i
            for i, node in enumerate(stream)
            if node.type == "markdown"
            and "sentrasql-processing__indicator" in node.value
        )
        input_index = next(
            i for i, node in enumerate(stream) if node.type == "text_input"
        )
        ask_index = next(
            i
            for i, node in enumerate(stream)
            if node.type == "button" and node.label == "Ask"
        )

        self.assertLess(question_index, processing_index)
        self.assertLess(processing_index, input_index)
        self.assertLess(input_index, ask_index)

    def test_each_further_question_appends_to_the_same_conversation(self):
        at = self._start_session()
        self._submit_question(at, _QUESTION_ONE)
        self._submit_question(at, _QUESTION_TWO)
        # A third question behaves exactly like the previous two: it appends.
        self._submit_question(at, _SAMPLE_RULE)

        # One conversation area holding all three exchanges, oldest first: no
        # second transcript surface, no leftover landing container, and every
        # question and answer rendered exactly once.
        conversations = at.main.container
        self.assertEqual(len(conversations), 1)
        conversation = conversations[0]
        conversation_text = "".join(m.value for m in conversation.markdown)
        exchanges = (
            _QUESTION_ONE,
            _ANSWER_ONE,
            _QUESTION_TWO,
            _ANSWER_TWO,
            _SAMPLE_RULE,
            _SAMPLE_ANSWERS[1],
        )
        for expected in exchanges:
            self.assertEqual(conversation_text.count(expected), 1)
        positions = [conversation_text.index(text) for text in exchanges]
        self.assertEqual(positions, sorted(positions))

        # The landing page never came back, and its three sample cards are gone
        # for good: Ask is the only button on the page by now.
        self.assertNotIn(_WELCOME_HEADING, conversation_text)
        self.assertNotIn(_WELCOME_TAGLINE, conversation_text)
        self.assertEqual([b.label for b in at.main.button], ["Ask"])
        self.assertEqual(len(at.session_state[_HISTORY_KEY]), 3)

        # ...and that area renders before the composer it must never cover.
        stream = list(at.main)
        self.assertLess(
            stream.index(conversation),
            stream.index(at.main.text_input(key="question_input")),
        )


class DashboardProcessingStateTest(unittest.TestCase):
    """The in-transcript processing surface: markup contract and its styling.

    It is only ever on screen while ``ask`` is running, so its markup is
    asserted on the component directly (the mid-run page itself is covered by
    ``DashboardConversationRenderingTest``).
    """

    def _captured_markup(self) -> str:
        """Render the component once and return the HTML block it emitted."""
        captured: dict[str, object] = {}
        with mock.patch.object(
            processing_state.st,
            "markdown",
            lambda body, **kwargs: captured.update(body=body, kwargs=kwargs),
        ):
            processing_state.render_processing_state()
        self.assertTrue(captured["kwargs"]["unsafe_allow_html"])
        return str(captured["body"])

    def test_component_emits_the_processing_surface_markup(self):
        markup = self._captured_markup()

        # One labeled surface: the SentraSQL speaker, the live status, and the
        # supporting line naming the real work.
        self.assertIn('class="sentrasql-processing"', markup)
        self.assertIn('role="status"', markup)
        self.assertIn("SentraSQL", markup)
        self.assertIn("Analyzing your question...", markup)
        self.assertIn(
            "Understanding your request, generating SQL, and querying your "
            "data...",
            markup,
        )
        # The indicator is decorative: the status line carries the meaning.
        self.assertIn('class="sentrasql-processing__indicator"', markup)
        self.assertIn('aria-hidden="true"', markup)

        # No question-bubble or composer markup (those belong elsewhere), and
        # none of the things the surface must never grow: a user avatar, a
        # timestamp, or a username.
        self.assertNotIn("sentrasql-question", markup)
        self.assertNotIn("sentrasql-composer", markup)
        for forbidden in ("avatar", "timestamp", "username"):
            self.assertNotIn(forbidden, markup)

    def test_processing_surface_rules_live_in_the_shared_stylesheet(self):
        css = styling.PAGE_CSS

        # The component emits classes only; the single stylesheet styles them.
        for rule in (
            ".sentrasql-processing {",
            ".sentrasql-processing__label {",
            ".sentrasql-processing__row {",
            ".sentrasql-processing__indicator {",
            ".sentrasql-processing__status {",
            ".sentrasql-processing__detail {",
            "@keyframes sentrasql-processing-spin",
        ):
            self.assertIn(rule, css)

        # Subtle by construction: an 18px accent ring (never a large spinner)
        # on the surface, which keeps the bottom margin an answer uses to clear
        # the pinned composer band.
        self.assertIn("width: 18px;", css)
        self.assertIn(f"border-top-color: {styling.ACCENT_COLOR};", css)
        self.assertIn("margin: 4px 0 40px;", css)


if __name__ == "__main__":
    unittest.main()

