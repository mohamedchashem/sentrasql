"""Processing state component: the in-transcript "working on it" surface.

Rendered inside the conversation area, directly beneath the question that was
just submitted, for as long as ``dashboard.graph_client.ask`` is still running.
That placement is the whole point of this component: the wait is shown exactly
where the answer will appear -- above the pinned composer, in the transcript --
instead of as a stray status message rendered underneath the composer, where a
user would have to scroll to discover that their question is being processed.

The surface is deliberately lightweight: a white response panel (hairline
border, soft shadow, 14px radius) holding the "SentraSQL" speaker label, one
small accent ring as the only motion, the live status line, and the supporting
line naming the real work being done (understanding the request, generating
SQL, querying the data). It is not a bubble, not a composer, not a spinner, and
not a modal: no dark background, no overlay, no blocking UI.

The module exposes one render function and emits semantic
``sentrasql-processing`` markup only -- every colour, size, spacing, and
animation lives in ``dashboard.styling.PAGE_CSS`` -- and it holds no state and
no widget, so it adds nothing to the page's widget contract.
"""

from __future__ import annotations

import streamlit as st

# The speaker label above the status line: the same "SentraSQL" the answers in
# the transcript belong to.
_LABEL = "SentraSQL"

# What is happening right now, and the concrete work behind it. Both lines are
# static copy: the processing surface represents the real execution lifecycle
# (one ask() run), it never claims a step that is not actually in progress.
_STATUS = "Analyzing your question..."
_DETAIL = (
    "Understanding your request, generating SQL, and querying your data..."
)


def render_processing_state() -> None:
    """Draw the processing surface for the exchange submitted this run.

    The block is marked as a polite live status region so assistive technology
    announces the work in progress, while the ring itself is decorative
    (``aria-hidden``) and only ever animates through CSS.
    """
    st.markdown(
        '<div class="sentrasql-processing" role="status">'
        f'<p class="sentrasql-processing__label">{_LABEL}</p>'
        '<div class="sentrasql-processing__row">'
        '<span class="sentrasql-processing__indicator" '
        'aria-hidden="true"></span>'
        '<div class="sentrasql-processing__text">'
        f'<p class="sentrasql-processing__status">{_STATUS}</p>'
        f'<p class="sentrasql-processing__detail">{_DETAIL}</p>'
        "</div></div></div>",
        unsafe_allow_html=True,
    )
