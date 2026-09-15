"""Capability card component: the "What can you ask?" scope card.

Rendered once, on the landing page only (below the example-question cards, above
the composer), this is the large premium white card that tells a first-time
visitor what the analytics can actually answer. It has two rows: the intro plus
four capability columns ("Sales & Revenue", "Products", "Customers",
"Time & Geography", each icon -> title -> description), then a dataset metadata
row (Dataset / Time range / Size / Countries) ending in a "View details"
control.

Everything here is one static ``st.markdown`` HTML block, deliberately: the card
holds no state and no interaction beyond a disclosure, so it must not add a
single Streamlit widget to the page. That keeps the page's widget contract
exactly as the rest of the app (and the AppTest suite) expects -- the sidebar
still exposes exactly one button, the main area still exposes exactly the three
sample-question buttons plus Ask, and the landing page renders no expander at
all -- while "View details" is a real, keyboard-accessible control, implemented
as a native ``<details>``/``<summary>`` element (confirmed to survive
Streamlit's markdown sanitiser) whose panel expands to show the detailed
dataset scope and limitations. Those details live HERE and only here: the
landing page no longer renders the old duplicate "What can I ask about?"
accordion.

All visual rules (grid columns, separators, card surface, metadata typography,
the summary button and its panel) live in ``dashboard.styling.PAGE_CSS``; this
module only emits semantic ``sentrasql-*`` markup, and every colour it needs is
read from ``dashboard.styling`` rather than restated here.
"""

from __future__ import annotations

import html

import streamlit as st

from dashboard import styling

# Section copy.
_TITLE = "What can you ask?"
_DESCRIPTION = (
    "Explore sales, products, customers and time-based insights from the "
    "Online Retail II dataset."
)

# The four capabilities, in display order: title, one-line description, and the
# outline icon path drawn beside them. One coherent blue/purple accent for every
# icon -- capabilities are deliberately NOT color-coded.
_CAPABILITIES = (
    (
        "Sales & Revenue",
        "Total revenue, quantities, average prices and transaction counts",
        "<line x1='18' y1='20' x2='18' y2='10'/>"
        "<line x1='12' y1='20' x2='12' y2='4'/>"
        "<line x1='6' y1='20' x2='6' y2='14'/>",
    ),
    (
        "Products",
        "Product performance, stock codes, descriptions and categories",
        "<path d='M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 "
        "3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z'/>"
        "<polyline points='3.27 6.96 12 12.01 20.73 6.96'/>"
        "<line x1='12' y1='22.08' x2='12' y2='12'/>",
    ),
    (
        "Customers",
        "Customer spending, unique customers and country-level activity",
        "<path d='M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2'/>"
        "<circle cx='9' cy='7' r='4'/>"
        "<path d='M23 21v-2a4 4 0 0 0-3-3.87'/>"
        "<path d='M16 3.13a4 4 0 0 1 0 7.75'/>",
    ),
    (
        "Time & Geography",
        "Daily, monthly or yearly analysis across 43 countries",
        "<circle cx='12' cy='12' r='10'/>"
        "<line x1='2' y1='12' x2='22' y2='12'/>"
        "<path d='M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 "
        "0 1-4-10 15.3 15.3 0 0 1 4-10z'/>",
    ),
)

# The dataset metadata row: icon body, label, value.
_METADATA = (
    (
        "<ellipse cx='12' cy='5' rx='9' ry='3'/>"
        "<path d='M21 12c0 1.66-4 3-9 3s-9-1.34-9-3'/>"
        "<path d='M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5'/>",
        "Dataset",
        "UCI Online Retail II",
    ),
    (
        "<rect x='3' y='4' width='18' height='18' rx='2'/>"
        "<line x1='16' y1='2' x2='16' y2='6'/>"
        "<line x1='8' y1='2' x2='8' y2='6'/>"
        "<line x1='3' y1='10' x2='21' y2='10'/>",
        "Time range",
        "Dec 1, 2009 – Dec 9, 2011",
    ),
    (
        "<line x1='8' y1='6' x2='21' y2='6'/>"
        "<line x1='8' y1='12' x2='21' y2='12'/>"
        "<line x1='8' y1='18' x2='21' y2='18'/>"
        "<line x1='3' y1='6' x2='3.01' y2='6'/>"
        "<line x1='3' y1='12' x2='3.01' y2='12'/>"
        "<line x1='3' y1='18' x2='3.01' y2='18'/>",
        "Size",
        "~1.07M transaction rows (after preprocessing)",
    ),
    (
        "<path d='M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z'/>"
        "<circle cx='12' cy='10' r='3'/>",
        "Countries",
        "43 countries (primarily United Kingdom)",
    ),
)

