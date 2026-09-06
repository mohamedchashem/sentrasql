"""Build a country -> IANA-timezone map for the dataset's ``Country`` values.

This module resolves the ``Country`` column values found in the raw retail
dataset (43 distinct values, see ``reports/data_profile.md``) against real ISO
3166 data using :mod:`pycountry`, then picks each resolved country's primary
timezone from :mod:`pytz`. It is the data source that backs the
``country_timezones`` table (see ``db/schema.sql`` and the timezone-handling
requirement in ``DESIGN_LOG.md`` section 1.6).

Resolution algorithm, per input name (in order):

1. **Exact pycountry match** -- case-insensitive comparison against every
   country's ``name``, ``common_name``, ``official_name``, ``alpha_2`` and
   ``alpha_3``. This resolves 36 of the 43 names, including the ones that are
   not the country's plain ISO name: ``USA`` (via the ``alpha_3`` code) and
   ``Czech Republic`` (via ``official_name``).
2. **Dataset alias** -- if exact matching fails, ``_DATASET_COUNTRY_ALIASES``
   maps the two labels this dataset uses that pycountry cannot resolve to the
   correct sovereign state: ``EIRE`` (the dataset's name for Ireland) and
   ``Korea`` (the dataset means the Republic of Korea; pycountry's fuzzy search
   resolves ``Korea`` to ``KP`` -- North Korea -- which would be wrong).
3. **pycountry fuzzy search** -- ``pycountry.countries.search_fuzzy`` catches
   abbreviations pycountry does know about, e.g. ``RSA`` resolves to South
   Africa (``ZA``).
4. **UTC fallback** -- names that still cannot be resolved are mapped to
   ``UTC`` and logged with ``logger.warning``. For this dataset those are
   exactly the four ambiguous/non-country values ``Channel Islands``,
   ``European Community``, ``Unspecified`` and ``West Indies``.

The "primary" timezone for a resolved country is the first entry of
``pytz.country_timezones[country.alpha_2]`` -- i.e. pytz's IANA zone.tab
ordering for that country (the load-layer contract for this table). Timezone
values therefore come from pytz, never from a hardcoded per-country list.
"""

from __future__ import annotations

import logging

import pycountry
import pytz

logger = logging.getLogger(__name__)

# Dataset-specific Country labels that pycountry cannot resolve to the correct
# country by itself. Keys are the exact dataset values (compared
# case-insensitively); values are ISO 3166-1 alpha-2 codes.
#   * "EIRE"  -- the dataset's name for Ireland; pycountry has no such name and
#     its fuzzy search raises LookupError for it.
#   * "Korea" -- this UK retail dataset means the Republic of (South) Korea;
#     pycountry's fuzzy search resolves "Korea" to KP (North Korea) first.
_DATASET_COUNTRY_ALIASES = {
    "EIRE": "IE",
    "Korea": "KR",
}


def _exact_match(name: str):
    """Return the pycountry country whose name/official/common name or ISO code
    equals ``name`` (case-insensitive), or ``None``."""
    lowered = name.lower()
    for country in pycountry.countries:
        for attribute in ("name", "common_name", "official_name", "alpha_2", "alpha_3"):
            value = getattr(country, attribute, None)
            if value is not None and str(value).lower() == lowered:
                return country
    return None


def _resolve_country(name: str):
    """Resolve ``name`` to a pycountry country and the method used.

    Returns ``(country, method)`` where ``method`` is one of ``"exact"``,
    ``"dataset alias"`` or ``"pycountry fuzzy"``; returns ``(None, None)`` when
    the name cannot be resolved at all.
    """
    country = _exact_match(name)
    if country is not None:
        return country, "exact"

    for alias_name, alpha_2 in _DATASET_COUNTRY_ALIASES.items():
        if name.lower() == alias_name.lower():
            country = pycountry.countries.get(alpha_2=alpha_2)
            if country is not None:
                return country, "dataset alias"

    try:
        fuzzy_matches = pycountry.countries.search_fuzzy(name)
    except LookupError:
        return None, None
    if fuzzy_matches:
        return fuzzy_matches[0], "pycountry fuzzy"
    return None, None


def build_country_timezone_map(country_names: list[str]) -> dict[str, str]:
    """Map each country name in ``country_names`` to an IANA timezone string.

    Every name is resolved with the four-step algorithm in the module
    docstring. Resolved countries map to ``pytz.country_timezones``' first
    entry for their ISO alpha-2 code; names that cannot be resolved at all map
    to ``"UTC"`` and are logged as fallbacks. The returned dict has one entry
    per input name (duplicate/blank inputs collapse to the last value seen).
    """
    result: dict[str, str] = {}
    for raw_name in country_names:
        name = str(raw_name).strip()
        if not name:
            logger.warning("Skipping blank country name (input value %r).", raw_name)
            continue

        country, method = _resolve_country(name)
        if country is None:
            logger.warning(
                "Country %r could not be resolved with pycountry; mapping to UTC.",
                name,
            )
            result[name] = "UTC"
            continue

        try:
            timezones = pytz.country_timezones[country.alpha_2]
        except KeyError as exc:  # pragma: no cover - every ISO country used here exists in pytz
            raise RuntimeError(
                f"Resolved {name!r} to {country.alpha_2} but pytz has no timezone "
                "entry for that country code."
            ) from exc
        if not timezones:  # pragma: no cover - pytz lists are never empty
            raise RuntimeError(
                f"Resolved {name!r} to {country.alpha_2} but pytz returned an "
                "empty timezone list."
            )

        result[name] = timezones[0]
        logger.debug(
            "Mapped %r -> %s (%s, alpha_2=%s).",
            name,
            timezones[0],
            method,
            country.alpha_2,
        )
    return result
