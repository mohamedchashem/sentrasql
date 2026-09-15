"""Shared visual constants and the single global stylesheet for the dashboard UI.

The single home of every color, gradient, icon, and repeated visual rule the
dashboard renders, so components pull from one place instead of hardcoding
values across files. ``app.py`` injects ``PAGE_CSS`` once at the top of the
page; every visual rule that reaches into Streamlit's own widget chrome
(borderless pill input, send-icon button, sample-question cards, pinned
composer, dark sidebar) lives here as CSS, while components only ever emit their
own semantic HTML (classes such as ``sentrasql-welcome`` / ``sentrasql-answer``)
that this sheet styles. This module holds no Streamlit state and no feature
logic: constants plus one stylesheet.

**Everything here is responsive by construction.** No rule encodes a
screenshot's pixel geometry: the sidebar is ``clamp(240px, 18vw, 280px)``, the
content column is ``min(1180px, 100%)`` with ``clamp()`` gutters, type sizes are
``clamp()`` values, spacing is ``clamp()``/``vh``-based, and every multi-column
block (sample cards, capability columns, metadata row) collapses through media
queries. The design therefore holds at 1366x768, 1440x900, 1536x864, 1920x1080,
2560x1440, a resized desktop window, and under browser zoom alike.

Layout notes -- why the structural rules are written the way they are (verified
against Streamlit 1.63's real DOM, not assumed):

* The page does not scroll at the ``<body>`` level. Streamlit 1.63 renders a
  fixed-height scroll container as ``<section data-testid="stMain">``
  (``overflow-y: auto``) that contains the whole main column, so anything that
  must "not scroll with the conversation" has to be pinned against *that*
  scrollport, not ``position: fixed`` on ``body``.
* ``position: fixed`` is therefore the wrong tool here: a fixed element is
  relative to the *window* viewport and would need hand-computed left/right
  offsets duplicating the sidebar width and centered-column gutters, breaking
  when the sidebar collapses. ``position: sticky`` inside
  ``[data-testid="stMain"]`` is pinned by the framework's own scrollport and
  needs no manual offsets.
* Sticky only travels inside its parent (containing block), so the composer's
  sticky node must be the layout wrapper that holds the input columns -- never a
  styled ``<div>`` we authored, whose parent is only as tall as the element
  itself. The selectors below target those framework wrappers via ``:has()``.
* ``position: sticky; bottom: 0`` alone floats the composer mid-page on short
  content (the landing page). The main block container and its root vertical
  block are therefore laid out as a full-height flex column and the composer
  wrapper gets ``margin-top: auto``: short content pushes the bar to the bottom
  of the viewport, and once the transcript outgrows the viewport the bar pins to
  the scrollport bottom while messages scroll beneath it.
* The landing page's translucent background shapes are drawn as
  ``::before`` / ``::after`` on ``[data-testid="stMain"]`` itself. Because they
  are pseudo-elements of the scrollport, their containing block is guaranteed to
  be that scrollport (no dependency on which framework wrapper happens to be
  positioned), they cannot affect layout at all, and ``overflow-x: hidden`` on
  the same node guarantees they can never introduce a horizontal scrollbar.
* The sidebar's single content gutter is the custom property
  ``--sentrasql-sidebar-gutter``, applied identically to the logo, the tagline,
  and the New Chat button's own container. Because all three are siblings under
  the same framework ancestors, giving them the *same* padding keeps their inner
  edges aligned no matter what padding the framework adds around them -- this is
  the structural fix for the previously overflowing/misaligned New Chat button
  (the button is never given a hand-computed width, only ``width: 100%`` of an
  already-inset box plus ``box-sizing: border-box``).
* Colour tokens are substituted from the constants above, so a palette change
  edits exactly one place. Tokens are uppercase names delimited by ``@`` and are
  replaced longest-name-first, so a token can never be a prefix of another.
"""

from __future__ import annotations

from urllib.parse import quote

# --- Brand / palette --------------------------------------------------------
# One hex per role lives here; nothing else in the codebase hardcodes a color.
NAVY_950 = "#07142F"
NAVY_900 = "#0B1733"

# The main content canvas is a very light cool white, never pure white; white
# surfaces (composer pill, sample/capability cards) sit on top of it.
PAGE_BACKGROUND = "#F8FAFF"
SURFACE_COLOR = "#FFFFFF"
SURFACE_ICON_COLOR = "#EEF1FF"

# Hairline borders: the standard card/divider edge and a slightly stronger
# variant for controls that should read as a button ("View details").
BORDER_COLOR = "rgba(90, 110, 160, 0.14)"
BORDER_STRONG_COLOR = "rgba(80, 100, 160, 0.18)"

# Type ramp: near-black navy headings, blue-gray secondary copy, quieter muted
# labels.
HEADING_COLOR = "#102044"
TEXT_COLOR = "#14213D"
SECONDARY_TEXT_COLOR = "#66779A"
MUTED_TEXT_COLOR = "#8794AE"

# One coherent blue/purple brand accent (buttons, gradients, icons, eyebrows).
ACCENT_COLOR = "#4F6BFF"
ACCENT_HOVER_COLOR = "#633BFF"
ACCENT_SOFT_COLOR = "#EEF1FF"
ICON_COLOR = "#4D5FFF"
BRAND_CYAN = "#2EA7FF"
BRAND_BLUE = "#397CFF"
BRAND_PURPLE = "#7147F5"
CONTROL_TEXT_COLOR = "#26365A"

# Neutral chat-bubble fill for user messages (soft lavender on the canvas).
BUBBLE_BACKGROUND = "#EDF0FF"

# Composer type colours: the placeholder line and, one step quieter, the
# secondary example line under it (both sit inside the same text block).
COMPOSER_PLACEHOLDER_COLOR = "#7A88A4"
COMPOSER_HINT_COLOR = "#8A96B0"

# Error tone for the "couldn't answer" state; distinct from the accent so a
# failure is readable at a glance while the copy stays consistent.
ERROR_COLOR = "#DC2626"
ERROR_COLOR_SOFT = "#FEF2F2"

# One primary modern sans-serif family, used everywhere (the same stack is
# declared in .streamlit/config.toml via [theme] font, which also loads Inter).
FONT_STACK = (
    '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, '
    "sans-serif"
)

# Eyebrow labels rendered at the top of each answer block.
ANSWER_EYEBROW = "ANSWER"
ERROR_EYEBROW = "COULDN'T ANSWER"
HINT_EYEBROW = "READY WHEN YOU ARE"

