"""Load the raw dataset into the ``transactions`` table and verify it.

Pipeline: locate + load the raw dataset exactly like
``scripts/profile_dataset.py`` (reusing its ``find_dataset_file`` and
``load_dataset`` so file detection and the UTF-8/latin1 encoding fallback stay
in one place -- no duplicated loading logic), run every raw row through
``db.transform.transform_raw_data``, persist the schema-ready result to the
SQLite database at ``data/processed/sentrasql.db`` (or the database path given
as the first CLI argument) with ``db.load.load_transactions``, and then run and
print three verification queries against the now-populated database:

1. total row count in ``transactions``,
2. ``line_item_type`` value counts via ``GROUP BY``,
3. one full sample row via ``SELECT * FROM transactions LIMIT 1``.

The whole insert happens inside a single transaction (all-or-nothing), so a
failed load leaves the target database untouched.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# Make the project root importable so the ``db`` package resolves, and the
# scripts directory importable so the sibling profile modules resolve. This
# mirrors the sys.path handling used by scripts/run_transform_check.py.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_DIR = Path(__file__).resolve().parent
for _path in (str(_PROJECT_ROOT), str(_SCRIPT_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from db.load import load_transactions  # noqa: E402
from db.transform import transform_raw_data  # noqa: E402
from profile_dataset import find_dataset_file, load_dataset  # noqa: E402

_PROCESSED_DB = _PROJECT_ROOT / "data" / "processed" / "sentrasql.db"


def _print_verification_queries(db_path: Path) -> None:
    """Run and print the three post-load verification queries."""
    conn = sqlite3.connect(str(db_path))
    try:
        print("=== Verification 1: total row count in transactions ===")
        print("SQL: SELECT COUNT(*) FROM transactions")
        total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        print(f"  {total:,}")
        print()

        print("=== Verification 2: line_item_type value counts ===")
        print(
            "SQL: SELECT line_item_type, COUNT(*) FROM transactions "
            "GROUP BY line_item_type"
        )
        count_rows = conn.execute(
            "SELECT line_item_type, COUNT(*) FROM transactions "
            "GROUP BY line_item_type"
        ).fetchall()
        for line_item_type, count in count_rows:
            print(f"  {line_item_type:<12} {count:>12,}")
        print()

        print("=== Verification 3: one full sample row ===")
        print("SQL: SELECT * FROM transactions LIMIT 1")
        cursor = conn.execute("SELECT * FROM transactions LIMIT 1")
        columns = [description[0] for description in cursor.description]
        sample_row = cursor.fetchone()
        for column, value in zip(columns, sample_row):
            rendered = "NULL" if value is None else str(value)
            print(f"  {column} = {rendered}")
    finally:
        conn.close()


def _main() -> None:
    args = sys.argv[1:]
    db_path = Path(args[0]) if args else _PROCESSED_DB

    dataset_path = find_dataset_file()
    raw_df, _ = load_dataset(dataset_path)
    transformed = transform_raw_data(raw_df)

    print(f"Dataset file: {dataset_path.name}")
    print(
        f"Transformed rows: {transformed.shape[0]:,} rows x "
        f"{transformed.shape[1]} columns"
    )
    print(f"Target database: {db_path}")
    print("Loading transactions (single transaction) ...")
    load_transactions(db_path, transformed)
    print(f"Load complete: {transformed.shape[0]:,} rows inserted.")
    print()
    _print_verification_queries(db_path)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _main()
