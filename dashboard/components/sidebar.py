"""Sidebar component: brand logo, tagline, and New Chat button.

Renders the app's ``st.sidebar`` contents -- the real ``sentrasql-logo.svg``
brand mark (the actual asset file, inlined, never recreated as styled text), a
two-line tagline, and the full-width "New Chat" button -- and returns whether
New Chat was pressed on this run so ``app.py`` can wipe the conversation. All
sidebar markup lives here so ``app.py`` never touches ``st.sidebar`` directly,
keeping the one-file-per-component convention intact. The markup is semantic
only; the dark navy surface, logo sizing, tagline typography, the accent plus
icon, and the button styling all live in ``dashboard.styling``. The logo,
tagline, and button are all inset by the same sidebar gutter in that stylesheet,
which is what keeps the button's edges aligned with the brand text above it
without any hand-computed width.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

# The project's real SVG brand mark, read once at import and inlined so the
# logo is the actual asset file rather than recreated text. The path resolves
# from this file, so it works no matter where streamlit is launched.
_LOGO_PATH = (
    Path(__file__).resolve().parent.parent.parent / "assets" / "sentrasql-logo.svg"
)
_LOGO_SVG = _LOGO_PATH.read_text(encoding="utf-8")

# Two-line tagline shown directly under the logo (line break kept in markup).
_TAGLINE = "Ask your data.<br>Get verified answers."

# "New Chat" clears the current conversation (same session, just wiped clean).
_NEW_CHAT_LABEL = "New Chat"
_NEW_CHAT_KEY = "new_chat_button"


def render_sidebar() -> bool:
    """Render the sidebar contents; return whether "New Chat" was pressed.

    Draws, top to bottom: the brand logo (the real SVG asset), a two-line
    tagline, and a full-width New Chat button. Returns ``True`` exactly on the
    run where New Chat is clicked, so ``app.py`` can reset the chat history to
    an empty list and start a fresh conversation in the same session.
    """
    st.sidebar.markdown(
        f'<div class="sentrasql-sidebar-logo">{_LOGO_SVG}</div>',
        unsafe_allow_html=True,
    )
    st.sidebar.markdown(
        f'<p class="sentrasql-sidebar-tagline">{_TAGLINE}</p>',
        unsafe_allow_html=True,
    )
    new_chat = st.sidebar.button(
        _NEW_CHAT_LABEL,
        key=_NEW_CHAT_KEY,
        width="stretch",
    )
    return new_chat