# Colour tokens are substituted from the constants above so a palette change
# edits exactly one place. Tokens are uppercase names delimited by ``@``.
_CSS_TOKENS = {
    "ACCENT": ACCENT_COLOR,
    "ACCENT_HOVER": ACCENT_HOVER_COLOR,
    "ACCENT_SOFT": ACCENT_SOFT_COLOR,
    "PAGE_BG": PAGE_BACKGROUND,
    "SURFACE_ICON": SURFACE_ICON_COLOR,
    "SURFACE": SURFACE_COLOR,
    "BORDER_STRONG": BORDER_STRONG_COLOR,
    "BORDER": BORDER_COLOR,
    "TEXT_SECONDARY": SECONDARY_TEXT_COLOR,
    "TEXT": TEXT_COLOR,
    "HEADING": HEADING_COLOR,
    "MUTED": MUTED_TEXT_COLOR,
    "BUBBLE": BUBBLE_BACKGROUND,
    "COMPOSER_PLACEHOLDER": COMPOSER_PLACEHOLDER_COLOR,
    "COMPOSER_HINT": COMPOSER_HINT_COLOR,
    "ERROR_SOFT": ERROR_COLOR_SOFT,
    "ERROR": ERROR_COLOR,
    "NAVY_950": NAVY_950,
    "NAVY_900": NAVY_900,
    "ICON": ICON_COLOR,
    "BRAND_CYAN": BRAND_CYAN,
    "BRAND_BLUE": BRAND_BLUE,
    "BRAND_PURPLE": BRAND_PURPLE,
    "CONTROL_TEXT": CONTROL_TEXT_COLOR,
    "FONT": FONT_STACK,
}

def _svg_data_uri(svg: str) -> str:
    """Encode a raw SVG document as a percent-encoded ``data:image/svg+xml`` URI.

    Encoding every non-alphanumeric character (``safe=""``) leaves the result
    free of ``#``, quotes, and whitespace, so it drops straight into a
    double-quoted CSS ``url("...")`` with no escaping surprises. The SVGs below
    stay human-readable while the URI is generated at import time.
    """
    return "data:image/svg+xml," + quote(svg, safe="")


def _outline_icon(body: str, size: int = 24, stroke: str = ICON_COLOR) -> str:
    """Wrap ``body`` in a Feather-style outline ``<svg>`` (2px round stroke).

    One shared wrapper keeps every glyph in the sheet visually identical: the
    same 24-unit viewBox, the same 2px stroke with round caps/joins, and no fill,
    so the icons read as a single minimal outline set rather than a mixed bag.
    """
    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{size}' height='{size}' "
        f"viewBox='0 0 24 24' fill='none' stroke='{stroke}' stroke-width='2' "
        "stroke-linecap='round' stroke-linejoin='round'>"
        f"{body}</svg>"
    )


# Crisp paper-plane (Feather "send") drawn with a 2px white stroke so it reads
# sharply against the accent gradient behind the circular button.
_SEND_ICON_SVG = _svg_data_uri(
    _outline_icon(
        "<line x1='22' y1='2' x2='11' y2='13'/>"
        "<polygon points='22 2 15 22 11 13 2 9 22 2'/>",
        stroke="white",
    )
)

# The three sample-card line icons (bar chart / price tag / people), each the
# same 2px-stroke outline glyph in the brand indigo.
_CARD_ICON_BAR = _svg_data_uri(
    _outline_icon(
        "<line x1='18' y1='20' x2='18' y2='10'/>"
        "<line x1='12' y1='20' x2='12' y2='4'/>"
        "<line x1='6' y1='20' x2='6' y2='14'/>"
    )
)
_CARD_ICON_TAG = _svg_data_uri(
    _outline_icon(
        "<path d='M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 "
        "8.59a2 2 0 0 1 0 2.83z'/>"
        "<line x1='7' y1='7' x2='7.01' y2='7'/>"
    )
)
_CARD_ICON_USERS = _svg_data_uri(
    _outline_icon(
        "<path d='M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2'/>"
        "<circle cx='9' cy='7' r='4'/>"
        "<path d='M23 21v-2a4 4 0 0 0-3-3.87'/>"
        "<path d='M16 3.13a4 4 0 0 1 0 7.75'/>"
    )
)

# Small white plus glyph for the sidebar's New Chat button, and the two
# chevrons used by the sample cards (pointing right, "this is clickable") and
# the capability card's "View details" disclosure (pointing down, rotating when
# its panel is open).
_PLUS_ICON_SVG = _svg_data_uri(
    _outline_icon(
        "<line x1='12' y1='5' x2='12' y2='19'/>"
        "<line x1='5' y1='12' x2='19' y2='12'/>",
        size=22,
        stroke="white",
    )
)
_CHEVRON_RIGHT_SVG = _svg_data_uri(
    _outline_icon(
        "<polyline points='9 18 15 12 9 6'/>",
        size=20,
        stroke="#8794AE",
    )
)
_CHEVRON_DOWN_SVG = _svg_data_uri(
    _outline_icon(
        "<polyline points='6 9 12 15 18 9'/>",
        size=16,
        stroke="#52648F",
    )
)

