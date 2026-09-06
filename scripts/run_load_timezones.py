"""Populate the ``country_timezones`` table from pycountry/pytz and verify it.

Pipeline:

1. Derive the actual country names from the **raw dataset** (the source of
   truth) by reusing ``profile_dataset.find_dataset_file`` and
   ``profile_dataset.load_dataset``. This was chosen over parsing
   ``reports/data_profile.md`` because that report is a *generated* artifact
   and can drift from the raw data; reading the ``Country`` column directly
   guarantees the map covers the dataset's current real values and reuses the
   existing loading helpers (no duplicated file detection / encoding logic).
   The derived count is validated to be exactly 43 before mapping proceeds.
2. Call ``db.timezones.build_country_timezone_map`` (pycountry exact lookup ->
   documented dataset aliases -> pycountry fuzzy search -> UTC fallback, with
   pytz supplying each resolved country's primary IANA timezone).
3. Print a resolution report: which names resolved to a real country/timezone
   versus which fell back to UTC.
4. Insert the mapping into the ``country_timezones`` table of
   ``data/processed/sentrasql.db`` using the same parameterized-insert /
   single-transaction / no-swallowed-errors pattern as ``db.load``, explicitly
   verifying (before binding) that no numpy scalar types leaked into the values
   and coercing any that did to plain Python ``str``.
5. Run and print ``SELECT COUNT(*)`` and ``SELECT * ... ORDER BY country``.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

# Make the project root importable so the ``db`` package resolves, and the
# scripts directory importable so the sibling profile modules resolve. This
# mirrors the sys.path handling used by the other run_* scripts.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_DIR = Path(__file__).resolve().parent
for _path in (str(_PROJECT_ROOT), str(_SCRIPT_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import numpy as np  # noqa: E402

from db.timezones import build_country_timezone_map  # noqa: E402
from profile_dataset import find_dataset_file, load_dataset  # noqa: E402

_PROCESSED_DB = _PROJECT_ROOT / "data" / "processed" / "sentrasql.db"
_EXPECTED_COUNTRY_COUNT = 43  # profiled in reports/data_profile.md

# country_timezones columns (db/schema.sql) and the parameterized INSERT,
# mirroring db/load.py's structure exactly.
_COUNTRY_TIMEZONE_COLUMNS = ("country", "timezone")
_PLACEHOLDERS = ", ".join("?" for _ in _COUNTRY_TIMEZONE_COLUMNS)
_INSERT_SQL = (
    "INSERT INTO country_timezones "
    f"({', '.join(_COUNTRY_TIMEZONE_COLUMNS)}) VALUES ({_PLACEHOLDERS})"
)


def _as_plain_str(value: object) -> str:
    """Return ``value`` as a plain Python ``str``.

    This mirrors the numpy-scalar fix required in ``db/load.py``: CPython's
    ``sqlite3`` binds some numpy scalar types as BLOBs rather than as their
    logical SQL type, so every value is verified to be an exact Python ``str``
    before binding. Numpy string scalars (``numpy.str_``) are coerced with
    ``str()``; anything else non-string raises ``TypeError``.
    """
    if type(value) is str:
        return value
    if isinstance(value, (str, np.str_)):
        return str(value)
    raise TypeError(
        f"Expected a str value to bind to country_timezones, got "
        f"{type(value).__name__}: {value!r}"
    )


def _derive_country_names() -> tuple[Path, list[str]]:
    """Return ``(dataset_path, sorted_unique_country_names)`` from the raw CSV."""
    dataset_path = find_dataset_file()
    raw_df, _ = load_dataset(dataset_path)
    names = sorted(
        {str(value).strip() for value in raw_df["Country"].dropna().unique().tolist()}
    )
    if len(names) != _EXPECTED_COUNTRY_COUNT:
        raise RuntimeError(
            f"Expected {_EXPECTED_COUNTRY_COUNT} unique Country values in the raw "
            f"dataset (as profiled in reports/data_profile.md) but found "
            f"{len(names)}: {names}"
        )
    return dataset_path, names


def _print_resolution_report(country_names: list[str], mapping: dict[str, str]) -> None:
    """Print which names resolved to a country/timezone and which fell back."""
    resolved = [name for name in country_names if mapping[name] != "UTC"]
    fell_back = [name for name in country_names if mapping[name] == "UTC"]

    print("=== Country-name resolution report ===")
    print(f"Resolved to a country + IANA timezone ({len(resolved)}):")
    for index, name in enumerate(resolved, start=1):
        print(f"  {index:>2}. {name}")
    print(f"Fell back to UTC ({len(fell_back)}):")
    for index, name in enumerate(fell_back, start=1):
        print(f"  {index:>2}. {name}")
    print()


def _verification_queries(db_path: Path) -> None:
    """Run and print the two post-load verification queries."""
    conn = sqlite3.connect(str(db_path))
    try:
        print("=== Verification 1: row count in country_timezones ===")
        print("SQL: SELECT COUNT(*) FROM country_timezones")
        total = conn.execute("SELECT COUNT(*) FROM country_timezones").fetchone()[0]
        print(f"  {total}")
        print()

        print("=== Verification 2: full contents (ORDER BY country) ===")
        print("SQL: SELECT * FROM country_timezones ORDER BY country")
        rows = conn.execute(
            "SELECT * FROM country_timezones ORDER BY country"
        ).fetchall()
        for country, timezone in rows:
            print(f"  {country} = {timezone}")
    finally:
        conn.close()


def _main() -> None:
    args = sys.argv[1:]
    db_path = Path(args[0]) if args else _PROCESSED_DB

    dataset_path, country_names = _derive_country_names()
    print(f"Dataset file: {dataset_path.name}")
    print(
        f"Country names derived from raw dataset (Country column): "
        f"{len(country_names)}"
    )
    print()

    mapping = build_country_timezone_map(country_names)
    _print_resolution_report(country_names, mapping)

    # Explicit numpy-scalar verification before binding (see _as_plain_str).
    raw_items = list(mapping.items())
    non_plain_values = [
        (value, type(value).__name__)
        for _country, timezone in raw_items
        for value in (_country, timezone)
        if type(value) is not str
    ]
    rows = [
        (_as_plain_str(country), _as_plain_str(timezone))
        for country, timezone in raw_items
    ]
    all_plain = all(type(value) is str for row in rows for value in row)
    print("=== Numpy-scalar check on values to bind ===")
    print(f"  values checked: {len(raw_items) * 2}")
    print(f"  non-plain-str values found (coerced): {len(non_plain_values)}")
    if non_plain_values:
        print(f"  coerced: {non_plain_values}")
    print(f"  all values plain str after coercion: {all_plain}")
    print()

    print(f"Target database: {db_path}")
    print("Inserting country_timezones rows (single transaction) ...")
    conn = sqlite3.connect(str(db_path))
    try:
        # ``with conn`` commits on success and rolls back on any exception,
        # so either every row is persisted or none are.
        with conn:
            conn.executemany(_INSERT_SQL, rows)
    finally:
        conn.close()
    print(f"Insert complete: {len(rows)} rows inserted.")
    print()
    _verification_queries(db_path)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    _main()