# The "View details" disclosure: its button label plus the detailed dataset
# scope it reveals. These entries are the single home of the dataset's scope and
# limitation facts -- the facts that previously sat in a second, legacy
# "What can I ask about?" expander on the landing page. They were moved here
# rather than dropped (nothing is lost), and they are presented exactly once,
# behind this control, instead of twice on the same page.
_VIEW_DETAILS_LABEL = "View details"
_DETAILS = (
    ("Grain", "One row per individual order line item."),
    (
        "Time range",
        "December 2009 through December 2011, grouped daily, monthly or "
        "yearly.",
    ),
    (
        "Measures",
        "Answers are measured by revenue, quantity, and average price.",
    ),
    (
        "Coverage",
        "43 countries, mostly the United Kingdom — products (stock code and "
        "description), and customers including guest/unidentified purchases.",
    ),
    (
        "Limitations",
        "Current limits: no comparisons between time periods, and no data "
        "outside December 2009 through December 2011.",
    ),
    (
        "Not yet supported",
        "There is no export or report generation yet.",
    ),
)


def _icon(body: str, size: int) -> str:
    """Return one inline outline ``<svg>`` glyph for the card.

    The glyph is written into the card's HTML (rather than used as a CSS
    background) because the card is markup, and the brand accent is read from
    ``styling.ICON_COLOR`` so no color is restated in this module.
    """
    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{size}' "
        f"height='{size}' viewBox='0 0 24 24' fill='none' "
        f"stroke='{styling.ICON_COLOR}' stroke-width='2' "
        "stroke-linecap='round' stroke-linejoin='round' aria-hidden='true' "
        f"focusable='false'>{body}</svg>"
    )


def _capability_html(title: str, description: str, icon_body: str) -> str:
    """One capability column: icon, title, description."""
    return (
        '<div class="sentrasql-capability__item">'
        f"{_icon(icon_body, 27)}"
        f"<h3>{html.escape(title, quote=False)}</h3>"
        f"<p>{html.escape(description, quote=False)}</p>"
        "</div>"
    )


def _metadata_html(icon_body: str, label: str, value: str) -> str:
    """One metadata cell: outline icon beside a bold label over a muted value."""
    return (
        '<div class="sentrasql-meta">'
        f"{_icon(icon_body, 18)}"
        '<div class="sentrasql-meta__text">'
        f'<span class="sentrasql-meta__label">{html.escape(label)}</span>'
        f'<span class="sentrasql-meta__value">'
        f"{html.escape(value, quote=False)}</span>"
        "</div></div>"
    )


def _details_html() -> str:
    """The "View details" disclosure and the dataset-scope panel it expands."""
    entries = "".join(
        '<dl class="sentrasql-details__entry">'
        f"<dt>{html.escape(label)}</dt>"
        f"<dd>{html.escape(value, quote=False)}</dd>"
        "</dl>"
        for label, value in _DETAILS
    )
    return (
        '<details class="sentrasql-details">'
        f"<summary>{html.escape(_VIEW_DETAILS_LABEL)}</summary>"
        f'<div class="sentrasql-details__panel">{entries}</div>'
        "</details>"
    )


def render_capability_card() -> None:
    """Draw the "What can you ask?" capability card as one static HTML block."""
    capabilities = "".join(
        _capability_html(title, description, icon_body)
        for title, description, icon_body in _CAPABILITIES
    )
    metadata = "".join(
        _metadata_html(icon_body, label, value)
        for icon_body, label, value in _METADATA
    )
    st.markdown(
        '<section class="sentrasql-capability">'
        '<div class="sentrasql-capability__row">'
        '<div class="sentrasql-capability__intro">'
        f"<h2>{html.escape(_TITLE)}</h2>"
        f"<p>{html.escape(_DESCRIPTION)}</p>"
        "</div>"
        f"{capabilities}</div>"
        '<div class="sentrasql-capability__meta">'
        f"{metadata}{_details_html()}</div>"
        "</section>",
        unsafe_allow_html=True,
    )