_PAGE_CSS_TEMPLATE = """<style>
@import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap");

/* ---------------------------------------------------------------------------
   1. Design tokens and the page canvas
   --------------------------------------------------------------------------- */
:root {
    color-scheme: light;
    --navy-950: @NAVY_950;
    --navy-900: @NAVY_900;
    --text-primary: @TEXT;
    --text-heading: @HEADING;
    --text-secondary: @TEXT_SECONDARY;
    --text-muted: @MUTED;
    --brand-blue: @ACCENT;
    --brand-cyan: @BRAND_CYAN;
    --brand-purple: @ACCENT_HOVER;
    --surface: @SURFACE;
    --surface-soft: @PAGE_BG;
    --surface-icon: @SURFACE_ICON;
    --border: @BORDER;
    --font-sans: @FONT;
}

/* Streamlit 1.63 renamed the old ``.stApp`` node to
   ``[data-testid="stAppViewContainer"]``, so the chrome override targets the
   testid rather than the retired class name. Everything outside the content
   canvas shares the canvas colour so no white strip ever flashes at the edges. */
html, body, #root, [data-testid="stAppViewContainer"] { background: @PAGE_BG; }

/* Main content canvas: Streamlit 1.63's ``.main`` node is now the
   ``[data-testid="stMain"]`` scrollport. An airy, luminous surface -- cool
   white with two soft blue/violet radial glows and a very light diagonal
   wash -- never a flat white and never a saturated tint.
   ``overflow-x: hidden`` is set here (and only here) so the decorative layers
   below can sit past the right/left edges without ever creating a horizontal
   scrollbar. */
[data-testid="stMain"] {
    position: relative;
    overflow-x: hidden;
    font-family: @FONT;
    background:
        radial-gradient(circle at 15% 40%, rgba(91, 154, 255, 0.12), transparent 32%),
        radial-gradient(circle at 90% 45%, rgba(130, 83, 255, 0.10), transparent 35%),
        linear-gradient(135deg, #FFFFFF 0%, @PAGE_BG 55%, #F4F2FF 100%);
}

/* Decorative translucent geometry around the edges. Purely atmospheric: both
   layers are pseudo-elements of the scrollport (so they cannot affect layout or
   depend on which framework wrapper is positioned), are painted beneath the
   content (see ``z-index`` in section 2), never accept pointer events, and are
   clipped by the scrollport's ``overflow-x: hidden``. */
[data-testid="stMain"]::before,
[data-testid="stMain"]::after {
    content: "";
    position: absolute;
    z-index: 0;
    pointer-events: none;
}

/* Right-hand stack: one large rotated glass panel with a soft blue -> violet
   wash, a hairline light edge, and a very subtle blur. */
[data-testid="stMain"]::before {
    top: clamp(24px, 6vh, 96px);
    right: clamp(-190px, -7vw, -48px);
    width: clamp(220px, 26vw, 430px);
    aspect-ratio: 1 / 1;
    border-radius: clamp(28px, 3vw, 56px);
    transform: rotate(-16deg);
    background:
        radial-gradient(closest-side, rgba(99, 59, 255, 0.16), rgba(99, 59, 255, 0) 100%),
        linear-gradient(140deg, rgba(46, 167, 255, 0.15) 0%, rgba(79, 107, 255, 0.09) 45%, rgba(113, 71, 245, 0.13) 100%);
    border: 1px solid rgba(255, 255, 255, 0.55);
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.6);
    filter: blur(0.4px);
    opacity: 0.85;
}

/* Lower-left counterweight: a wide, soft ellipse blurred well past legibility
   so it reads as light in the surface rather than as a shape. It is positioned
   from the top and sized to end just inside the scrollport (no rotation, no
   negative bottom), so it can never extend the page's scrollable height. */
[data-testid="stMain"]::after {
    top: 56%;
    left: clamp(-240px, -16vw, -80px);
    width: clamp(320px, 44vw, 680px);
    height: 42%;
    border-radius: 50%;
    background: linear-gradient(120deg, rgba(91, 154, 255, 0.11), rgba(130, 83, 255, 0.06));
    filter: blur(8px);
}

/* Remove the browser-default focus outline streamlit leaves on some wrappers. */
[data-testid="stAppViewContainer"]:focus { outline: none; }

/* Hide the Deploy button, the three-dot menu, the status widget, and the whole
   top utility strip on desktop: the brand identity lives in the sidebar, so a
   second branded header above the hero would be redundant. Config
   (client.toolbarMode / ui.hideTopBar) already removes most of these; these
   rules are the belt-and-braces layer for whatever DOM remains. The strip is
   deliberately re-shown on small screens (section 8), where it carries the only
   sidebar toggle a phone/tablet user gets. */
[data-testid="stToolbar"],
[data-testid="stMainMenu"],
[data-testid="stAppDeployButton"],
[data-testid="stStatusWidget"],
[data-testid="stHeader"] { display: none !important; }

/* ---------------------------------------------------------------------------
   2. Scroll-area structure and the centered content container
   --------------------------------------------------------------------------- */

/* The main column's centered, width-constrained content container. Streamlit
   1.63 keeps the ``block-container`` class but also tags the node
   ``[data-testid="stMainBlockContainer"]``; the override uses the testid so it
   keeps working if the class is ever retired. The selector is scoped to
   ``[data-testid="stMain"]`` so these rules apply ONLY to the main content
   block -- ``stMain`` is a sibling of the sidebar under the app container, so
   the sidebar (``[data-testid="stSidebar"]``) is never matched. One shared,
   centered column for the hero, cards, capability card, composer, and
   transcript, with symmetric responsive gutters instead of full-bleed
   stretching.

   ``position: relative; z-index: 1`` (not a hardcoded pixel value) is what puts
   the whole content column in front of the decorative background layers
   declared in section 1: every positioned descendant -- including the sticky
   composer -- then paints above them, whatever the viewport size. */
[data-testid="stMain"] [data-testid="stMainBlockContainer"] {
    position: relative;
    z-index: 1;
    box-sizing: border-box;
    width: 100%;
    max-width: 1180px !important;
    margin-left: auto !important;
    margin-right: auto !important;
    padding-top: 0 !important;
    padding-bottom: 0 !important;
    padding-left: clamp(20px, 3vw, 48px) !important;
    padding-right: clamp(20px, 3vw, 48px) !important;
    display: flex;
    flex-direction: column;
    min-height: 100%;
}

/* The root vertical block fills the container as a flex column so the composer
   can be pushed to the bottom of the viewport on the landing page (see the
   module docstring). */
[data-testid="stMain"] [data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] {
    flex: 1 1 auto;
    display: flex;
    flex-direction: column;
    min-width: 0;
}

/* Column wrappers hold the sample-card row and the composer row, so they need
   the same stacking treatment as the element containers beside them. */
[data-testid="stMain"] [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"] {
    position: relative;
    z-index: 1;
}

/* Consistent text rhythm across the main column. */
[data-testid="stMain"] [data-testid="stVerticalBlock"] { row-gap: 0; gap: 0; }
[data-testid="stMain"] p { line-height: 1.6; }

/* ---------------------------------------------------------------------------
   3. Composer: ONE unified, self-contained question pill.

   The framework nests this as:

     stLayoutWrapper (sticky bottom band, fading into the canvas)
       stHorizontalBlock          <- THE PILL: surface, border, radius, shadow,
                                     padding, blur, focus ring
         stColumn (first)         <- TEXT AREA: flex column, centered block
           stTextInput            -> fully transparent, unstyled input
           stMarkdown             -> the secondary example line
         stColumn (last)          <- SEND BUTTON: fixed 52px flex child

   Responsibilities are split exactly that way: the pill owns the appearance,
   the text area owns the text layout, and the send button is an independent
   flex child, so nothing inside the pill draws its own rectangle. Every
   framework wrapper inside the text area is flattened (no background, border,
   shadow, outline, margins, or fixed height) and the focus indication belongs
   to the pill via ``:focus-within``, never to the input.

   Geometry notes (this is the fix for the broken composer):
   * The pill uses ``min-height`` and ``height: auto``, never a rigid height, so
     it always grows to fit its contents instead of letting the example line
     spill out; ``overflow: hidden`` makes escaping impossible either way.
   * ``flex-wrap: nowrap`` on the pill guarantees the send button can never drop
     below the text area onto a second line.
   * The text area keeps ``min-width: 0`` at every breakpoint so the example
     line wraps to a second line on narrow screens instead of pushing the send
     button out of the pill.
   --------------------------------------------------------------------------- */
[data-testid="stMain"] [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"]:has(.st-key-question_input) {
    position: sticky;
    bottom: 0;
    z-index: 800;
    margin-top: auto;
    padding: clamp(14px, 2.2vh, 24px) 0 clamp(16px, 2.6vh, 30px);
    background: linear-gradient(180deg, rgba(248, 250, 255, 0) 0%, @PAGE_BG 42%, @PAGE_BG 100%);
}

/* The pill. Width is 100% of the main content column: that column already
   enforces ``min(1180px, calc(100% - responsive gutters))`` (section 2), so the
   composer is centered within the MAIN CONTENT AREA (never the viewport) and
   can never overlap the sidebar -- without a second viewport-relative width
   here that would make the composer narrower than the hero and cards above it.
   Its responsive tiers (tablet/mobile padding and button size) live at the end
   of the sheet with the other breakpoints. */
[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-question_input) {
    box-sizing: border-box;
    display: flex;
    flex-wrap: nowrap;
    align-items: center;
    gap: 14px;
    width: 100%;
    max-width: 100%;
    margin-inline: auto;
    min-height: 76px;
    height: auto;
    padding: 10px 12px 10px 28px;
    border-radius: 38px;
    background: rgba(255, 255, 255, 0.96);
    border: none;
    box-shadow: 0 10px 35px rgba(60, 80, 130, 0.10);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    overflow: hidden;
    transition: border-color 0.18s ease, box-shadow 0.18s ease;
}

/* --- TEXT AREA (first column): flex:1, min-width:0, and both lines form ONE
   block that is vertically centered inside the pill rather than pinned to the
   top or bottom. --- */
[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-question_input) > [data-testid="stColumn"]:first-child {
    flex: 1 1 auto !important;
    width: auto !important;
    max-width: none !important;
    min-width: 0 !important;
    height: auto !important;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: stretch;
    margin: 0;
    padding: 0;
}
[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-question_input) > [data-testid="stColumn"]:first-child > [data-testid="stVerticalBlock"] {
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 0;
    margin: 0;
    padding: 0;
    min-width: 0;
    min-height: 0;
    height: auto !important;
}

/* --- SEND BUTTON (last column): a fixed-size flex child, vertically centered
   by the pill's own ``align-items`` (never absolute positioning). --- */
[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-question_input) > [data-testid="stColumn"]:last-child {
    flex: 0 0 auto !important;
    width: auto !important;
    max-width: none !important;
    min-width: 0 !important;
    height: auto !important;
    display: flex;
    align-items: center;
    justify-content: center;
    margin: 0;
    padding: 0;
}

/* Focus belongs to the WHOLE pill: when the input is focused the entire
   composer reacts, so the user never perceives a rectangular input inside it. */
[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-question_input):focus-within {
    border-color: rgba(79, 107, 255, 0.35);
    box-shadow: 0 10px 35px rgba(60, 80, 130, 0.10), 0 0 0 3px rgba(79, 107, 255, 0.06);
}

/* Every framework wrapper inside the text area is flattened: no background, no
   border, no shadow, no outline, no extra height, and no margins -- so nothing
   can render as a nested rectangle inside the pill and nothing can push the
   example line outside it. This deliberately reaches through BaseWeb's own
   input shell (``[data-baseweb]``), which is what draws the rectangular focus
   frame in the default Streamlit input. */
[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-question_input) > [data-testid="stColumn"]:first-child [data-testid="stVerticalBlock"],
[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-question_input) > [data-testid="stColumn"]:first-child [data-testid="stElementContainer"],
[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-question_input) > [data-testid="stColumn"]:first-child [data-testid="stMarkdown"],
[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-question_input) > [data-testid="stColumn"]:first-child [data-testid="stMarkdownContainer"],
[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-question_input) > [data-testid="stColumn"]:first-child [data-testid="stTextInput"],
[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-question_input) > [data-testid="stColumn"]:first-child [data-baseweb] {
    box-sizing: border-box;
    width: 100% !important;
    max-width: 100%;
    min-width: 0 !important;
    height: auto !important;
    min-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    outline: none !important;
}

/* The real input stays a real, fully functional Streamlit input; it is simply
   transparent, fills the text area, and is never allowed to draw its own
   border, background, or focus ring. */
[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-question_input) > [data-testid="stColumn"]:first-child input {
    box-sizing: border-box;
    display: block;
    width: 100% !important;
    min-width: 0;
    height: 28px;
    margin: 0;
    padding: 0;
    border: none !important;
    border-radius: 0;
    background: transparent !important;
    outline: none !important;
    box-shadow: none !important;
    font-family: @FONT;
    font-size: 16px;
    font-weight: 400;
    line-height: 1.4;
    color: @TEXT;
}
[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-question_input) > [data-testid="stColumn"]:first-child input::placeholder {
    color: @COMPOSER_PLACEHOLDER;
    opacity: 1;
}

/* The secondary example line: it is INSIDE the composer, directly beneath the
   placeholder, part of the same vertically-centered text block, and it wraps
   naturally (never nowrap/ellipsis, never absolutely positioned, never pulled
   upward with a negative margin). The ``[data-testid="stMain"]`` prefix keeps
   these values ahead of the main column's generic ``p`` line-height. */
[data-testid="stMain"] .sentrasql-composer-hint {
    margin: 3px 0 0 0;
    padding: 0;
    font-family: @FONT;
    font-size: 12px;
    font-weight: 400;
    line-height: 1.4;
    color: @COMPOSER_HINT;
    white-space: normal;
    overflow-wrap: anywhere;
}

[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-question_input) > [data-testid="stColumn"]:first-child *,
[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-question_input) > [data-testid="stColumn"]:first-child *:focus,
[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-question_input) > [data-testid="stColumn"]:first-child *:focus-visible,
[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-question_input) > [data-testid="stColumn"]:first-child *:focus-within {
    outline: none !important;
    box-shadow: none !important;
}

[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-question_input) > [data-testid="stColumn"]:first-child [data-baseweb],
[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-question_input) > [data-testid="stColumn"]:first-child [data-baseweb] *,
[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-question_input) > [data-testid="stColumn"]:first-child input {
    border: none !important;
    outline: none !important;
    box-shadow: none !important;
}

/* Send button: circular accent disc (brand gradient) with a crisp white
   paper-plane glyph. It is a fixed 52px flex child of the pill (52px -> 50px on
   tablet -> 48px on mobile, see the breakpoints at the end of the sheet), so it
   is vertically centered by the pill's own ``align-items`` and can never drift
   or escape; the icon is a 22px white glyph. The label text ("Ask") is kept in
   the DOM for accessibility and tests, but fully collapsed and replaced by the
   SVG glyph so it can never nudge the icon off-center. */
[data-testid="stMain"] .st-key-ask_button {
    flex: 0 0 52px !important;
    width: 52px !important;
    min-width: 52px !important;
    display: flex;
    align-items: center;
    justify-content: center;
    margin: 0;
    padding: 0;
}
[data-testid="stMain"] .st-key-ask_button button {
    box-sizing: border-box;
    width: 52px;
    height: 52px;
    min-width: 52px;
    min-height: 52px;
    flex: 0 0 52px;
    padding: 0;
    border-radius: 50%;
    border: none;
    display: flex;
    align-items: center;
    justify-content: center;
    background: linear-gradient(135deg, @ACCENT 0%, @ACCENT_HOVER 100%);
    color: #FFFFFF;
    box-shadow: 0 6px 18px rgba(79, 107, 255, 0.24);
    transition: filter 0.16s ease, box-shadow 0.16s ease;
}
[data-testid="stMain"] .st-key-ask_button button:hover,
[data-testid="stMain"] .st-key-ask_button button:focus-visible {
    background: linear-gradient(135deg, @ACCENT 0%, @ACCENT_HOVER 100%);
    border: none;
    filter: brightness(1.05);
    box-shadow: 0 8px 22px rgba(79, 107, 255, 0.34);
}
[data-testid="stMain"] .st-key-ask_button button:active { transform: translateY(1px); }
/* Collapse the button's label wrapper (Streamlit wraps the "Ask" text in a
   <div> that stays ~26px wide even after the inner <p> is font-size:0). That
   wrapper was the source of the icon's left-shift: it sat as a second flex item
   next to the ::before glyph, so justify-content:center centered the pair and
   pushed the icon off true center. Zero-size the wrapper (keeping the text in
   the accessibility tree) so only the glyph remains to be centered. */
[data-testid="stMain"] .st-key-ask_button button > div {
    width: 0;
    height: 0;
    min-width: 0;
    max-width: 0;
    overflow: hidden;
    padding: 0;
    margin: 0;
    border: none;
    flex: 0 0 0;
}
[data-testid="stMain"] .st-key-ask_button button p {
    font-size: 0;
    line-height: 0;
    margin: 0;
    width: 0;
    height: 0;
    overflow: hidden;
}
[data-testid="stMain"] .st-key-ask_button button::before {
    content: "";
    flex: 0 0 auto;
    width: 22px;
    height: 22px;
    background: url("_SEND_ICON_URL") center / contain no-repeat;
}

/* ---------------------------------------------------------------------------
   4. Hero: centered eyebrow, two-line headline, supporting line
   --------------------------------------------------------------------------- */
/* The hero block itself is a full-width, centered block; the description's
   centering is fixed at the CONTAINER level here, not compensated for with
   transforms or offsets. */
.sentrasql-welcome {
    position: relative;
    display: block;
    width: 100%;
    box-sizing: border-box;
    padding: clamp(34px, 7vh, 76px) 0 clamp(26px, 4.4vh, 44px);
    margin: 0;
    text-align: center;
}
/* Belt and braces: the framework element/markdown wrappers around the hero are
   centered too, so nothing in Streamlit's own markdown chrome can left-align
   the description paragraph underneath the headline. */
[data-testid="stMain"] [data-testid="stElementContainer"]:has(> [data-testid="stMarkdown"] .sentrasql-welcome) {
    width: 100% !important;
    max-width: none !important;
    align-self: stretch !important;
    text-align: center !important;
}

[data-testid="stMain"] [data-testid="stElementContainer"]:has(> [data-testid="stMarkdown"] .sentrasql-welcome) [data-testid="stMarkdownContainer"] {
    width: 100% !important;
    max-width: none !important;
    text-align: center !important;
}
.sentrasql-welcome__eyebrow {
    font-size: clamp(11.5px, 0.86vw, 14px);
    font-weight: 700;
    letter-spacing: 0.28em;
    text-transform: uppercase;
    color: @TEXT_SECONDARY;
    margin: 0 0 clamp(14px, 2.2vh, 22px);
}
.sentrasql-welcome__title {
    font-family: @FONT;
    font-size: clamp(38px, 4.2vw, 64px);
    line-height: 1.04;
    font-weight: 800;
    letter-spacing: -0.03em;
    color: @HEADING;
    margin: 0 0 clamp(14px, 2.2vh, 22px);
}
/* The supporting line under the headline: a full-width block capped at 760px
   and centered with auto margins, so its box and its text share exactly the
   same horizontal center axis as the heading above it. No transforms, no
   left/right offsets, no relative nudging. */
.sentrasql-welcome__tagline {
    display: block;
    box-sizing: border-box;
    width: min(760px, 100%);
    max-width: 760px;
    margin-left: auto !important;
    margin-right: auto !important;
    padding: 0;
    font-family: @FONT;
    font-size: 17px;
    font-weight: 400;
    line-height: 1.6;
    color: @TEXT_SECONDARY;
    text-align: center !important;
}
/* Shared blue/purple text gradient, applied to the eyebrow's "ANSWERS" and the
   headline's second line. background-clip: text + transparent fill paints the
   gradient through the glyphs only. */
.sentrasql-gradient-text {
    background: linear-gradient(90deg, @BRAND_CYAN 0%, @BRAND_BLUE 42%, @BRAND_PURPLE 100%);
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
    color: transparent;
}

/* ---------------------------------------------------------------------------
   5. Example question cards: three real buttons (so AppTest can click them and
      the pipeline runs the exact wording), laid out as a responsive grid.
      The framework row is 3 equal columns on desktop, 2 on medium widths, and 1
      on small screens -- the media queries in section 9 only change flex-basis,
      never card widths in pixels.
   --------------------------------------------------------------------------- */
[data-testid="stMain"] [data-testid="stLayoutWrapper"]:has(.st-key-sample_question_0) {
    margin-bottom: clamp(22px, 3.4vh, 36px);
}
[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-sample_question_0) {
    display: flex;
    flex-wrap: wrap;
    align-items: stretch;
    gap: clamp(12px, 1.4vw, 20px);
    background: transparent;
}
[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-sample_question_0) > [data-testid="stColumn"] {
    flex: 1 1 0 !important;
    min-width: 0 !important;
    width: auto !important;
    max-width: none !important;
    display: flex;
}
[data-testid="stMain"] [class*="st-key-sample_question_"] { display: flex; flex: 1 1 auto; min-width: 0; }

/* The card itself: translucent white surface, hairline border, soft shadow,
   icon chip on the left, question text in the middle, chevron on the right. */
[data-testid="stMain"] [class*="st-key-sample_question_"] button {
    width: 100%;
    height: 100%;
    min-height: 104px;
    display: flex;
    flex-direction: row;
    align-items: center;
    gap: clamp(12px, 1.2vw, 16px);
    text-align: left;
    background: rgba(255, 255, 255, 0.88);
    border: 1px solid @BORDER;
    border-radius: 16px;
    box-shadow: 0 8px 30px rgba(50, 70, 120, 0.07);
    padding: clamp(16px, 1.6vw, 22px);
    margin: 0;
    transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}
[data-testid="stMain"] [class*="st-key-sample_question_"] button:hover,
[data-testid="stMain"] [class*="st-key-sample_question_"] button:focus-visible {
    transform: translateY(-2px);
    border-color: rgba(79, 107, 255, 0.5);
    box-shadow: 0 14px 36px rgba(50, 70, 130, 0.14);
}
/* The 48px icon chip (pseudo-element) carries the pale lavender fill and,
   layered via a second background-image, the per-card SVG glyph. */
[data-testid="stMain"] [class*="st-key-sample_question_"] button::before {
    content: "";
    flex: 0 0 auto;
    width: 48px;
    height: 48px;
    border-radius: 12px;
    background-position: center;
    background-repeat: no-repeat;
    background-size: 24px 24px, 100% 100%;
}
[data-testid="stMain"] .st-key-sample_question_0 button::before {
    background-image: url("_CARD_ICON_BAR_URL"), linear-gradient(135deg, @SURFACE_ICON, #F2EDFF);
}
[data-testid="stMain"] .st-key-sample_question_1 button::before {
    background-image: url("_CARD_ICON_TAG_URL"), linear-gradient(135deg, @SURFACE_ICON, #F2EDFF);
}
[data-testid="stMain"] .st-key-sample_question_2 button::before {
    background-image: url("_CARD_ICON_USERS_URL"), linear-gradient(135deg, @SURFACE_ICON, #F2EDFF);
}
/* Far-right chevron: the quiet affordance that says "clickable". */
[data-testid="stMain"] [class*="st-key-sample_question_"] button::after {
    content: "";
    flex: 0 0 auto;
    width: 18px;
    height: 18px;
    margin-left: auto;
    opacity: 0.7;
    background: url("_CHEVRON_RIGHT_URL") center / contain no-repeat;
    transition: opacity 0.18s ease, transform 0.18s ease;
}
[data-testid="stMain"] [class*="st-key-sample_question_"] button:hover::after {
    opacity: 1;
    transform: translateX(2px);
}
[data-testid="stMain"] [class*="st-key-sample_question_"] button [data-testid="stMarkdownContainer"] {
    display: block;
    flex: 1 1 auto;
    min-width: 0;
    text-align: left;
}
[data-testid="stMain"] [class*="st-key-sample_question_"] button [data-testid="stMarkdownContainer"] p {
    margin: 0;
    font-size: clamp(0.94rem, 1vw, 1rem);
    font-weight: 600;
    color: #17233F;
    line-height: 1.45;
    white-space: normal;
    text-align: left;
}

/* ---------------------------------------------------------------------------
   6. "What can you ask?" capability card: one large premium white card holding
      two rows -- the intro plus four capability columns, then a dataset
      metadata row with a real (CSS-only, no extra Streamlit widget) "View
      details" disclosure. Everything is a grid, so it reflows by breakpoint
      instead of by pixel width.
   --------------------------------------------------------------------------- */
.sentrasql-capability {
    margin: 0 0 clamp(20px, 3vh, 38px);
    padding: clamp(20px, 2.1vw, 30px);
    background: rgba(255, 255, 255, 0.90);
    border: 1px solid @BORDER;
    border-radius: clamp(18px, 1.6vw, 24px);
    box-shadow: 0 10px 35px rgba(60, 80, 130, 0.06);
    text-align: left;
}
.sentrasql-capability__row {
    display: grid;
    grid-template-columns: 1.15fr repeat(4, minmax(0, 1fr));
    column-gap: clamp(14px, 1.7vw, 26px);
    row-gap: 20px;
    align-items: start;
}
.sentrasql-capability__intro h2 {
    font-family: @FONT;
    font-size: clamp(17px, 1.3vw, 19px);
    font-weight: 700;
    letter-spacing: -0.01em;
    color: @HEADING;
    margin: 0 0 8px;
}
.sentrasql-capability__intro p {
    font-size: 14px;
    line-height: 1.5;
    color: @TEXT_SECONDARY;
    margin: 0;
}
/* Subtle vertical separators between capability columns (the intro keeps no
   left border, so the divider set starts at the first capability). */
.sentrasql-capability__item {
    min-width: 0;
    padding-left: clamp(14px, 1.7vw, 26px);
    border-left: 1px solid @BORDER;
}
.sentrasql-capability__item h3 {
    font-size: clamp(15px, 1.05vw, 16px);
    font-weight: 700;
    color: @TEXT;
    margin: 0 0 6px;
}
.sentrasql-capability__item p {
    font-size: 14px;
    line-height: 1.5;
    color: @MUTED;
    margin: 0;
}
.sentrasql-capability__item svg {
    width: 27px;
    height: 27px;
    display: block;
    margin-bottom: 10px;
}

/* Row two: four metadata items then the "View details" control at the far
   right. One 5-column grid, so the button's right edge aligns with the card's
   padding without any absolute positioning. */
.sentrasql-capability__meta {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr)) auto;
    align-items: center;
    column-gap: clamp(14px, 1.7vw, 26px);
    row-gap: 16px;
    margin-top: clamp(16px, 2.2vh, 24px);
    padding-top: clamp(14px, 2vh, 22px);
    border-top: 1px solid @BORDER;
}
.sentrasql-meta {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    min-width: 0;
}
.sentrasql-meta svg { width: 18px; height: 18px; flex: 0 0 auto; margin-top: 2px; }
.sentrasql-meta__text { min-width: 0; }
.sentrasql-meta__label {
    display: block;
    font-size: 14px;
    font-weight: 700;
    color: @TEXT;
    line-height: 1.35;
}
.sentrasql-meta__value {
    display: block;
    font-size: 13.5px;
    font-weight: 400;
    color: @MUTED;
    line-height: 1.4;
}

/* "View details" is a native <details> element, so it is a real, keyboard
   accessible control that works with no JavaScript and without adding a
   Streamlit widget (which would change the page's widget contract).
   ``display: contents`` lets its <summary> and panel participate directly in
   the metadata grid: the summary stays in the last column of row two while the
   panel spans the full card width beneath it. */
.sentrasql-details { display: contents; }
.sentrasql-details > summary {
    grid-column: 5;
    justify-self: end;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    height: 40px;
    padding: 0 15px;
    border: 1px solid @BORDER_STRONG;
    border-radius: 11px;
    background: @SURFACE;
    color: @CONTROL_TEXT;
    font-size: 13.5px;
    font-weight: 600;
    line-height: 1;
    cursor: pointer;
    list-style: none;
    white-space: nowrap;
    transition: border-color 0.18s ease, color 0.18s ease, box-shadow 0.18s ease;
}
.sentrasql-details > summary::-webkit-details-marker { display: none; }
.sentrasql-details > summary:hover,
.sentrasql-details > summary:focus-visible {
    border-color: @ACCENT;
    color: @ACCENT;
    box-shadow: 0 6px 18px rgba(79, 107, 255, 0.14);
    outline: none;
}
.sentrasql-details > summary::after {
    content: "";
    width: 14px;
    height: 14px;
    flex: 0 0 auto;
    background: url("_CHEVRON_DOWN_URL") center / contain no-repeat;
    transition: transform 0.18s ease;
}
.sentrasql-details[open] > summary::after { transform: rotate(180deg); }
.sentrasql-details:not([open]) > .sentrasql-details__panel { display: none; }
.sentrasql-details__panel {
    grid-column: 1 / -1;
    margin-top: 16px;
    padding: clamp(16px, 1.6vw, 22px);
    background: rgba(238, 241, 255, 0.55);
    border: 1px solid @BORDER;
    border-radius: 14px;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    gap: 14px clamp(16px, 2vw, 28px);
}
/* Each scope fact is its own definition list (valid markup for a dt/dd pair)
   with the browser's default block margin removed. */
.sentrasql-details__entry { margin: 0; }
.sentrasql-details__entry dt {
    font-size: 12.5px;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: @TEXT_SECONDARY;
    margin: 0 0 3px;
}
.sentrasql-details__entry dd {
    margin: 0;
    font-size: 14px;
    line-height: 1.45;
    color: @TEXT;
}

/* Note: the landing page no longer renders a second, legacy dataset-scope
   accordion. Its scope and limitation facts were moved into the capability
   card's "View details" panel (see
   ``dashboard/components/capability_card.py``), so no ``stExpander`` rules are
   needed here any more -- the landing page's last block before the composer is
   the capability card itself. */

/* ---------------------------------------------------------------------------
   7. Chat transcript: user question bubbles, the in-transcript processing
      surface shown while a question runs, flat answers with an accent eyebrow,
      and disclosures as a muted sub-section under a thin divider.
   --------------------------------------------------------------------------- */

/* User question: right-aligned rounded bubble, soft lavender fill, dark text. */
.sentrasql-question-row { display: flex; justify-content: flex-end; margin: 14px 0 8px; }
.sentrasql-question {
    max-width: 78%;
    background: @BUBBLE;
    color: @TEXT;
    border-radius: 18px 18px 4px 18px;
    padding: 10px 16px;
    font-size: 0.97rem;
    line-height: 1.5;
    overflow-wrap: break-word;
    white-space: pre-wrap;
    box-shadow: 0 1px 1px rgba(16, 24, 40, 0.03);
}

/* In-flight exchange: the question is already in the transcript (bubble above),
   so the wait is shown where its answer will land -- a lightweight white
   response panel under the question, still ABOVE the pinned composer, never a
   status message below it. Deliberately not a bubble and not a composer:
   hairline border, soft shadow, 14px radius, and one small accent ring as the
   only motion (no large spinner, no overlay, no blocking UI). It shares the
   answer's bottom margin so the last line clears the pinned composer band the
   same way an answer does. */
.sentrasql-processing {
    margin: 4px 0 40px;
    padding: 14px 18px;
    background: rgba(255, 255, 255, 0.88);
    border: 1px solid @BORDER;
    border-radius: 14px;
    box-shadow: 0 6px 24px rgba(60, 80, 130, 0.06);
}
.sentrasql-processing__label {
    margin: 0 0 8px;
    font-family: @FONT;
    font-size: 14px;
    font-weight: 700;
    line-height: 1.35;
    color: @TEXT;
}
.sentrasql-processing__row { display: flex; align-items: flex-start; gap: 10px; }
/* The indicator: an 18px ring in the brand accent on a hairline track, spinning
   slowly. It is decorative (aria-hidden) -- the status line carries the
   meaning -- and is the only animation this surface adds. */
.sentrasql-processing__indicator {
    box-sizing: border-box;
    flex: 0 0 18px;
    width: 18px;
    height: 18px;
    margin-top: 2px;
    border: 2px solid @BORDER;
    border-top-color: @ACCENT;
    border-radius: 50%;
    animation: sentrasql-processing-spin 0.9s linear infinite;
}
.sentrasql-processing__text { min-width: 0; }
.sentrasql-processing__status {
    margin: 0;
    font-family: @FONT;
    font-size: 15px;
    font-weight: 400;
    line-height: 1.45;
    color: @TEXT;
}
.sentrasql-processing__detail {
    margin: 2px 0 0;
    font-family: @FONT;
    font-size: 12.5px;
    font-weight: 400;
    line-height: 1.45;
    color: @TEXT_SECONDARY;
}
@keyframes sentrasql-processing-spin {
    to { transform: rotate(360deg); }
}
/* A user who asked for reduced motion still gets the ring, just not the spin. */
@media (prefers-reduced-motion: reduce) {
    .sentrasql-processing__indicator { animation: none; }
}

/* Answer: flat, no bubble; eyebrow in accent, body clean text on the canvas.
   The generous bottom margin guarantees the final line always clears the
   pinned composer band at the end of the scroll. */
.sentrasql-answer { margin: 4px 0 40px; padding-left: 2px; }
.sentrasql-answer__eyebrow {
    font-size: 0.66rem;
    font-weight: 800;
    letter-spacing: 0.12em;
    color: @ACCENT;
    margin: 0 0 6px;
}
.sentrasql-answer__eyebrow--error { color: @ERROR; }
.sentrasql-answer__body {
    color: @TEXT;
    font-size: 0.99rem;
    line-height: 1.65;
    white-space: pre-wrap;
    overflow-wrap: break-word;
}

/* Disclosures: visually distinct, slightly muted, under a thin divider. */
.sentrasql-disclosures {
    margin-top: 14px;
    padding-top: 12px;
    border-top: 1px solid @BORDER;
}
.sentrasql-disclosures__eyebrow {
    font-size: 0.62rem;
    font-weight: 800;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: @MUTED;
    margin: 0 0 6px;
}
.sentrasql-disclosures__body {
    color: @MUTED;
    font-size: 0.87rem;
    line-height: 1.55;
    white-space: pre-wrap;
    overflow-wrap: break-word;
}

/* Empty-state hint before any question has run. */
.sentrasql-hint { color: @MUTED; font-size: 0.92rem; padding-left: 2px; }

/* ---------------------------------------------------------------------------
   8. Sidebar: brand logo, two-line tagline, and the single "New Chat" action
      on a deep-navy surface. NO username, NO profile, NO database status, NO
      footer -- the sidebar is intentionally just brand + one action.
   --------------------------------------------------------------------------- */

/* Streamlit 1.63's real sidebar node is ``[data-testid="stSidebar"]``. The width
   is fluid (``clamp(240px, 18vw, 280px)``) rather than a fixed 280px, so wider
   monitors get the same proportions instead of a relatively tiny rail, and
   smaller laptops still get a comfortably readable 240px. ``!important`` is
   needed to beat the framework's inline resizable width; the framework's own
   min/max-width are left intact so collapse still works. */
[data-testid="stSidebar"] {
    width: clamp(240px, 18vw, 280px) !important;
    --sentrasql-sidebar-gutter: clamp(20px, 2.1vw, 30px);
    background: linear-gradient(180deg, @NAVY_900 0%, @NAVY_950 100%) !important;
    border-right: none;
}

/* Drop the framework's own scrollbar-gutter-aware sidebar content padding so
   the single design-system gutter below is exact rather than stacked on top of
   it. Every sidebar row then applies --sentrasql-sidebar-gutter itself, which
   is what keeps the logo, tagline, and New Chat button mutually aligned. */
[data-testid="stSidebarContent"] { padding: 0 !important; }

.sentrasql-sidebar-logo {
    padding: clamp(22px, 3.6vh, 34px) var(--sentrasql-sidebar-gutter) 0;
}
.sentrasql-sidebar-logo svg {
    display: block;
    width: 100%;
    max-width: 176px;
    height: auto;
}

.sentrasql-sidebar-tagline {
    padding: 0 var(--sentrasql-sidebar-gutter);
    margin: clamp(14px, 2.2vh, 20px) 0 0;
    font-size: clamp(14px, 1.02vw, 15.5px);
    line-height: 1.5;
    font-weight: 400;
    color: rgba(244, 247, 255, 0.74);
}

/* New Chat: the sidebar's only action. The button container carries the same
   gutter as the logo and tagline and the button is simply ``width: 100%`` of
   that already-inset box with ``box-sizing: border-box`` -- so its left and
   right edges always line up with the brand text above it and it can never
   overflow or extend past the sidebar, at any viewport width. */
[data-testid="stSidebar"] .st-key-new_chat_button {
    box-sizing: border-box;
    width: 100%;
    margin: clamp(18px, 3vh, 30px) 0 0;
    padding-inline: var(--sentrasql-sidebar-gutter);
}
[data-testid="stSidebar"] .st-key-new_chat_button button {
    box-sizing: border-box;
    width: 100%;
    height: clamp(50px, 6.4vh, 56px);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    padding: 0 18px;
    border: none;
    border-radius: 13px;
    font-family: @FONT;
    font-size: clamp(15px, 1vw, 16px);
    font-weight: 600;
    color: #FFFFFF;
    background: linear-gradient(135deg, @ACCENT 0%, @ACCENT_HOVER 100%);
    box-shadow: 0 8px 24px rgba(69, 83, 255, 0.28);
    transition: transform 0.16s ease, box-shadow 0.16s ease, filter 0.16s ease;
    overflow: hidden;
}
[data-testid="stSidebar"] .st-key-new_chat_button button:hover,
[data-testid="stSidebar"] .st-key-new_chat_button button:focus-visible {
    background: linear-gradient(135deg, @ACCENT 0%, @ACCENT_HOVER 100%);
    border: none;
    transform: translateY(-1px);
    filter: brightness(1.05);
    box-shadow: 0 12px 32px rgba(69, 83, 255, 0.42);
}
[data-testid="stSidebar"] .st-key-new_chat_button button:active { transform: translateY(0); }
[data-testid="stSidebar"] .st-key-new_chat_button button p {
    margin: 0;
    font-size: inherit;
    font-weight: inherit;
    color: inherit;
    white-space: nowrap;
}
/* The leading plus glyph, drawn as a 22px white outline icon. */
[data-testid="stSidebar"] .st-key-new_chat_button button::before {
    content: "";
    flex: 0 0 auto;
    width: 22px;
    height: 22px;
    background: url("_PLUS_ICON_URL") center / contain no-repeat;
}

/* ---------------------------------------------------------------------------
   9. Responsive behavior. Desktop (>=1200px) is the default composition above;
      these two breakpoints reorganize the same markup rather than duplicating
      it, so no rule here depends on a particular screen resolution.
   --------------------------------------------------------------------------- */

/* Medium desktop / tablet: the sidebar stays usable (fluid width above), the
   hero scales through its clamp(), the example cards become two per row, and
   the capability card reorganizes into two columns with the separators dropped
   (a wrapped two-column grid has no clean vertical separator lines). */
@media (max-width: 1199.98px) {
    [data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-sample_question_0) > [data-testid="stColumn"] {
        flex: 1 1 calc(50% - 12px) !important;
    }
    .sentrasql-capability__row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .sentrasql-capability__intro { grid-column: 1 / -1; }
    .sentrasql-capability__item { padding-left: 0; border-left: none; }
    .sentrasql-capability__meta { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .sentrasql-details > summary { grid-column: 1 / -1; }
}

/* Tablet tier for the composer: tighter left gutter, smaller send button. */
@media (max-width: 1023.98px) {
    [data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-question_input) {
        padding: 10px 10px 10px 22px;
        gap: 12px;
    }
    [data-testid="stMain"] .st-key-ask_button {
        flex: 0 0 50px !important;
        width: 50px !important;
        min-width: 50px !important;
    }
    [data-testid="stMain"] .st-key-ask_button button {
        width: 50px;
        height: 50px;
        min-width: 50px;
        min-height: 50px;
        flex: 0 0 50px;
    }
}

/* Small screens: the desktop composition is abandoned on purpose. The sidebar
   becomes the framework's compact overlay (opened from the top strip, which is
   deliberately left visible here), everything stacks to a single column, and
   the composer keeps only its natural margins. */
@media (max-width: 767.98px) {
    [data-testid="stSidebar"] { width: min(86vw, 300px) !important; }
    /* Only the sidebar toggle is needed from the header strip; the brand lives
       in the sidebar, so the strip itself stays chrome-free. */
    [data-testid="stHeader"] {
        display: flex !important;
        background: transparent !important;
        box-shadow: none !important;
    }
    [data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-sample_question_0) > [data-testid="stColumn"] {
        flex: 1 1 100% !important;
    }
    .sentrasql-welcome { padding: clamp(18px, 4vh, 32px) 0 clamp(20px, 3vh, 28px); }
    .sentrasql-welcome__eyebrow { letter-spacing: 0.2em; }
    .sentrasql-welcome__title { font-size: clamp(30px, 8.6vw, 42px); }
    .sentrasql-welcome__tagline { font-size: clamp(15px, 3.6vw, 17px); }
    .sentrasql-capability__row { grid-template-columns: minmax(0, 1fr); row-gap: 18px; }
    .sentrasql-capability__meta { grid-template-columns: minmax(0, 1fr); }
    .sentrasql-details > summary { grid-column: 1 / -1; justify-self: stretch; }
    .sentrasql-details__panel { grid-template-columns: minmax(0, 1fr); }
    [data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-question_input) {
        min-height: 68px;
        padding: 9px 8px 9px 18px;
        gap: 10px;
        border-radius: 28px;
    }
    [data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.st-key-question_input) > [data-testid="stColumn"]:first-child input {
        font-size: 15px;
    }
    [data-testid="stMain"] .st-key-ask_button {
        flex: 0 0 48px !important;
        width: 48px !important;
        min-width: 48px !important;
    }
    [data-testid="stMain"] .st-key-ask_button button {
        width: 48px;
        height: 48px;
        min-width: 48px;
        min-height: 48px;
        flex: 0 0 48px;
    }
    [data-testid="stMain"] .sentrasql-composer-hint { font-size: 11.5px; }
    .sentrasql-question { max-width: 92%; }
}

/* Short desktop viewports (e.g. a 768px-tall laptop with browser chrome): trim
   the landing page's breathing room so the composer and the whole capability
   card still fit without meaningless scrolling. */
@media (max-height: 820px) and (min-width: 768px) {
    .sentrasql-welcome { padding-top: clamp(20px, 3.4vh, 38px); }
    [data-testid="stMain"] [data-testid="stLayoutWrapper"]:has(.st-key-sample_question_0) {
        margin-bottom: clamp(16px, 2.4vh, 24px);
    }
}
</style>"""

# Icon URIs are substituted into their placeholders first (they contain no
# tokens, and encoding keeps them free of ``@``, so they can never be mistaken
# for a token).
_PAGE_CSS = _PAGE_CSS_TEMPLATE.replace("_SEND_ICON_URL", _SEND_ICON_SVG)
_PAGE_CSS = _PAGE_CSS.replace("_CARD_ICON_BAR_URL", _CARD_ICON_BAR)
_PAGE_CSS = _PAGE_CSS.replace("_CARD_ICON_TAG_URL", _CARD_ICON_TAG)
_PAGE_CSS = _PAGE_CSS.replace("_CARD_ICON_USERS_URL", _CARD_ICON_USERS)
_PAGE_CSS = _PAGE_CSS.replace("_PLUS_ICON_URL", _PLUS_ICON_SVG)
_PAGE_CSS = _PAGE_CSS.replace("_CHEVRON_RIGHT_URL", _CHEVRON_RIGHT_SVG)
_PAGE_CSS = _PAGE_CSS.replace("_CHEVRON_DOWN_URL", _CHEVRON_DOWN_SVG)

# Colour/font tokens are substituted longest-name-first, so a token that is a
# prefix of another (``@TEXT`` inside ``@TEXT_SECONDARY``, ``@ACCENT`` inside
# ``@ACCENT_HOVER``) can never be partially replaced.
for _token, _value in sorted(
    _CSS_TOKENS.items(), key=lambda item: len(item[0]), reverse=True
):
    _PAGE_CSS = _PAGE_CSS.replace(f"@{_token}", _value)

# Final global stylesheet ready for ``st.markdown(..., unsafe_allow_html=True)``.
PAGE_CSS = _PAGE_CSS


